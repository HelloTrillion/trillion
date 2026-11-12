# Who said yes

**Date:** 2026-11-07 · **Requirements:** SPN-14a, SPN-15a, SPN-18, PER-13 (PRD §5.3a) · **Builds on:** [the speaker-embedding seam](2026-11-07-small-gaps.md#the-speaker-embedding-seam-voi-15) (VOI-15)

## Why

The gate (SPN-2) proves what was approved, not who approved it. Phase 0 anchors a spoken yes to the session (SPN-14): anyone in the room, a podcast, or a recording of the owner's earlier yes can close a card that a spoken yes may close. §5.3a closes that gap for the people who want it: the owner teaches her their voice, and from then on a spoken yes counts only in that voice, and anyone else gets nothing personal.

## Threat model

**What it protects against, once a voice is enrolled:**

1. Another person in the room saying yes to a card (SPN-18).
2. Audio from a speaker (a podcast, a video) saying yes.
3. A recording of something she already heard the owner say, played back: an enrolment phrase, or one of the owner's last 16 accepted turns (SPN-14a's replay fixture).
4. A synthetic voice that does not sound enough like the owner (SPN-14a's clone fixture, at the threshold).
5. Another person asking her for the owner's things: memories, files, folders, connected accounts (email, calendar, Slack, Stripe, MCP servers), her board, reminders, rules and helpers (PER-13).

**What it does not protect against.** Said plainly in `SECURITY.md` too:

- **A good clone.** The model scores how alike two voices sound. There is no liveness or anti-spoofing model, so a synthetic voice that scores like the owner passes. A free, local anti-spoofing model good enough to ship was not found.
- **A recording she never heard.** The replay check compares against sounds she heard herself. The owner's yes recorded somewhere else and played to her is judged as a live voice.
- **A heavily changed replay.** Replays are caught by the sound's fingerprint. A recording replayed through a poor speaker in a large room, or re-recorded and edited, can change it enough to pass as live.
- **The conversation so far.** Earlier turns stay in her context. On another voice's turn she is told not to repeat them; that is an instruction, not code.
- **Anyone at the keyboard or the screen.** Typed messages and taps are the session's, as in Phase 0. A tap is a physical act on the owner's machine.
- **Nobody enrolled.** Everything is exactly as in Phase 0, and the Phase 0 statement in `SECURITY.md` still applies. A voiceprint deleted outside Trillion is nobody enrolled from the next start; one damaged is not (decision 12).

## Decisions

1. **One model, fetched on request.** WeSpeaker's ResNet34-LM speaker model (ONNX, 26.5 MB), trained on VoxCeleb2 and published by the WeSpeaker project under **CC BY 4.0** (the dataset's licence), which allows downloading and use with credit. Trillion never ships it. Settings → Your voice fetches it from a pinned Hugging Face revision (`f0c48c2…`), checks its SHA-256 and size, and keeps it in `models/speaker` (ONB-7, the same `voice/models.py` path as Whisper). The credit and a link to the licence are on that screen. Nothing is fetched at runtime (ONB-8).
2. **Optional, like local Whisper.** It needs `onnxruntime` and `numpy`: `uv sync --extra speaker`. Without them, or without the model, the runtime keeps VOI-15's stub, and Settings says how to add them.
3. **Enrolment: three phrases.** About four seconds each, ordinary words; the last has a yes in it. Each is checked as it comes in: too short (under 1.2 s of speech), too quiet, so loud it clipped, or two voices (its two halves don't agree). Then the three must agree with each other; the one that doesn't is read again. The voiceprint is their mean.
4. **Where it lives.** `voiceprint.json` in Trillion's data folder, written mode 600 and replaced whole, never in the database and never sent anywhere. The deny floor already keeps every tool out of that folder. It holds the template, the three phrases' embeddings and fingerprints, and the last 16 accepted turns' embeddings and fingerprints, for the replay check. Delete removes the file. Nothing about a voice is logged: not a vector, not a score.
5. **Scoring.** Cosine similarity against the template, with named thresholds in one place (`voice/speaker.py`): `ACCEPT = 0.65`, `REJECT_FLOOR = 0.40`. How they were chosen is written beside them: on six synthetic voices, the same voice scored 0.82–0.91 on sentences and 0.38–0.68 on a short yes; other voices averaged under 0.2, at most 0.49 on a word and 0.62 on a sentence (two voices from one speech engine, the nearest thing to a clone). A short "yes" alone is often not enough to tell, so it lands in the band between, where she asks for a longer one.
6. **Four verdicts.** `owner` (at or above ACCEPT), `unsure` (between, or it could not be scored: too short, the embedder failed or timed out, another model's vector), `other` (below the floor), `replay` (the same sound as something already heard). Not knowing is never `owner`. (A fifth since 2026-11-12: `unchecked`, while the voiceprint can't be read; see decision 12.)
7. **The replay check is on the sound, not the voice.** Speaker embeddings proved too unstable for it: the same recording with half a second of silence added scored 0.63 against itself. So each utterance also carries a fingerprint (after Haitsma and Kalker): 16 bits a frame of how the spectrum moves, compared at the best alignment over loud frames. Replays with gain, noise, a 0.2% speed change or a small room's echo differed in 0–24% of bits; the same voice saying the same words again in 37–44%; `REPLAY_BITS = 0.30`. An embedding within 0.99 of one already heard is a replay too (a digital copy). `SpeakerEmbedding` gains `fingerprint` (empty when an embedder makes none).
8. **The gate is speaker-scoped (SPN-14a, SPN-18).** `ToolContext.speaker`, set by the session from the verdict on spoken turns only, never from anything the model says. In `decide`: a confirmation not by tap, with a speaker other than `owner`, is refused: `not_owner` for `other` and `replay`, `voice_unsure` for `unsure`. Tap-only actions still need the tap first (SPN-14 is not weakened by a matching voice). Declining is allowed in any voice: it runs nothing, and the card can be asked for again.
9. **Never a dead end (SPN-15a).** The first `voice_unsure` on a card tells her to ask for the yes again, a little longer; the next tells her to ask for a tap. The card stays up throughout, and its tap works as it always has.
10. **Default-private, enforced in the router (PER-13).** On a turn whose speaker is not `owner`, the router refuses every tool but `GUEST_TOOLS` (`web_fetch`, `stay_quiet`) before any of its code runs, with `private`. The session also: offers her only those tools; leaves out recalled memories, the board, helpers and helper offers; replaces each waiting card's words with "an approval waiting for the owner"; leaves the shared folders out of the system prompt; and adds a host note that names who she may be talking to and tells her to decline personal things with a plain, named reason. `unsure` is private too, with a note that asks them to say it again or type it.
11. **What "private" means.** Memories (recall, write, forget), files and folders, connected accounts and MCP tools, her board and home, reminders, timed asks and routines, standing rules, helpers, `describe_self` (it names the shared folders), the decision record, and approving or raising any card.
12. **Nobody enrolled: unchanged.** `judge` returns None, `speaker` stays None, and every path is Phase 0's. PER-13's row says "or nobody is enrolled"; taking that literally would make her private to every voice for everyone who never enrols, so it waits for enrolment. Nobody enrolled means no voiceprint file. A file that is there but can't be read is someone enrolled whose voice can't be checked: every spoken turn is `unchecked`, private, and approves only by a tap, and the owner is told.

    > **Changed 2026-11-12** ([a voiceprint that can't be read](2026-11-12-unreadable-voiceprint.md)). This decision first said "a voiceprint file that can't be read counts as nobody enrolled", to fall back to the session. An outside tester showed the cost: damaging the file and restarting let any voice approve again, with PER-13's privacy gone and Settings showing "Not set up". It now fails closed.
13. **The tap and the typed message are untouched.** Only spoken turns are judged.

## What the user sees

- **Settings → Your voice.** "Not set up", then "Get the voice model (27 MB download)" with its credit, then "Teach her your voice": three phrases, Record and Stop, a plain line when one needs reading again ("That was too quiet. Come a little closer…"). Then "She knows your voice", with Teach her again and Forget my voice (asks first).
- A spoken yes in their voice works as before. A short, unclear one: "I couldn't be sure that was you. Say it again?" Then: "Tap Approve on the card."
- Someone else says "yes, do it": nothing happens, and she says that voice can't approve it. Someone else asks what's on the calendar: she says she keeps that for the owner.

## How

- `voice/providers/wespeaker.py`: the pinned model, Kaldi-style filterbank in numpy, the ONNX session, the fingerprint, the phrase checks, `LocalSpeaker` (the `SpeakerEmbedder`), and `embedder(enrolled)`, which picks it or the stub.
- `voice/speaker.py`: the thresholds, `judge`, the replay check, enrolment, and `Voiceprints`, the file's one reader and writer.
- `spine/contract.py` (`Speaker`, `ToolContext.speaker`, `private`), `spine/confirm.py` (the speaker check in `decide`, its words), `spine/router.py` (`GUEST_TOOLS`, the private refusal, the unsure count per card).
- `api/session.py`: judges each spoken turn in `_heard` (a thread, off the loop), keeps accepted ones, sets `speaker` in `_ctx`, and builds a private turn's input, tools and system prompt. A draft built for one voice is not used for another.
- `api/voiceprint.py`: `GET /api/voiceprint`, `POST /model`, `POST /phrase`, `POST /save`, `DELETE /draft`, `DELETE`. The phrases' embeddings wait in memory until saved; no voice data goes back to the page.
- `apps/web/src/voiceprint.ts`: the section, with its own microphone open only while a phrase records.

## Tests

- `tests/spine/test_speaker_gate.py`: each verdict against the gate, the unsure-then-tap path, the tap and typed yes untouched, decline in any voice, tap-only still tap-only, and every personal tool refused for every non-owner verdict.
- `tests/voice/test_voiceprint.py`: scoring, the replay fixtures (the owner's accepted yes played back; an enrolment phrase played back; a digital copy), the clone fixture, enrolment's checks, and the file (mode 600, unreadable files failing closed, delete).
- `tests/api/test_speaker_identity.py`: whole spoken turns with a fake embedder: the owner's yes, a guest's yes (the card stays, the tap still works), a replayed yes, an unsure yes then the owner's, a guest asking for a memory.
- `tests/api/test_voiceprint_api.py`, `apps/web/test/voiceprint.test.ts`: enrolment and forgetting.
- `tests/voice/test_wespeaker.py`: the pinned model and what needs no numpy; the filterbank, fingerprint and phrase checks run where numpy is installed.
- CI needs no model and no real audio: synthetic embeddings and fingerprints throughout. The thresholds were measured once by hand with the real model on system text-to-speech voices.

## Platforms

The check runs in the server, so it works wherever the server does; onnxruntime has wheels for macOS, Windows and Linux. The page records phrases with the same microphone code as listening, in any browser and in the desktop app. Windows ignores mode 600: the voiceprint file relies on the user folder's own permissions there. See [`docs/platforms.md`](../platforms.md).

## Not now

- Liveness and anti-spoofing against good clones.
- Measuring the thresholds on recorded human speech across microphones; for now they come from synthetic voices.
- More than one enrolled person, or a "guest" voice with its own permissions.
- A per-voice view of the conversation's history, so earlier turns are hidden from a stranger in code.
- SPN-20 (the owner's own words as the confirmation), which this unblocks.
