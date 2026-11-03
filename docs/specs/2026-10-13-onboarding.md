# Onboarding: she learns your name and you choose hers

**Date:** 2026-10-13 · **Requirements:** ONB-3 (first part), ONB-9, PER-1a, PER-2, PER-6 · **Phase 0 item:** 3

On a first run she says who she is, asks what to call you, asks what you want to call her, and asks how blunt you want her. The app writes `USER.md` and `IDENTITY.md`, and from the next turn she answers to the name you gave. This is the first slice of onboarding. Walking the capability ladder (model, voice, listening) and demonstrating the confirmation rule are the next one.

## The conversation

It happens in the transcript, typed, in three questions:

1. **Your name.** "I'm Sam." and "call me Sam" both mean Sam.
2. **Her name.** Keep the one she has (one click, or "keep it") or type a new one.
3. **Tone.** Blunt, balanced or gentler, as buttons or typed.

Then the app saves, and she says what was saved, the timezone the browser reported, and what comes next. **Skip for now** is offered at every step (ONB-9). **Names** in Settings changes them later, as a form or, with "Change them by talking", as the same three questions starting from the current answers.

**Her lines here are authored, not composed.** On a first run there may be no model to compose anything, and the names must not be written by her in any case. The script lives in `apps/web/src/onboarding.ts` as a pure state machine. It uses the name she currently has and never a hardcoded one, and her last line says the script is over.

Onboarding starts only in an empty conversation, on the first `state` after a `session` message that says `onboarding: "due"`. On a first run with no model it comes before the model dialog, which opens when the introduction ends or is skipped.

## The privileged setup path (PER-2)

`trillion.persona.setup` is the only code that writes the identity files. It is reached through `PUT /api/settings/persona`, which needs the session token and the app's own origin like the rest of Settings. No tool reaches it, and the deny floor refuses any tool that names the persona folder.

| Endpoint | What it does |
|---|---|
| `GET /api/settings/persona` | Current names, timezone, directness, and `onboarding`: `due`, `skipped` or `done`. |
| `PUT /api/settings/persona` | Validates, writes both files, adopts them, and sends `identity` to every open page. |
| `POST /api/settings/persona/skip` | Remembers that the user put it off. Does nothing once they are introduced. |

Each file is written to a temporary file and renamed into place, so it is all of the new text or all of the old.

```
---
user_name: Sam
timezone: America/Chicago
directness: blunt
---
```

## What a name may be

A name goes into every prompt and into a `key: value` line, so it is one line of at most 40 characters, with no braces or angle brackets and no leading quote, dash or `#`. Runs of whitespace, including line breaks, collapse to one space before anything else, so a pasted second line cannot add a field. A refusal returns a code (`name_empty`, `name_too_long`, `name_not_allowed`, `timezone_not_allowed`, `timezone_unknown`) and writes nothing. A timezone must be a name in the IANA list (`zoneinfo`; on Windows the `tzdata` package supplies it); an unknown one is refused with a plain message and, when one is close, the nearest real name. A machine with no zone data at all skips this check, and `trillion doctor` says when the saved timezone is not a real one. A hand-edited file that breaks these rules reads as not set up, with one exception: a timezone this machine does not know is set aside and the names are kept, because whether a zone is known depends on the machine's data, not on the file. The page never asks for a timezone (it sends the browser's), so when one is refused it saves again at once with the nearest real name, or with none, and she says which.

## Directness

`balanced` is the soul as written. `blunt` and `gentle` each add one authored paragraph (`persona/prompts/tone_*.md`) after the soul. An overlay adjusts her and never replaces her (PER-7 is the fuller version of this, at P1).

## Checked

- `tests/unit/test_persona_setup.py`: round trip, the file text, every refused name and timezone, a hand edit that breaks the rules, the overlays, and that no registered tool names the setup path.
- `tests/api/test_onboarding.py`: due on a fresh install, token and origin required, both files written and the next system prompt uses both names, refusals write nothing, skip survives a restart, a rename takes effect on the next turn, and the socket takes no such message.
- `tests/spine/test_deny_floor.py`: a tool that targets `USER.md` or `IDENTITY.md` is refused.
- `apps/web/test/onboarding.test.ts`: every step, the ways people say a name, skip, a failed save, and the rename flow.
- By hand on macOS with a brand-new data folder: the first run with no model, a reload, a rename, a skip, and a real reply from `gemma4:e4b` that used both names.

## Not checked

- Windows and Linux by hand.
- A data folder the app cannot write to, beyond the `not_saved` code path being covered by a unit of logic and not by a real read-only folder.

## Known limits

