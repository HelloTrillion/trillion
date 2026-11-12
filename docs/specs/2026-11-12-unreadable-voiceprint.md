# A voiceprint that can't be read

**Date:** 2026-11-12 · **Requirements:** SPN-14a, SPN-15a, SPN-18, PER-13 (PRD §5.3a) · **Changes:** decision 12 of [who said yes](2026-11-07-speaker-identity.md)

## Why

Decision 12 of the speaker-identity spec counted a `voiceprint.json` that exists but can't be read (bad JSON, another version, bad vectors, a permission error) as nobody enrolled, and so Phase 0: "falls back to the session". That is the right floor for someone who never enrolled, and the wrong one for someone who did. An outside tester showed it live. A second voice's yes was refused. She damaged the file and restarted, and the same voice's yes approved its card: the file was created. PER-13's privacy was gone too, and nothing said so: Settings read "Not set up".

So a damaged file was a way to undo the owner's enrolment without them knowing. Anyone who can write to the data folder can already do worse, but a file can also be damaged by accident: a full disk, a sync client, a bad restore. In both cases the owner should be told, and she should fail closed.

## Decisions

1. **Missing and unreadable are different.** No file: nobody enrolled, exactly as before (decision 12 stands for that case). A file that is there but can't be read: **someone enrolled, and no voice can be checked.** `Voiceprints.get` returns a third state, `Unreadable(why)`, beside a `Profile` and None.
2. **A new verdict, `unchecked`, as strict as `unsure`.** `judge` returns it for every spoken turn while the voiceprint is unreadable, whatever the embedding. It is not `owner`, so `ToolContext.private` holds, and in the spine it is everything `unsure` is: no spoken yes closes a card, and only `GUEST_TOOLS` run (PER-13). It differs from `unsure` in two places only, both about what she is told. The gate refuses with `voice_unchecked` and says to tap the card, every time. Asking for the yes again, a little longer, can't help when there is nothing to compare it with, so SPN-15a's fresh sample is skipped and the fallback is the tap from the first try. Her host note says why and how it is fixed.
3. **Why private, not a narrower "approvals need a tap" verdict.** A narrower verdict would keep her useful to the owner by voice, but it would also make damaging the file a way round PER-13: any voice could ask for the owner's calendar again, which is half of what the tester found. The owner enrolled to get both protections, and a damaged file is indistinguishable from one damaged on purpose. Typed messages and taps are untouched, so the owner loses nothing but speaking personal requests, and is told how to put it right. It is never weaker than "a tap for every approval".
4. **Typed and tapped turns are the session's.** Only spoken turns are judged (decision 13 of the speaker-identity spec, unchanged).
5. **She knows, in her own words.** On each spoken turn the host note says the owner's voice file can't be read, that anyone could be speaking, that a tap approves and a spoken yes doesn't, and that the owner fixes it in Settings, Your voice. She says it herself; there is no stock line (PER-11).
6. **The owner is told once.** When the runtime finds an unreadable voiceprint on start, `SecurityAlerts.voiceprint_unreadable` raises one security notice through the door (SEC-11): critical, not held for quiet hours, in her words or shown by its title and detail ("Your voice file can't be read: every approval needs a tap"). Not on every turn. Once per start while it stays unreadable, because it is a security state still in force.
7. **Never destroy the evidence or the owner's choice.** Nothing deletes, rewrites or moves the damaged file on its own: not loading it, not a spoken turn (an accepted turn is never kept, since none is accepted), not `doctor`. **Teach her again** replaces it whole, as any enrolment does. **Forget my voice** deletes it, and is the only way back to nobody enrolled.
8. **Locked or damaged.** A `PermissionError` is `locked`; anything else (not text, not JSON, too deeply nested, another version, bad vectors, a folder in its place) is `damaged`. Both fail closed the same way; the difference is only in what Settings and `doctor` suggest.
9. **Read once, like a profile.** The file is read on start and kept, whatever its state. Saving and forgetting set the kept state themselves, so teaching her again or forgetting takes effect at once, without a restart. A file put right by hand (its permissions, a good copy restored) is used from the next start, which also picks the embedder it needs. Reading it again on every turn was considered and left out: it would recover a profile while the session still has no embedder for it.
10. **Nothing about a voice is logged.** The first time, the log says `voiceprint can't be read (damaged)` or `(locked)`, once, and nothing of the file.

