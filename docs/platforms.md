# Platforms

What works on macOS, Windows and Linux, in a browser tab and in the desktop app, and why each gap is there. Every new spec has a Platforms section, and the pull request that adds it keeps this file current ([`CONTRIBUTING.md`](../CONTRIBUTING.md)).

## How to read this

- The server (`trillion run`) does the work on every system. Most features live there, so they work wherever it runs.
- The page runs in a browser tab on any system, or inside the desktop app. The desktop app is made for macOS (PRD SUR-2); it also builds for Linux (SUR-3b), untested by hand.
- CI runs every Python test directory on Ubuntu, macOS and Windows, the one-line installers on all three, a clean-machine install, setup and doctor on Ubuntu 22.04 and macOS, the TypeScript suites on Ubuntu and Windows, and the desktop app's Rust checks and build on macOS and Ubuntu 22.04 (`.github/workflows/ci.yml`).
- ✓ means the code is there and its tests run in CI on that system. The manual checks in [`TESTING.md`](TESTING.md) have been written on a Mac; on Windows and Linux a ✓ is untested by hand unless the row says otherwise.

## By feature

| Area | macOS | Windows | Linux | Browser tab (any OS) | Desktop app (macOS) |
|---|---|---|---|---|---|
| Server, web app, `setup`, `doctor` | ✓ | ✓ | ✓ | ✓ | ✓ |
| One-line installer (installs uv and Trillion, then runs `setup`) | ✓ `install.sh` | ✓ `install.ps1` (Windows PowerShell 5.1 in CI; PowerShell 7 untested) | ✓ `install.sh` | — | not installed by it |
| Keys in the system keychain | ✓ Keychain | ✓ Credential Manager | ✓ Secret Service (GNOME Keyring, KWallet) | as the OS | ✓ |
| Hearing, paid: Deepgram | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hearing, free: local Whisper | ✓ | ✓ | ✓ | ✓ | ✓ |
| Speaking, paid: ElevenLabs | ✓ | ✓ | ✓ | ✓ | ✓ |
| Speaking, free: the system voice | ✓ | ✓ | depends on the browser's voices | as the browser | ✓ WebKit's voices |
| Speaking, free: a local OpenAI-compatible voice (Kokoro) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Talk shortcut | ✓ | ✓ | ✓ | while the tab is in front | from any app (a combination) |
| Connectors: Google, Slack, Stripe, MCP | ✓ | ✓ | ✓ | ✓ | ✓, sign-ins in the default browser |
| Workspace Home: tiles, Connect buttons, Build my briefing | ✓ | ✓ | ✓ | ✓ (once a day per browser) | ✓, sign-ins in the default browser |
| Reminders, timed asks, routines | ✓ | ✓ | ✓ | ✓ (server-side) | ✓ |
| Background reads | ✓ | ✓ | ✓ | ✓ | ✓ |
| System notifications | ✓ `osascript` | ✓ PowerShell toast | ✓ with `notify-send` | ✓ (shown by the server) | ✓ |
| Speaking up: watchers, once per find, notes, her line | ✓ | ✓ | ✓ | ✓ (server-side) | ✓ |
| Speaking up: quiet hours, in a meeting | ✓ | ✓ | ✓ | ✓ | ✓ |
| Speaking up: the spoken notice | ✓ | ✓ | ✓ | while a page is open | ✓, with the floating orb |
| Speaking up: mic probe | ✓ macOS 14.2+ | ✓ | ✓ with `pactl` (PulseAudio 16+ or `pipewire-pulse`) or `pw-dump` | ✓, except a call in the same browser | ✓, except a call in a WebKit app |
| Speaking up: Focus probe | best effort, mostly can't tell | can't tell | ✓ GNOME, and desktops that report Do Not Disturb | as the OS | as the OS |
| Spend caps: the gate, prices, paused background work | ✓ | ✓ | ✓ | ✓ (server-side) | ✓ |
| Spend caps: the pill, Usage and limits, the card at the cap | ✓ | ✓ | ✓ | ✓ | ✓ |
| Spend caps: the spoken line at the cap | ✓ | ✓ | ✓ | while a page is open, with voice on | ✓ |
| Helpers panel: her routines, timed asks and watchers, live (a full-height sheet at phone width) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Helpers you make: create, talk, hand off, suggest, pictures, pause and delete | ✓ | ✓ | ✓ | ✓ | ✓ |
| Shared folders: shares, the card, Settings | ✓ | ✓ | ✓ | ✓ | ✓ |
| Shared folders: the folder browser | ✓ | ✓ | ✓ | ✓ (drawn by the page from the server's listing) | ✓ |
| Shared folders: the system folder picker | — | — | — | not available | ✓ `NSOpenPanel` |
| Desktop app | ✓ universal `.dmg`; signed and notarized in a release once the Apple secrets are set | not built (SUR-3c) | AppImage and `.deb` built on Ubuntu 22.04, never opened by hand (SUR-3b) | — | — |
| Your voice: enrolment, the speaker check, default-private ([spec](specs/2026-11-07-speaker-identity.md)) | ✓ | ✓, the file is not limited to the owner (no mode 600) | ✓ | ✓ (recorded by the page, judged by the server) | ✓ |
| Desktop app | ✓ unsigned dev build | not built (SUR-3c) | not built (SUR-3b) | — | — |

### The gaps, and why

- **No keychain on a headless Linux machine.** A key can't be saved in the app there. A local model still works. The encrypted-file store (SEC-2) is not built yet.
- **The system voice on Linux.** The page speaks with the browser's speech synthesis. Whether a Linux browser has voices depends on how it was built and what the system has installed. Untested.
- **The talk shortcut in a tab** works only while the tab is in front, and never with Fn. A page hears keys only while it has focus. The desktop app makes a combination work from any app.
- **A spoken notice needs a page.** The voice runs in the page. With no page open, the notice waits for the next page and a system notification shows instead.
- **System notifications on Linux** need `notify-send` (libnotify). Without it none is shown, and the notice still waits for the page. A silent one asks the notification daemon for no sound with a hint; a daemon may ignore it.
- **No system folder picker in a browser tab.** A browser never gives the page a folder's real path, so Add a folder opens the folder browser instead. The desktop app's picker (`rfd`) would be the common item dialog on Windows and the GTK dialog on Linux, but the desktop app is not built on Windows yet, and on Linux it is untested.
- **The folder browser's places differ by system** (`src/trillion/files/browse.py`): Home, Desktop, Documents and Downloads everywhere (Windows known folders, so a redirected Documents is found; on Linux `xdg-user-dir`, falling back to the English names); drives are `/Volumes/*` on macOS, drive letters with a disk on Windows, and `/media/<user>/*`, `/run/media/<user>/*` and `/mnt/*` on Linux. Hidden folders are a leading `.` everywhere, plus the hidden flag on macOS and the hidden or system attribute on Windows. Names compare without case on macOS and Windows and with case on Linux.
- **Folders that can't be shared** are the same rule on every system (`src/trillion/files/rules.py`): a home folder, another user's home, a drive root or mount point, system folders, Trillion's data folder, `~/Library` and `AppData` (iCloud Drive, folders under `~/Library/CloudStorage` and iCloud app folders excepted), hidden and protected folders. Under WSL, `/mnt/c/Users/<user>` is a folder on a mounted drive, not a home.
- **The one-line installer** (`install.sh`, `install.ps1`) installs the server with uv on each system, and CI runs it on all three (as a file and piped, twice). It does not install the desktop app on any system. `install.sh` is POSIX `sh`, so it runs under dash, bash and macOS's `/bin/sh`. On Linux it needs `curl` or `wget` only when uv is not already installed. It won't run as root (`TRILLION_ALLOW_ROOT=1` in a container). The `clean-machine` CI job runs it on Ubuntu 22.04 and macOS with an empty home and no uv, then checks `setup`, `doctor` and `run`.
- **The desktop app on Windows** is not built, and **on Linux** it builds but has never been opened by hand. See below.
- **The one-line installer** (`install.sh`, `install.ps1`) installs the server with uv on each system, and CI runs it on all three (as a file and piped, twice). It does not install the desktop app on any system. `install.sh` is POSIX `sh`, so it runs under dash, bash and macOS's `/bin/sh`. On Linux it needs `curl` or `wget` only when uv is not already installed.
- **Your voice on Windows.** The voiceprint file is written with mode 600, which Windows ignores; it relies on the user folder's own permissions there. onnxruntime ships wheels for all three systems, so the speaker check itself runs everywhere. CI runs only the synthetic tests: no model and no onnxruntime.
- **The desktop app on Windows and Linux** is not built. See below.
- **The mic probe and the Focus probe** have limits on each system. See the next section.

## Speaking up: the probes

Before she speaks a notice, she asks whether another app is using the microphone and whether Focus is on (`src/trillion/attention/probes/`). Each probe answers yes, no or can't tell. Can't tell never holds her voice. A probe runs with a 2 s limit and its answer is kept for 10 s. Settings → Speaking up shows each probe's status on this computer: works here, the reason it can't tell, or that it didn't answer.

| Probe | System | What it reads | Limits |
|---|---|---|---|
| Mic | macOS | CoreAudio's process list through `ctypes`: each process's pid, bundle id and whether it records input. No permission prompt. | macOS 14.2 or later; older says can't tell. |
| Mic | Windows | The registry the privacy indicator reads, under `HKCU\…\CapabilityAccessManager\ConsentStore\microphone`, packaged apps and `NonPackaged` by exe path. An app with a start time and no stop time is recording now. No admin. | Only apps Windows records there. A key it can't read → can't tell. |
| Mic | Linux | `pactl -f json list source-outputs` (PulseAudio, and PipeWire through `pipewire-pulse`), else `pw-dump`. Streams that record what is playing (monitors) or only meter the level don't count. | `pactl -f json` needs PulseAudio 16 or later, or `pipewire-pulse`; an older `pactl` falls back to `pw-dump`. Neither tool → can't tell. A recording of a speaker's monitor source, told apart only by its name, is left for later. |
| Focus | macOS | The Focus assertions file under `~/Library/DoNotDisturb/DB/`, if this process may read it. | Recent macOS guards the folder (Full Disk Access), so it is usually can't tell. A scheduled or automatic Focus leaves no assertion and is missed. |
| Focus | Windows | Nothing. | Windows has no documented signal a user process can read for Do Not Disturb, so it is always can't tell. |
| Focus | Linux | `gsettings get org.gnome.desktop.notifications show-banners` (`false` is on), else the `Inhibited` property of `org.freedesktop.Notifications` over `gdbus`. | A desktop with neither → can't tell. |

**Telling Trillion's own microphone apart.** The page says where it runs: the desktop app, or a browser family (Chrome, Edge, Firefox, Safari, other). The mic probe leaves out that host's own recording processes. Two blind spots follow:

- **Same browser.** When Trillion and a call share one browser, the probe can't see the call.
- **WebKit.** The desktop app and Safari both leave out every WebKit helper process. So a call in Safari while using the desktop app, or in another WebKit app while using Safari, isn't seen either.

The meeting rule covers both: with Google Calendar connected and "While you are in a meeting" on, a meeting on the calendar holds her voice whatever the probe says, even with meeting heads-ups off (the calendar is then read for this alone). Settings says so.

## Desktop app on Windows and Linux

The desktop app (`apps/desktop`) is made for macOS. It builds on Linux too (PRD SUR-3b): CI runs its Rust checks and tests on Ubuntu 22.04, and a release attaches an AppImage and a `.deb`. Nobody has opened it there yet, so every "check it" below still stands. A Windows build is part of SUR-3c, with the signed build. Each macOS-only piece, and what it needs on the other two:

| Piece | Today, on macOS | Windows needs (SUR-3c) | Linux (SUR-3b) |
|---|---|---|---|
| Build and CI | `tauri build --bundles app` and `cargo test`/`clippy` on `macos-latest` | A Windows job and an installer target | Done: `tauri build --bundles deb,appimage` and `cargo test`/`clippy` on `ubuntu-22.04`, off pull requests; the Release workflow attaches both |
| Timers in a hidden window (`webkit.rs`) | Turns off WebKit's private hidden-page timer throttling, so voice reports stay on time with the window hidden | A spike: does WebView2 throttle a hidden window's timers, audio or socket, and what turns it off | The same spike for WebKitGTK |
| Background throttling switch (`lib.rs`) | `BackgroundThrottlingPolicy::Disabled` on the main window | Check it has an effect on WebView2 | Check it has an effect on WebKitGTK |
| Floating orb (`orb.rs`, `macOSPrivateApi` in `tauri.conf.json`) | Transparent, frameless, always on top, never takes focus; over full-screen apps and every Space through AppKit calls | A transparent WebView2 window that does not take focus | A transparent window needs a compositor; check it on X11 and Wayland. The full-screen and every-workspace behaviour needs its own code on each |
| Tray icons (`tray.rs`) | Template images, so macOS tints them for the menu bar; idle, listening, speaking | Coloured icons that read on light and dark taskbars | A tray needs the AppIndicator library; coloured icons; some desktops show no tray at all |
| Finding `trillion` (`locate.rs`, `shell.rs`) | `TRILLION_BIN`, else the login shell (`$SHELL -lc`), else `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`. The login shell also gives the server the user's environment | No login shell: read the user's `PATH` from the environment and look for `trillion.exe` where the installer puts it (uv's tool folder) | An app started from a desktop menu may also lack the shell's `PATH`; the login-shell probe can work, but check it, and add the usual install places |
| Stopping the server (`server.rs`, `record.rs`) | The child gets its own process group; Quit sends SIGTERM to the group, then SIGKILL after 5 s | No process groups or signals: put the child in a Job Object that kills it with the app, and ask it to stop first (close its input, below) | As macOS (process groups and signals) |
| The app's own quit on a signal (`quit_on_signals`) | SIGTERM, SIGINT, SIGHUP quit the app and stop the server | The console-close and shutdown events instead | As macOS |
| Is the server running (`running.rs`) | `kill(pid, 0)` for a live process, `proc_pidinfo` and `getpgid` to know it | `OpenProcess` and its start time | Done: `/proc/<pid>/stat` and `/proc/stat` for the start time; `kill` and `getpgid` as macOS |
| The input lifeline (`server.rs`, `cli/server.py`) | The app holds the server's input open; when the app goes, even by a force quit, the input closes and the server stops itself | Check that the pipe closes on a force quit and the server's reader sees it | Expected to work as on macOS; check it |
| Global talk shortcut (`talk.rs`, `tauri-plugin-global-shortcut`) | The D6 rule: Command, Control or Option plus a key, not a common combination | Name the modifiers Ctrl, Alt and Win; leave out the combinations Windows keeps for itself | The plugin's Linux support is X11. On Wayland a global shortcut needs the desktop's own portal; until then, say it works only while the window is in front |
| Open at login (`tauri-plugin-autostart`) | A LaunchAgent | The plugin's registry entry; check it | The plugin's autostart file; check it |
| Microphone permission | `NSMicrophoneUsageDescription` in `Info.plist`, and the `audio-input` entitlement for a signed build (`Entitlements.plist`); the webview's request is granted for the server's page only | WebView2's permission request, granted the same way; Windows' own microphone privacy switch | WebKitGTK's permission request, granted the same way; the `.deb` depends on `gstreamer1.0-plugins-good`, which WebKitGTK records through. The AppImage uses the system's GStreamer; check it |
| The mic probe's host | Leaves out the app and WebKit's helpers | Already names `msedgewebview2` and the app's exe | Already names `webkitwebprocess` and the app's binary |
| Signing | A release signs with a Developer ID, notarizes and staples, once the Apple secrets are set ([`releasing.md`](releasing.md)); otherwise unsigned and not attached | Signed installer (SUR-3c) | Not signed: AppImage and `.deb` as built. A signed apt repository is for later |
| The Dock icon (`RunEvent::Reopen` in `lib.rs`) | Clicking it shows the window | — | No such event: macOS only in the code; the tray shows the window |

The page side needs no change: `desktop.ts` is the same on every system, and the server already runs everywhere.