- **The timezone is stored and not yet used.** The core runs on the user's machine, so `now` already comes from the local clock. Using the stored zone needs time zone data that Windows does not ship.
- **Her scripted lines are never spoken**, even with a voice chosen. Onboarding runs as text.
- **The soul is not copied into the data folder.** The PRD says onboarding copies `SOUL.md` there; this slice leaves her on the shipped one, so testers keep getting updates to it. That needs a decision before Phase 0 is done.
- An introduction is offered only in an empty conversation. If a tab resumes a conversation with history, Names in Settings is the way in.

## The second slice: the ladder, the card and her first words

Added the same day, on a first run only. A rename still ends when the names are saved.

**The ladder (ONB-3, ONB-4, ONB-9).** After the names she asks about a model, then a voice, then a way to listen. At each step she names the free choice and the paid ones in a sentence, and the quick replies either open the existing settings dialog for that step or leave it for later. Anything typed that is not a skip opens the dialog too. A step that is already set up is not asked about. When the dialog saves, the core's `model`, `speech` or `listening` message moves her on. Skipping the model ends the ladder, because a voice and a microphone are no use without one.

**The example card.** She states the rule (anything that changes something shows a card first) and shows one. It is drawn by the page with the same markup as a real card, titled "Example", and it never reaches the core: nothing is pending, nothing is delivered, and both buttons do nothing but move the script on. Typing yes or no works, as it does for a real card. Her next line says outright that nothing happened.

**Her first real words.** When the script ends with a model in place, the page sends `greet`. The session answers once, only at the start of a conversation, and only for someone who has introduced themselves. What the model is told is authored in `persona/prompts/greeting.md` and filled with the two names. Nothing the page sent is passed on, so the message cannot carry an instruction. It is recorded as a host note, the same way a tapped card is.

Checked: `apps/web/test/onboarding.test.ts` covers every rung, skipping, what is already set up, and both answers to the card. `tests/api/test_onboarding.py` covers the greeting: it happens, it happens once, it is refused mid-conversation and before an introduction, and extra fields from the page never reach the model. By hand on macOS in a brand-new data folder: names, the model dialog, a model saved, voice and mic left for later, the card approved, and a composed greeting from `gemma4:e4b` ("Sam. Glad to finally be out of the script.").

Limits of this slice:

- **No prices (ONB-10).** She says who bills you and never how much. A per-hour estimate needs figures we can stand behind, and the spend caps it should be offered beside (BRN-7) are not built.
- **The free and paid names are written into her lines**, not read from the provider catalogs. A new provider needs a line edited here.
- **A reload mid-ladder does not resume it.** The names are saved by then, so the rest is reached from the buttons at the top.
- The example card is not a real pending action, so it does not exercise the spine. It shows the surface only.

## The third slice: names read right, a folder, and who she trusts

**Names.** Her name is read out of the ways people answer her question: "call me Nova", "I want to call you Nova", "something else, Nova", "how about Nova?", "let's go with Nova", "Nova please". Keep it has as many ways ("keep it", "Trillion is fine", "nah, stick with it", "you pick"). A name read out of a sentence is said back with Yes and No before it is used, so a wrong guess is never written to `IDENTITY.md`; a bare name is taken at once ("Nova it is."). A reply over five words is asked again rather than saved. The user's own name takes the same care ("it's Robin", "Robin here", "I'm Robin, nice to meet you", curly apostrophes).

**A folder (shared folders spec, D2).** After the ears and before the card she asks for a folder to work in, saying plainly that a shared folder lets her read and change what is in it, that every change still shows a card, and that a home folder or a whole drive is never shared. The quick replies are the Desktop and Documents (when the core lists them among the places), Another folder (Settings, Shared folders) and Not now. A tap shares the folder through `POST /api/folders` with `access: change`, the same route and store as Settings; typed words never share anything, and only a folder she offered can be shared. A refusal is said in the core's words and she asks again. The step is skipped when a folder is already shared, and a share made in Settings meanwhile moves her on.

**Who she trusts (SPN-15).** As the script ends, whichever way it ends, she says once that she trusts whoever is at this computer, cannot tell one voice from another, and that anything costing money or that cannot be undone waits for a tap on its card.

Checked: `apps/web/test/onboarding.test.ts` covers every phrasing, the check step, the folder step's offers, tap, refusal, skip and Settings path, and the trust line on every way out.

## Left for later slices
- Prices and spend caps at the point a key is entered (ONB-10, BRN-7).
- Her scripted lines spoken aloud once a voice is chosen.
- `trillion setup` and `trillion doctor` (ONB-2, ONB-5).