## What the user sees

- **Settings → Your voice**: "Your voice file can't be read, so she can't tell your voice from anyone's. Until you teach her again, every approval needs a tap." With **Teach her again** (once the model is there) and **Forget my voice**, which asks first. The summary beside "Your voice" reads "Your voice file can't be read", never "Not set up".
- **A security notice** on the first start that finds it.
- **A spoken yes**: nothing happens, and she says she can't check voices right now, so tap the card. A spoken request for something personal: she says the same, and that typing works.
- **`trillion doctor`**: a "Your voice" line. `info` not set up, `ok` she knows your voice, `warn` when the file is damaged or can't be opened, with the fix.

## How

- `voice/speaker.py`: `Unreadable`, the `unchecked` verdict in `judge`, the three states in `Voiceprints` (`get`, and `profile()` for callers that want only a usable one).
- `spine/contract.py`: `Speaker` gains `unchecked`. `spine/confirm.py`: `Refusal.VOICE_UNCHECKED` and its detail, one branch in `decide`. `spine/router.py` is unchanged: its unsure count applies only to `voice_unsure`, and its private refusal covers every non-owner verdict.
- `api/session.py`: the host note for `unchecked` in `guest_note`. Everything else is the private path that already exists.
- `api/runtime.py`: no embedder for an unreadable voiceprint (the stub), and the one notice after `SecurityAlerts` is made. `security/alerts.py`: `voiceprint_unreadable`, and a line in her instructions on where it is put right.
- `api/voiceprint.py`: `GET /api/voiceprint` reports `enrolled: true`, `working: false` and `unreadable: "locked" | "damaged" | null`. `apps/web/src/voiceprint.ts` shows it.
- `cli/doctor.py`: `check_voice`, read-only.

## Tests

- `tests/voice/test_voiceprint.py`: `test_an_unreadable_file_is_nobody_enrolled` is replaced, deliberately, by `test_an_unreadable_file_is_someone_enrolled_and_fails_closed`, over the same damaged files: its meaning is what this reverses. Also: a missing file is still nobody enrolled; a folder in its place is unreadable and deep nesting is damage, not a crash; a locked file is `locked`, and read as it is on the next start; one deleted by hand stays unreadable until then; teaching again and forgetting put it right without a restart; it is logged once, without its contents.
- `tests/spine/test_speaker_gate.py`: `unchecked` refuses every spoken yes with the tap, the card stays and its tap works, a typed yes works, spend still needs the tap, and nothing personal runs.
- `tests/api/test_speaker_identity.py`: whole spoken turns with the fake embedder: the yes is refused twice with `voice_unchecked`, the host note is there, the tap approves, the damaged file is untouched; nothing personal for a spoken request; typing is the session's; teaching again and forgetting, without a restart; the notice found on start is told once and not again for spoken turns.
- `tests/api/test_voiceprint_api.py`: the state, re-enrolment and forgetting. `apps/web/test/voiceprint.test.ts`: the words, the buttons, teaching again and forgetting. `tests/unit/test_doctor.py`: the line. `tests/delivery/test_security_alerts.py`: the notice, with and without a model.

## Platforms

The same on macOS, Windows and Linux: the check is in the server. "Locked" is the system refusing to open the file: mode bits on macOS and Linux, its access list on Windows. The locked-file tests run on macOS and Linux only (they lock the file with mode 000, which Windows ignores). See [`docs/platforms.md`](../platforms.md).

## Not now

- **A deleted file.** A voiceprint deleted outside Trillion is a missing file on the next start, so nobody enrolled. Anyone who can delete it could also replace it, so this is the same limit as before, now written down. Recording "a voice is enrolled" somewhere else (the database) would catch it; not done here.
- A backup copy of the voiceprint, to restore from without teaching her again.
