# Voice providers, and hardening the first server: design

**Date:** 2026-10-07 · **Tracks:** B (voice, first slice), C (spine and server fixes) · **Status:** built

## Part 1: what an independent review found in the first server

The first-conversation work merged without a second reader. A review afterwards confirmed these by running them. Each is fixed here with a test that fails without the fix.

| Finding | Fix |
|---|---|
| **The model could confirm its own pending write** if the user tapped any card mid-turn: every tap advanced the turn counter the same-turn rule depends on. | The turn a model acts in is fixed when its turn starts. Taps no longer advance it; a tap is judged as the turn after the newest thing said. Only typed (later, spoken) input counts. |
| Log redaction was never in effect under `trillion run` (the filter sat on the root logger, which does not see other loggers' records). | The scrub is installed in the log record factory, which every record passes through. Environment keys and the session token are now known values too. |
| Two interrupts during a tool, or one while the tool call was being saved, left a tool call with no result. Every later turn in that conversation would then fail. | A tool call and its result are saved as a pair that cancellation cannot split, however many times it arrives. |
| Two sessions on one conversation (a duplicated tab, a reload mid-tool) interleaved what they saved. | One live session per conversation. Opening it elsewhere ends the first, after its turn has finished recording. |
| Two turns could run at once in the gap after a turn ended. | A session runs its turns in one loop; the "busy" state covers the whole of it. |
| With OpenAI-compatible providers, a reply cut off mid tool call still ran the earlier calls. | A length cut-off stays a length cut-off even with tool calls in it, and nothing runs. |
| A rejected settings request echoed the submitted key back. | Validation errors name the fields and never quote the input. |
| A malformed address crashed the endpoint; the environment path skipped the "HTTPS or this machine" rule. | Malformed addresses are plain refusals; the environment obeys the same rule. |
| Tool-call ids made up for local servers repeated across replies. | Made-up ids are unique; another provider's ids are mapped into the characters Anthropic accepts. |

Also added in this pass: `describe_self` (BRN-6), safe formatting of her replies, a New conversation button, and a test that freezes every shipped migration (a database refuses to open if an applied migration changes by one byte).

## Part 2: she speaks, through any provider

Not everyone can afford a paid voice. Speaking and listening sit behind interfaces that no vendor shapes, and every rung of the ladder has a free path at Phase 0 (PRD VOI-11, 11a, 11b, 11c, updated the same day).

**What keeps it from hardening around one vendor**

- **One catalog** (`voice/catalog.py`). Each entry says what it costs and where the words go. The settings panel is generated from it and the core builds providers through it. A new provider is one adapter file and one entry.
- **Two very different providers from the first day:** the computer's own voice, and ElevenLabs.
- **One contract suite** (`tests/voice/test_tts_contract.py`) that every provider must pass. A test fails if a catalog entry is not under it.

**The two shapes a voice can take**

- **Made in the core** (ElevenLabs): the core synthesizes each sentence and streams PCM to the page in binary frames tagged with turn and sentence.
- **Made by the page** (the system voice): the core sends the sentence; the page speaks it with the operating system's own speech synthesis. No audio crosses the socket and nothing leaves the machine.

The page hides the difference: either way it has one voice that is or is not sounding, and the orb follows it.

**The pipeline** (`api/speech.py`)

1. The reply streams from the model. A splitter cuts it into sentences the moment each is certainly complete (not at "Dr." or "3.5").
2. Each sentence is made speakable: markup, code blocks, links and emoji are removed; no word is added.
3. The sentence is announced to the page, then synthesized if the voice is made in the core.
4. Before a tool runs, what she has said so far is spoken rather than held.
5. An interrupt stops synthesis and playback. A failing voice is reported once and the reply carries on as text (VOI-14).
6. A muted page is sent no speech, so nothing is synthesized or paid for.

**Deliberately simple for now**

- Sentences are synthesized one at a time, in order, rather than three ahead. A hosted voice arrives faster than it plays, so the next sentence is ready in time; this is revisited when latency is measured (EVL-3).
- Hosted voices have no in-app preview yet: playing a provider's sample needs a wider Content-Security-Policy than the app currently allows.
- The browser's built-in speech *recognition* is not used and will not be: in some browsers it sends audio to a third party the user never chose.

**Next in this track:** a local neural voice (Kokoro or Piper), OpenAI speech, then listening (Deepgram and local Whisper).

## From the first round of testing (issues 29 to 33)

| Issue | Change |
|---|---|
| 29: a reasoning model shows nothing while it thinks | The page shows a "Thinking" line in the conversation whenever a turn is running and no text is arriving. It is driven by the view alone (`waiting`), so it covers every provider, the gap before the first word and the gaps between tool calls. |
| 29, related | OpenAI-compatible services are no longer sent a fixed output cap of 4096. On those services the cap also counts thinking, so a reasoning model could spend it before its first word. A cap is sent only when one is known for the model. |
| 32: smaller models ask in chat before calling the tool | `rules.md` now says to call the tool right away, because the tool does the asking, and that a card reported by a `[host]` line is settled and must not be confirmed again. |
| 33: a model server on another machine on the network | "Another compatible service" accepts plain HTTP for a numeric address in a private range (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7). A key is never sent to such an address (`keyless`), and supplying one is refused as `key_needs_https`. Names and public addresses still need HTTPS; "A model on this computer" is still loopback only. |

Issues 30 and 31 are fixed by their own pull requests (34 and 35), which merge cleanly with this branch.
