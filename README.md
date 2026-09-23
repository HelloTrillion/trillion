<div align="center">

# Trillion

**The voice-first AI agent with a face.**
Talk to her. Watch her think. Trust that she never acts without your "yes."

[hellotrillion.ai](https://hellotrillion.ai) · Apache-2.0 · Python + TypeScript

</div>

---

> **Status: pre-alpha. Nothing works yet.**
> This repository is the foundation being extracted from a private build that has run every day for six months. The first milestone is **the Shell** — see [the roadmap](#roadmap) and [`docs/PRD.md`](docs/PRD.md) for exactly what that means. Watch or star the repo if you want to know when it's usable.

## What this is

Most open-source agents are chat programs that learned to talk later. Trillion is built the other way around: a real-time voice conversation is the product, and everything else serves it.

**Three things make her different.**

### 1. The Galaxy

She has a face. An orb of noise-displaced light breathes while idle, turns gold when the mic is hot, shifts purple while she thinks, and rides the waveform of her own voice while she speaks. Sub-agents orbit her as a constellation. Tool calls arc out as energy. It is a real visual system, not a spinner — and it ships as a standalone package you can embed anywhere.

### 2. The Voice

Sub-second turn-taking, a wake word, barge-in, and a microphone that is already warm when you start speaking, so your first syllable is never clipped. Under that sits a personality with actual opinions: brief, dry, deadpan. She pressure-tests your plan in one question instead of five bullet points, and she never opens with "Great question!"

### 3. The Spine

**Confirmation is enforced in code, not requested in a prompt.**

A tool that sends, spends or destroys can never run on the turn it is proposed. Its arguments are frozen the moment she asks you, the confirmation call carries no argument fields at all, and the deny floor applies on both paths. Compaction can't erase the rule, because the rule was never in the context window.

Other agents have deleted inboxes while an instruction to "confirm first" scrolled out of memory. That class of failure is structurally impossible here.

## Roadmap

| Phase | What | Status |
|---|---|---|
| **0 · The Shell** | Galaxy engine, voice loop, agent core, spine, memory, persona, onboarding, CI | 🔨 in progress |
| **1 · Private alpha** | Waitlist cohort, macOS, Google + MCP + skills, signed build | planned |
| **2 · Public beta** | Windows + Linux, local no-key voice tier, Slack + Telegram, docs site | planned |
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

Everything runs on your machine, in one process, against a local SQLite file. There is no server to expose, no account to create, and no infrastructure of ours involved at any point — your API keys live in your operating system's keychain, and the only outbound traffic goes to providers you connected yourself. With the network off, Trillion still starts. Model, speech-to-text and text-to-speech providers all sit behind interfaces: the first release uses Anthropic, Deepgram and your own ElevenLabs account, with cheaper and fully local options to follow.

## Contributing

We want help, and the fastest way in is [`CONTRIBUTING.md`](CONTRIBUTING.md). It explains the three-pillar test every feature has to pass, the **never list** (things a pull request may never do), and the sign-off we require on commits.

Safety-critical areas (`src/trillion/spine/`, `src/trillion/persona/`, voice capture) need a maintainer review. That isn't gatekeeping for its own sake — it's how the guarantee above stays true.

## Security

Please don't open a public issue for a vulnerability. See [`SECURITY.md`](SECURITY.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

"Trillion" and "Hello Trillion", along with the orb mark, are trademarks of Lightbulb Moment Labs, Inc. The code is open; the name is not. See [`TRADEMARKS.md`](TRADEMARKS.md).
