<div align="center">

# Trillion

**The voice-first AI agent with a face.**
Talk to her. Watch her think. Trust that she never acts without your "yes."

[hellotrillion.ai](https://hellotrillion.ai) · Apache-2.0 · Python + TypeScript

</div>

---

> **Status: pre-alpha.** You can talk or type to her in a browser, with the Galaxy behind, hear her answer aloud (a free system voice, or your ElevenLabs account), and approve a file write on a card. She hears through Whisper on your own machine (free) or your Deepgram account. She remembers what you tell her between conversations, in plain files you can open. On a first run she asks your name and what you want to call her, walks you through choosing a model, a voice and a way to listen, and shows how approval works.
> This repository is the foundation being extracted from a private build that has run every day for six months. The first milestone is **the Shell** — see [the roadmap](#roadmap) and [`docs/PRD.md`](docs/PRD.md) for exactly what that means. Watch or star the repo if you want to know when it's usable.

## What this is

Most open-source agents are chat programs that learned to talk later. Trillion is built the other way around: a real-time voice conversation is the product, and everything else serves it.

**Three things make her different.**

### 1. The Galaxy

She has a face. An orb of noise-displaced light breathes while idle, turns gold when the mic is hot, shifts purple while she thinks, and rides the waveform of her own voice while she speaks. Sub-agents orbit her as a constellation. Tool calls arc out as energy. It is a real visual system, not a spinner — and it ships as a standalone package you can embed anywhere.

### 2. The Voice

Sub-second turn-taking, a wake word, barge-in, and a microphone that is already warm when you start speaking, so your first syllable is never clipped. With Deepgram you can pause mid-thought without being cut off, and say "yeah" over her without stopping her; anything else stops her and she listens. "Start thinking before I finish" (Settings → Hearing, on by default) lets her begin her answer early, at the cost of some extra model calls. Under that sits a personality with actual opinions: brief, dry, deadpan. She pressure-tests your plan in one question instead of five bullet points, and she never opens with "Great question!"

### 3. The Spine

**Confirmation is enforced in code, not requested in a prompt.**

A tool that sends, spends or destroys can never run on the turn it is proposed. Its arguments are frozen the moment she asks you, the confirmation call carries no argument fields at all, and the deny floor applies on both paths. Compaction can't erase the rule, because the rule was never in the context window.

Other agents have deleted inboxes while an instruction to "confirm first" scrolled out of memory. That class of failure is structurally impossible here.

## Roadmap

| Phase | What | Status |
|---|---|---|
| **0 · The Shell** | Galaxy engine, voice loop, agent core, spine, memory, persona, onboarding, CI. macOS and Linux; the core also runs on Windows | 🔨 in progress |
| **1 · Private alpha** | Waitlist cohort, Google + MCP + skills, local no-key voice, signed builds, Windows hardening | planned |
| **2 · Public beta** | The repo goes public, Slack + Telegram, docs site | planned |
| **3 · 1.0** | Signed skill registry, semantic turn detection | planned |

The full specification, including every requirement ID that Phase 0 must satisfy, is in [`docs/PRD.md`](docs/PRD.md). How each one gets proven is in [`docs/TESTING.md`](docs/TESTING.md).

## Architecture

```
apps/web         Vite + TypeScript UI (the app you talk to)
apps/desktop     Tauri v2 shell for macOS
packages/galaxy  @hellotrillion/galaxy — the visual engine, standalone
src/trillion/    Python core
  brain/         agent loop, model providers, prompt assembly
  voice/         VAD, STT, TTS, sentence pipeline, turn-close
  spine/         tool contract, confirmation, deny floor, untrusted wrapping
  persona/       soul loading, modality overlays, tonal checkpoint
  memory/        markdown + sqlite-vec hybrid recall
  agents/        manifests, loops, loop guard, supervisor
  storage/       SQLite (no hosted database required)
```

Everything runs on your machine, in one process, against a local SQLite file. There is no server to expose, no account to create, and no infrastructure of ours involved at any point — your API keys live in your operating system's keychain, and the only outbound traffic goes to providers you connected yourself. With the network off, Trillion still starts. Model, speech-to-text and text-to-speech providers all sit behind interfaces: you choose each one, and each has a free, fully local option beside the paid ones (Anthropic, OpenAI, ElevenLabs, Deepgram).

## Try it

**From a release** (the easy way, and the only way Google sign-in works out of the box). You need [uv](https://docs.astral.sh/uv/). Download the `.whl` file from the latest [release](https://github.com/HelloTrillion/trillion/releases), then:

```bash
uv tool install ./trillion-0.0.1-py3-none-any.whl
trillion run
```

To upgrade later, download the newer file and run `uv tool install --force` with it.

**From source**, to work on it: you need uv, Node 22+ and pnpm.

```bash
uv sync
pnpm install
pnpm --filter @hellotrillion/web build
```

Then start it:

```bash
uv run trillion run --grant ~/Desktop
```

The app opens in your browser and asks who should answer: Anthropic (Claude), OpenAI, a free local model through [Ollama](https://ollama.com) or LM Studio, or any OpenAI-compatible service. Paste your key there. It is checked, then stored in your operating system's keychain: never in a file, the database or a log, and it is sent only to the provider you chose. Change it any time in **Settings** (top right), under Model.

Developers can skip the panel with `TRILLION_ANTHROPIC_API_KEY`, or `TRILLION_OPENAI_BASE_URL` plus `TRILLION_MODEL`; the environment wins when set.

**If something isn't working**, ask it:

```bash
uv run trillion doctor
```

It checks the data folder, the database, the port, the keychain, your model, voice and mic choices, and says how to fix whatever is wrong. It only looks; it changes nothing. `--secrets` also tests every saved key without printing one, and `--offline` skips the checks that need the network. `uv run trillion setup` does the first-run preparation, runs the same checks, and then starts the app; it is safe to run again.

**If she feels slow**, `uv run trillion doctor --trace` shows how long her last ten turns took (`--trace 30` for more), step by step from the moment you stopped talking: your words ready, her first words on screen, her first audio, when it reached your speaker, and tokens and cache use per model call. It also says whether the Trillion running is the one on disk, so a fix you saved is not mistaken for a fix that is running. Only timings are kept, never what was said. To measure a fresh set of spoken turns without making a sound, see `evals/latency/run.py`.

`--grant` shares one folder with the file tools; without it they are off. Any folder that exists will do: if you have no `~/Desktop` (common on Linux), make one for Trillion, such as `mkdir ~/trillion-files`, and grant that. Every write asks first, on a card you approve. Trillion listens on `127.0.0.1` only.

**Talking to her.** Open **Settings** (the menu button, top left), then **Microphone**, and choose how she hears. *On this computer (Whisper)* is free and nothing you say leaves the machine; it needs one extra install, `uv sync --extra whisper`, and a one-time download you start from that panel. *Deepgram* is faster and shows your words as you speak; it needs your own key. Then press **Speak**, or **Space** when the message box is empty, talk, and stop: she answers when you go quiet. Settings, under Shortcuts, lets you record any other key or combination, or turn it off. The microphone is on only while you are speaking to her.

Just the orb: `pnpm --filter @hellotrillion/galaxy dev`

**Connecting your apps (MCP).** Open Settings → **Connected tools** and pick an app: Notion, Linear, Todoist, Canva, Stripe, Zapier, GitHub and more. Most connect by signing in with the app in your browser; a few take a key, which goes straight to your system keychain. **Add your own** connects any other MCP server: paste its address, the command from its instructions, or its settings for Claude Desktop, Cursor or VS Code. Connections start and stop without restarting Trillion.

Every tool asks you first until you switch it to run on its own, and what an app sends back is treated as information, never as instructions. Only connect servers you trust: a local server is a program running on your computer.

**Gmail, Google Calendar and Google Drive** are built in: choose one and sign in with Google. Sending email and changing your calendar always ask. Google requires every app that signs people in to be registered, so the first time, Settings walks you through making your own free OAuth client in Google Cloud (about five minutes); a distribution ships its own registered Google app instead, so its users just sign in: `google_client.json` next to `connectors/google/config.py` (the file Google gives you for a Desktop client, plus `"picker_key"` for Drive), or `TRILLION_GOOGLE_CLIENT_ID`, `TRILLION_GOOGLE_CLIENT_SECRET` and `TRILLION_GOOGLE_PICKER_KEY`. The file is git-ignored and included in the wheel when present, so official builds write it from a CI secret before `uv build`.

**Slack and Telegram.** With Slack connected she can search, read and post in your workspace (posting always asks), and you can talk to her there. Telegram lets you talk to her from your phone through a bot you make with @BotFather. In both she only answers: no tools, nothing approved from a chat, and only people you allow (the list starts empty). Both connect outbound, so nothing on your computer is reachable from the internet.

**Skills.** Settings → **Skills** lists a few skills that come with Trillion (email replies, meeting prep, a weekly review) and any folder of `SKILL.md` skills you add; your `~/.claude/skills` works as it is. Every skill starts off: turn on the ones you trust. She reads a skill when a task calls for it. A skill can teach her how to do something, never let her skip asking you, and she never runs a skill's scripts. If a skill's files change, it switches off until you look at it again. Your choices are kept in `skills.json` in Trillion's data folder, and `trillion doctor` reports them.

**Reminders and timed asks.** Ask her to "remind me at four to call Sam", or "every weekday at 9, check my email and tell me what's urgent". Times are in your own timezone (the one you gave in Settings → Names and tone, else the computer's own, where the system reports one; on Windows, set it in Names and tone). A reminder says her words at that time. A timed ask shows a card first, then runs at that time with only the tools that never ask you, and tells you what she found. With a page open it arrives in the conversation, never in the middle of a reply, and if you start typing or talking while she says it, she stops and listens. With no page open the computer shows a notification (a timed ask's says only "Ready in Trillion") and keeps it for the next time you open Trillion. They run only while Trillion is running: one missed while it was not arrives late and says so, and a timed ask more than an hour late is reported missed instead. Settings → **Reminders** lists them, recent results, and when the scheduler last checked; `trillion doctor` reports them too.

Connections are kept in `mcp.json` in Trillion's data folder (`~/.trillion/mcp.json`), in the format Claude Desktop and Cursor use, so you can also edit it by hand. A key is never written there, only its keychain name (`keychain:NAME`). `"risk": "read"` under `tools` lets a tool run without asking. `trillion doctor` checks the file.

## Contributing

We want help, and the fastest way in is [`CONTRIBUTING.md`](CONTRIBUTING.md). It explains the three-pillar test every feature has to pass, the **never list** (things a pull request may never do), and the sign-off we require on commits.

Safety-critical areas (`src/trillion/spine/`, `src/trillion/persona/`, voice capture) need a maintainer review. That isn't gatekeeping for its own sake — it's how the guarantee above stays true.

## Security

Please don't open a public issue for a vulnerability. See [`SECURITY.md`](SECURITY.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

"Trillion" and "Hello Trillion", along with the orb mark, are trademarks of Lightbulb Moment Labs, Inc. The code is open; the name is not. See [`TRADEMARKS.md`](TRADEMARKS.md).
