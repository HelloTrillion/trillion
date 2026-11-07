# Windows hardening

**Date:** 2026-11-07 · **Requirements:** SUR-3c, with SEC-1, SEC-2, SEC-4, SEC-9, ONB-5, OPS-3

## Why

The core and the browser UI already run on Windows and are tested there. What was left was the Windows-specific work: keys in Credential Manager that really fit, private files that really are private, a `doctor` that can see the microphones, and the desktop app built and signed for Windows.

## Decisions

1. **Credential Manager stays the store, through `keyring`.** On Windows `keyring` picks `WinVaultKeyring` (priority 5), which `doctor` names "Windows Credential Manager". When `keyring` chains several backends, `doctor` names the first, where a new key goes.
2. **Long values in pieces.** Credential Manager keeps at most 2,560 bytes an entry, and `keyring` writes UTF-16, so a sign-in's tokens (an MCP server's, a Google account's) could not be saved. On Windows a value over 640 characters is kept in entries `<name>~piece-1`, `~piece-2`, …, and its own entry holds `trillion-split:v1:<n>`. A shorter value written later removes the old pieces; a missing piece reads as no key, never half of one.
3. **Every keychain error is caught.** Credential Manager raises its own error type, not `keyring`'s. The store now catches any failure, logs the key's name and the error's class, and never the value (SEC-4).
4. **Owner-only on Windows through the security API.** `trillion.owner_only.restrict(path)` gives a path a protected access list: the user and SYSTEM, full control, nothing inherited. A folder's entries pass to what is made in it, and Windows passes them down to what is already there. It uses `ctypes` (`SetNamedSecurityInfoW`): no process is started, nothing is installed, and it is fast enough to run on every database open. Off Windows it does nothing; the modes stay where they were.
5. **Where it is called.** The database's folder (the data folder) and the database file on every open, and the voiceprint's temporary file before the voice is written into it. The `-wal` and `-shm` files inherit the folder's list. The encrypted-file store (SEC-2) is not built yet; when it is, its file goes through the same call.
6. **A refusal is logged, not raised.** A drive without access lists (FAT, exFAT, some shares) still works, only unprotected.
7. **`doctor` says who else can open the data folder, on every system.** The mode on macOS and Linux, the access list on Windows. SYSTEM, administrators and the owner don't count.
8. **The microphones through Core Audio.** On Windows `doctor` lists the active recording endpoints and the default one through the MMDevice enumerator (what WASAPI is built on), by COM through `ctypes`. It reads names only: no microphone is opened, nothing is played. Before that it reads the microphone privacy switches from the registry, since a switch that is off explains everything.
9. **NSIS, not MSI.** The installer installs for the current user without an administrator, as the server itself does (uv in the user's folder). WiX's MSI also can't take a major version above 255, which the coming CalVer versions (2026) break.
10. **Signing with Azure Artifact Signing.** A publicly trusted code-signing key must live in hardware since 2023, so there is no `.pfx` to put in a secret. Microsoft's own action signs the program, then the installer built around it, with six secrets. Without them the installer is built unsigned, named `…_unsigned.exe`, and kept on the run only, as the Mac rule.
11. **The server starts hidden and ends with the app.** `CREATE_NO_WINDOW` (OPS-3), started suspended, put in a Job Object that kills everything in it when the app ends however it ends, then let run. Quit closes its input first (the lifeline), and ends the job after 5 s. The release build is a windowed program, so no console opens beside the app either.

## What the user sees

- Signing in to an MCP server or a Google account on Windows now saves.
- `trillion doctor` has a "Data folder access" line ("only you can open it", or who else can), and on Windows the Microphone line lists the microphones and the default, or says which privacy switch is off.
- A Windows installer on the release page, once the signing is set up.

## How

- `src/trillion/secrets/store.py`: pieces and broader error handling in `KeychainStore`.
- `src/trillion/owner_only.py`: `restrict`, `exposed`, the SDDL and its reading.
- `src/trillion/storage/db.py`, `src/trillion/voice/speaker.py`: call `restrict`.
- `src/trillion/cli/windows_audio.py`, `src/trillion/cli/doctor.py`: the new lines.
- `apps/desktop/src-tauri/src/winproc.rs`: the Job Object, process times; `server.rs`, `running.rs`, `locate.rs`, `shell.rs`, `record.rs`, `lib.rs` made to build on Windows. `icons/icon.ico` added.
- `.github/workflows/ci.yml` `desktop-windows` (off pull requests), `.github/workflows/release.yml` `desktop-windows`.
- Tests: `tests/unit/test_windows_hardening.py` fakes Credential Manager, the access-list call, Core Audio and the registry on every system; tests marked for Windows use the real ones in the Windows CI job.

## Platforms

Windows only, except the "Data folder access" line in `doctor`, which reads the mode on macOS and Linux. `docs/platforms.md` is updated.

## Not now

- The encrypted-file store (SEC-2) itself.
- Opening the desktop app on Windows by hand: the WebView2 throttling spike, the floating orb, tray icons on a light taskbar, the talk shortcut's modifiers.
- Signing the NSIS uninstaller (it is made inside the installer).
- Recognising a server left behind by the Windows app: the Job Object means none is.
