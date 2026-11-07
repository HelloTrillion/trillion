# Installers and desktop builds

**Date:** 2026-11-07 · **Requirements:** ONB-1, SUR-3b, Phase 0 definition of done item 1, the signed Mac build · **Builds on:** [the desktop app](2026-10-28-desktop-app.md), [`docs/releasing.md`](../releasing.md)

Phase 0 is done when, on a clean Mac and a clean Ubuntu 22.04, `uv tool install trillion && trillion setup` succeeds and `trillion doctor` is green. The one-line installer (`install.sh`, `install.ps1`) already exists and CI runs it, but nothing checked that promise end to end on a clean machine. The desktop app is built on macOS only, unsigned, and is not in releases.

## Decisions

1. **One clean-machine job is the check for item 1.** CI builds the wheel, then, with an empty home and a PATH of `/usr/bin:/bin` only, runs `install.sh`. It has to install uv with Astral's installer, install Trillion, and run `trillion setup`. Then `trillion doctor` must exit 0 with no `fail` line, and `trillion run` must serve the page. Ubuntu 22.04 on every pull request; macOS on main, nightly and on demand, as every Mac job (runners are scarce).
2. **Free, with no keys.** No model, no voice, no key: the ladder's first rung. Doctor says "none chosen yet" for those, which is not a failure. A runner with no keychain gets a warning, which is not a failure either.
3. **Setup needs no new mode.** It never asks anything. `trillion setup --no-run` prepares and checks, then stops instead of serving. The installer passes anything after `--` to `trillion setup`, so CI runs `sh install.sh --wheel … -- --no-run --no-open`.
4. **The installer won't run as root.** Trillion and its data belong in a person's home folder. As root they would land in root's, and the app would run as root. It stops and says to run it as yourself. `TRILLION_ALLOW_ROOT=1` lets it run in a container where root is the only user. `install.ps1` is unchanged: on Windows it already needs no administrator rights.
5. **Linux desktop build: AppImage and `.deb`, on Ubuntu 22.04.** The oldest supported system, so the AppImage's glibc is one every supported system has. The `.deb` depends on WebKitGTK 4.1, GTK 3 and AppIndicator (Tauri adds these) and on `gstreamer1.0-plugins-good`, which WebKitGTK needs to open the microphone. The release attaches both.
6. **The Rust builds on Linux.** Two macOS-only pieces stopped it: `RunEvent::Reopen` (the Dock icon) and `proc_pidinfo` for a process's start time. The first is macOS only now. The second reads `/proc/<pid>/stat` and `/proc/stat` on Linux.
7. **Mac: signed and notarized when the owner has set it up.** Six repository secrets, named as Tauri reads them: `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY` to sign, and `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` to notarize. Tauri imports the certificate into a temporary keychain, signs with the hardened runtime, notarizes and staples. With some missing, the build goes on unsigned (or signed but not notarized), and the log says which names are missing, never their values.
8. **Only a signed and notarized `.dmg` goes in a release.** An unsigned app downloaded from the web is blocked by Gatekeeper, and on Apple silicon it is called "damaged". An unsigned build is kept on the run as `…_unsigned.dmg` for testers.
9. **Universal Mac build.** One `.dmg` for Apple silicon and Intel.
10. **The microphone under the hardened runtime.** A signed app needs the `com.apple.security.device.audio-input` entitlement as well as `NSMicrophoneUsageDescription`, or the microphone is silently denied. `Entitlements.plist` adds it. An unsigned build ignores it.
11. **The release build uses no Rust cache.** Whatever ships is compiled from the tagged source only.

## What the user sees

- `curl -fsSL …/install.sh | sh` as before. Run with `sudo` or as root, it stops: "don't run this as root or with sudo. Run it as yourself…".
- On a release page: `Trillion_<version>_amd64.AppImage` and `Trillion_<version>_amd64.deb`, and, once the Apple account is set up, `Trillion_<version>_universal.dmg`.

## How

- `install.sh`: `--` ends its options; the rest goes to `trillion setup`. A root check after the options.
- `.github/workflows/ci.yml`: the `clean-machine` job, and a `desktop-linux` job (clippy, tests and both bundles on Ubuntu 22.04; not on pull requests, as the Mac desktop job).
- `.github/workflows/release.yml`: `desktop-linux` and `desktop-mac`, after the wheel job (it makes the release and checks the tag). Each stamps the version from `pyproject.toml` with `tauri build --config`, keeps its files on the run, and attaches them with `gh release upload --clobber`, so a job can be re-run.
- `apps/desktop`: `bundle:linux`, the deb's dependency, the descriptions, `Entitlements.plist`.
- `docs/releasing.md`: what the owner sets up with Apple and GitHub.

## Platforms

| | macOS | Linux | Windows |
|---|---|---|---|
| Clean-machine check | ✓ main, nightly, on demand | ✓ Ubuntu 22.04, every pull request | — (the installer job covers `install.ps1`) |
| Installer refuses root | ✓ | ✓ | n/a |
| Desktop build in a release | ✓ signed and notarized only | ✓ AppImage, `.deb` | not built (SUR-3c) |

## Not now

- **Bundling Python in the desktop app.** It still starts an installed `trillion`, and says how to install it if there is none. The installer does not install the desktop app.
- **Testing the Linux app by hand.** CI builds it but never opens it. The tray, the floating orb on X11 and Wayland, the global shortcut on Wayland and the microphone through WebKitGTK are untested ([`docs/platforms.md`](../platforms.md)).
- **Signing for Linux** (a GPG-signed apt repository, AppImage signatures), an `.rpm`, Flatpak.
- **The wake-word and embedding models in the installer** (ONB-1 at P1; the wake word is deferred), and Homebrew.
- **Installing from PyPI on a clean machine.** CI installs the wheel it built. After a release, run the one-liner once on a clean machine.
