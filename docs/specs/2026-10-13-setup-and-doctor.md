# `trillion setup` and `trillion doctor`

**Date:** 2026-10-13 · **Requirements:** ONB-2, ONB-2a, ONB-5, SEC-4, SEC-9, SUR-3a (in part), OPS-2 (the warning) · **Phase 0 items:** 1, 11

Two commands for the moment something does not work: one that prepares and starts, one that looks and explains.

## `trillion doctor`

It checks the machine and prints one line per check, with a second line saying what to do when something is wrong.

```
ok    Database     up to date (5 migrations)
warn  Port         7777 is in use by something else
                   -> Stop what is using it, or start on another: trillion run --port 7778
```

| Check | What it looks at |
|---|---|
| Version | The Trillion and Python versions. Python older than 3.13 fails. |
| Data folder | Exists and can be written. Warns if it sits inside OneDrive, Dropbox, Google Drive or iCloud (OPS-2). |
| Database | Opens read-only, passes SQLite's quick check, and its migration history matches this build. Newer than this build, or a changed history, fails. |
| App page | The built page the browser opens. |
| Port | Free, or already held by Trillion itself, or held by something else (a warning, with the way round it). |
| Keychain | Whether the operating system has one, and which. On Linux the fix names the Secret Service. |
| Model | The chosen model answers and lists the model that was chosen. |
| Voice, Hearing | The chosen provider answers. Local Whisper not installed, or its model not downloaded, is named with its fix. |
| Microphone | On Linux, that PipeWire or PulseAudio is present. Elsewhere it says the browser opens the microphone. |
| Names, Memory | Whether the user has introduced themselves, how many memories there are, and any file that could not be read. |

**It only looks.** Nothing is created, no migration is applied, and the database is opened read-only. A test checks that the file's modification time does not change.

**The network.** The only requests go to the providers the user already chose, asking what they offer. That costs nothing and proves a key works. Each waits at most eight seconds. `--offline` skips them all and says "not tested".

**`--secrets` (SEC-9).** One more line per place a key can live: not set, or its length and last four characters and whether it works. A value is never printed, and a key shorter than twenty characters shows only its length (SEC-4).

**Exit code.** 0 when nothing fails, 1 when something does. A warning does not fail.

## `trillion setup`

1. **Prepare.** Make the data folder and its subfolders, open the database and apply migrations. All of it is local and needs no credential (ONB-2a), and every step is safe to repeat, so a setup that was interrupted is finished by running it again.
2. **Check.** The same checks as `doctor`.
3. **Start.** If nothing failed, start the app exactly as `trillion run` does, with the same options. If something failed, say so and stop with exit code 1. `--no-run` stops after the checks.

## Checked

- `tests/unit/test_doctor.py`: a fresh machine is healthy and nothing is created; the synced-folder warning; a database from a newer build, a file that is not a database, and that doctor never writes; a port held by something else; a model that answers, one that does not, one that is not listed, a refused key and a missing key; offline; Whisper not installed; names and memory; `--secrets` output never containing a key; exit codes; and setup stopping when something is broken.
- By hand on macOS: `doctor --secrets` against a real install (a local model, local Whisper, and a real ElevenLabs key shown as "51 characters, ending in …, works"); `doctor` on a machine with no data folder; `setup` on an empty folder, which prepared, checked and started the app; and `setup` again, which reported nothing to do.

## Not checked

- Linux and Windows by hand. The Linux audio and keychain lines are written from the PRD and have not been seen on a Linux machine.
- A paid model key against the real service.

## Known limits

- **The microphone is not tested.** The browser opens it, so the command line cannot. On macOS and Windows the line is information only.
- **A custom service's key is shown but not tested by `--secrets`**, unless that service is the model in use, in which case the Model line tests it.
- **No model files are fetched by setup (ONB-7).** The only model file today is local Whisper, which the user downloads from the Mic settings.
- A port held by an older Trillion that answers differently is reported as "something else".

## Left for later

- `doctor --trace`: timings for the last turns and the running build against the installed files (ONB-11).
- `doctor --egress`: every destination the current settings can reach.
- The encrypted-file secret store for machines with no keychain (SEC-2).
