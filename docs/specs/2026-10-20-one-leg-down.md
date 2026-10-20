# One leg down, and the airplane test

**Date:** 2026-10-20 · **Requirements:** VOI-14, SUR-8, PER-11 · **Phase 0 items:** 12, 15

Most of this already worked: when her model, her voice or her ears failed, the turn carried on and the page showed a banner. What was missing was her saying it, aloud when her voice still works; one more try when a voice service has a bad second; and the two tests the PRD names.

## What she says

Each failure has one authored sentence, in `persona/prompts/degraded.md`, keyed by the leg and the provider's failure kind: what broke, then what happens next. "I can't think right now: I can't reach the model provider, so check the connection or switch to a model on this computer." They are authored rather than composed because when the model is the leg that failed there is nobody to compose anything (PER-11 allows authored lines; it forbids catch-all ones, and none of these is).

| Leg down | What happens |
|---|---|
| Model | The sentence is spoken through her voice if she has one, and shown in the banner. No reply is invented. |
| Ears | The sentence is spoken and shown; the microphone is not reopened when it ends, so one fault cannot repeat itself; typing works. |
| Voice | The reply arrives as text, whole. The sentence is shown once, not once per sentence of the reply. |

The core sends the sentence with the error (`sentence`), and the page puts it in the banner (SUR-8). An older core without it still gets the page's own wording.

## Speaking again

A voice failure that tends to pass (unreachable, timed out, the service having trouble, rate limited) is tried once more after half a second, for the sentence that failed, if none of it had been heard yet. A refused key or an empty account is not retried. The speaker holds text, never a tool call, so speaking again cannot repeat anything the reply did: the test approves a file write, fails the voice, and counts one execution.

## The tests

- `tests/integration/test_degraded_provider_matrix.py` (VOI-14): the model down with a voice (spoken and shown) and without one (shown); the voice down (text whole, said once, one retry); the ears down (spoken, typing works, the microphone not reopened); and a voice that drops once or fails for good after an action succeeded (the result shown, one execution). I checked it can fail by removing the spoken sentence.
- `tests/integration/test_airplane.py` (PRD §4): every lookup of a host other than this machine is refused. The page, settings, memory written and read back, and a whole conversation with a model on this computer all work, and nothing tries to leave the machine. `doctor` finishes quickly with and without `--offline`. A hosted model, a hosted voice and hosted ears each fail within ten seconds with her sentence, using the real Anthropic, ElevenLabs and Deepgram adapters, and the key never appears in what is sent to the page.
- By hand: a model saved while its server was up, the server stopped, a message sent. Her sentence arrived in 0.02 seconds.

## Not covered

- **The Galaxy and the microphone offline.** They run in the browser; the airplane test covers the core.
- **Per-capability liveness (CON-1).** Connectors are not built. Today a failure is known when a call fails, not before.
- **A spoken sentence when the voice itself is down.** By definition it is written.
