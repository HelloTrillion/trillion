# Listening: she hears, through any provider

**Date:** 2026-10-09 · **Requirements:** VOI-1, VOI-3, VOI-4, VOI-8, VOI-10, VOI-11, VOI-11a, VOI-11b, VOI-11c, VOI-14, VOI-15, VOI-15a, VOI-15b, VOI-15d, ONB-7

This is the first slice of listening. It makes the "press the mic and talk" moment work end to end, with one free local recognizer and one hosted one, and leaves the capture hardening and the remaining providers for the next slice (listed at the end).

## What a turn looks like

1. The user presses **Speak**. The page opens the microphone inside that click and sends `listen.start`.
2. The core answers `mic.start`, the orb goes to `arming`, the recognizer connects, and the orb goes to `listening`.
3. The page sends 16 kHz mono PCM16 as binary frames of about 60 ms.
4. The core decides when the user has stopped (below), sends `mic.stop{reason}`, and the page closes the microphone.
5. The words become a user message (`transcript.final`), and the turn runs exactly as a typed one does.
6. If her reply is spoken, the page reports `playback.ended` when it has finished sounding. Only then, and only after a spoken turn, the core reopens the microphone for three seconds (`mic.start{follow_up: true}`).

The core opens and closes the microphone; the page only follows. Closing the tab, typing a message, pressing Stop or Escape all close it.

## Deciding the user has stopped

`voice/endpoint.py` is pure and is clocked by the audio itself (time is samples received), so it behaves the same under test as on a slow machine.

| Rule | Value |
|---|---|
| Quiet after a final segment from the recognizer | 0.4 s ends the turn |
| Quiet with no final to lean on | 1.0 s ends the turn |
| Quiet is not believed while words arrived this recently | 0.5 s |
| Nothing louder than a dead-microphone level | after 2.5 s: `inaudible`, and she says so |
| Nobody speaks after a click | 8 s: closes quietly |
| Nobody speaks in the follow-up window | 3 s: closes quietly |
| No audio frames arrive at all | 3 s: `inaudible` |

"Speech" is loudness above three times the room's own level, learned from the quiet parts, sustained for 60 ms. A final segment is not the end of a message: several finals, a pause mid-thought and a self-correction all make one message (VOI-15a).

## Providers

`STTProvider` (in `voice/types.py`) has `open(choice)`, `choices()` and `prepare(choice)`, and says whether it is `streaming`. `voice/catalog.py` lists them in `LISTENING`; the Mic panel is generated from that list.

| Provider | How it works | Cost and data |
|---|---|---|
| Local Whisper (`faster-whisper`, optional install) | Buffers the utterance, transcribes it when the user stops. No words while speaking. | Free. Nothing leaves the machine. |
| Deepgram Nova-3 | Streams audio up a WebSocket; words come back while the user speaks. | The user's own key and bill. What the microphone hears goes to Deepgram. |

Both pass one contract suite (`tests/voice/test_stt_contract.py`). The browser's own speech recognition is deliberately not offered (VOI-11b).

**Model files (ONB-7).** `voice/models.py` downloads a model only when the user presses Download in the Mic panel, from an address pinned to an exact revision, and compares every file with a SHA-256 written in the repository. A file that does not match is deleted. The recognizer is loaded from that folder with network access off.

## When it fails

Typing always still works (VOI-14). A failure is `error{code: "listening", kind}` and a banner in plain words: a dead microphone, a rejected key, an unreachable provider, a recognizer that is not installed. Sound with no words in it gets a notice rather than silence. The page reports its own failures (permission denied, no device) the same way.

Nothing heard is logged. Audio is held in memory for the length of one utterance and dropped.

## Checked

- 577 Python tests and 54 web tests, including the two contract suites.
- Local Whisper for real: the pinned `base.en` files downloaded and passed their checksums, and a spoken sentence was transcribed word for word.
- In the browser with a stand-in microphone playing that recording: orb states, the transcript becoming the message, and a confirmation card raised from a spoken request.
- By the maintainer, speaking into a real microphone on macOS with local Whisper.

## Not checked

- Deepgram against the real service. Its adapter is tested against a stand-in socket only.
- Firefox, Safari, Linux and Windows capture.

## Left for the next slice

- VOI-2 capture guards: the stereo channel latch, the echo-cancellation fallback, and pre-roll before the click.
- VOI-7 sign-off detection ("okay, thanks" should not get the last word).
- VOI-9 voice barge-in, VOI-13 diagnostics endpoint, a global hotkey (that belongs to the desktop app).
- OpenAI transcription, and download progress in the Mic panel.
- A filled speaker embedding (VOI-15): the seam exists as `Utterance.speaker`, always `None`.
