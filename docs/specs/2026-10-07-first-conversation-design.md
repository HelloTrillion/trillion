# First conversation: design

**Date:** 2026-10-07 · **Tracks:** D (brain, persona), E (web app), the network half of C · **Status:** built

## Goal

Type to her in a browser, with the Galaxy behind, and get an answer from a real model. Ask her to write a file and approve it on a card. This is the first slice that joins the three pillars: the Galaxy shows her state, the brain talks, and every tool call goes through the spine.

It reaches rung 1 of the capability ladder (PRD §4): a typed conversation with any model, including a free local one.

## What is in

| Area | Requirement IDs | Where |
|---|---|---|
| Model provider interface, Anthropic and OpenAI-compatible adapters | BRN-1, BRN-2 | `src/trillion/brain/` |
| The tool loop, bounded by the loop guard | BRN-3, SPN-7 | `brain/loop.py` |
| Token usage per turn | BRN-4 (recording only) | `usage` table |
| Prompt assembly: cached stable block, volatile context on the user turn | PER-1, PER-1a, PER-2 (read side), PER-5 | `src/trillion/persona/` |
| Loopback server, session token, origin and host checks | SPN-11, SPN-11a | `src/trillion/api/app.py` |
| The realtime protocol, text half | §8.3 | `api/session.py` |
| Web app: Galaxy, conversation, confirmation card, live region | SUR-1 (part), SUR-6 (part), SPN-14, SPN-17 delivery | `apps/web/` |
| `trillion run` | ONB-2 (part) | `cli/main.py` |

## What is deliberately out

- **The rest of the secrets track.** The keychain store and in-app key entry are in (see below). The encrypted-file fallback (SEC-2), `doctor --secrets` (SEC-9) and the cost disclosure before a key is entered (ONB-10) are not.
- **Voice, memory, compaction, spend ceilings, onboarding, the desktop shell.** Their tracks.
- **`web_fetch` and egress rules (SPN-11b).** No tool in this slice touches the network.
- **Pricing for most models.** Anthropic's current published rates are built in for four models, dated in the source. Any other model's cost is recorded as unknown (`NULL`), never as zero.

## Decisions

1. **The loop never calls a tool.** It calls `Router.dispatch`, which is `Router.execute` under a second name. The structural test keeps its strictest form: outside `spine/router.py`, the source never says `.execute`.
2. **A tap approves its own card at once, and gives the model nothing.** SPN-2e forbids confirming in the turn that proposed the action. A tap is judged as the turn after the newest thing said, so the user can approve the moment the card appears. It does not advance the turn the model acts in, and that turn is fixed when the model's turn starts. *(Corrected 2026-10-07: the first version advanced a shared counter on every tap, and an independent review showed that tapping one card mid-turn let the model confirm another in the same turn.)*
3. **The host tells her what a tap did.** After a tap, the session adds a `[host]` line carrying the router's recorded response and lets her answer. She reports the outcome from the record, not from a guess (PER-10).
4. **History stays valid when a turn is cut short.** Every message is saved as it completes. An interrupt keeps the text already shown, marked `[interrupted]`, and drops a half-formed tool call. Tool calls already handed to the spine finish and are recorded (VOI-15c). Every tool call gets a result, even when a limit stops the turn.
5. **The token rides in the URL fragment.** `trillion run` prints and opens `http://127.0.0.1:7777/#token=…`. A browser never sends the fragment to a server or another site, and the page removes it on load and keeps it in `sessionStorage`. The token is new at every launch and lives only in memory; persisting it in the keychain waits for the secrets store.
6. **What an unauthenticated client can do:** fetch the static UI shell, which holds no user data, and open a socket that says `unauthenticated` and closes. Nothing else. Tested for a wrong token, a missing token, a non-hello first message, a foreign origin and a rebound hostname.
7. **Conversations use their own database connection.** The spine store holds explicit transactions on its connection; a message must never ride inside one.
8. **Reads run in parallel, everything else in order.** BRN-3 asks for parallel tool calls; a round that proposes or writes keeps the order the model asked for.
9. **Nothing the model or a tool wrote is parsed as markup.** The web app sets `textContent` only, and the server sends a strict Content-Security-Policy.
10. **The web app uses the Galaxy from source,** so it builds without building the package first.

## Choosing a model and entering a key in the app

A novice should never have to edit a file to add a key. On first run the app opens a panel: Anthropic, OpenAI, a model on this computer, or another OpenAI-compatible service.

- **The key goes to the OS keychain** (SEC-1) through `keyring`: macOS Keychain, Windows Credential Manager, Secret Service on Linux. It is never written to the database, a file or a log, and never returned to the page, which learns only that a key exists and its last four characters (SEC-4, SEC-7).
- **It is checked before it is saved,** with the provider's model-list call, which costs nothing. A typo is caught on the spot and a rejected key is not stored.
- **The settings API is a privileged path** (`/api/settings`), not a tool. It needs the session token in a header, a JSON body, and the app's own origin. The model cannot reach it.
- **A key is only sent over HTTPS, or to this machine.** A "local" model must be on a loopback address; OpenAI's address cannot be overridden.
- **Environment variables still win** when set, and then the panel is read-only (SEC-3).
- **With no keychain** (a headless Linux box), a key is refused rather than written to disk. A local model still works. SEC-2 will lift this.
- **A log filter scrubs keys** by shape and by the exact values in use (SEC-5).

## What the current Anthropic API requires

The first version of the Anthropic adapter was hand-written HTTP and would have failed against current models. It is now built on the official SDK, and three rules shape the design:

- **Assistant turns are sent back exactly as they came.** A reply can carry signed reasoning that is valid only if returned unchanged. The full turn is stored beside the neutral blocks and replayed to the same provider.
- **History is append-only.** The time and open cards used to be added to the newest user message and removed on the next request. Editing an earlier turn invalidates the model's reasoning, and is an error on newer accounts. The context is now saved with the turn it belongs to and replayed byte for byte, which also keeps the prompt cache warm.
- **If the system prompt, tool set or model changes** (a restart with different shared folders, an upgrade), earlier reasoning is no longer valid. Those turns are replayed as plain text and tool calls instead; nothing is sent that would be rejected.

Deliberately not sent: `thinking`, effort and sampling parameters (each model runs at its default, and the request is valid on every current model); eager tool-input streaming (a cut-off write is worse than a slower one); and the server-side refusal fallback (it is a beta that could not be tested without a key; a refusal is reported plainly instead).

## Protocol additions to §8.3

- Client → server: `hello` takes `resume` (a conversation id); `confirm.shown{pending_id}` records delivery when a card is really on screen (SPN-17).
- Server → client: `model{model}` (the model changed in settings), `history{messages}`, `confirm.resolved{pending_id, status, reason}`, `error{code, kind?}`, `notice{code, reason}`. `confirm.request` gains `tap_only`.

## Known gaps

- A page reload resumes the conversation and its open cards; closing the tab forgets the conversation id, so a new tab starts fresh. A conversation list is later work.
- The built UI is not yet inside the Python wheel. From a checkout, build it with `pnpm --filter @hellotrillion/web build`; the server finds `apps/web/dist`.
- Small local models sometimes reply with nothing. The app says so instead of showing silence.
