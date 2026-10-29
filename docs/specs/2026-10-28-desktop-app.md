# Desktop app

**Date:** 2026-10-28 · **Requirements:** SUR-2 (unsigned dev build), SPN-11a, SUR-6 · **Builds on:** the web app (`apps/web`), the talk shortcut (`apps/web/src/hotkey.ts`), the running note (`src/trillion/build.py`)

Trillion as a Mac app instead of a browser tab. A thin Tauri v2 shell around the web
app the user already has: it starts the server, shows a splash orb until it answers,
lives in the menu bar, and makes the talk shortcut work from any app. The page code is
the same page; the desktop adds only what a tab cannot do.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Start the installed server.** The app runs the user's installed `trillion run` as a child and stops it on quit. No Python inside the app yet. | What the user chose. Small app, always the user's code. Bundling waits for the signed build. |
| D2 | **The app makes the token.** It generates a session token and hands it to the child in `TRILLION_SESSION_TOKEN`; the server uses it instead of making one. Nothing is parsed from output and nothing is written down (SPN-11a). | A token only the app and its child know, in memory only. |
| D3 | **One server.** If a server is already running (the running note names a live process), the app does not start a second (one writer) and cannot open it (its token is not written down). The splash says so: "Trillion is already running from a terminal" with **Open in browser** (its port) and **Retry**. | One writer to the database; no token on disk. |
| D4 | **Closing hides.** The red button hides the window; she keeps running in the menu bar. Quit (menu bar or ⌘Q) stops her. | What the user chose. |
| D5 | **Talk from any app without switching.** The recorded talk shortcut becomes global. While the main window is hidden or behind, a small floating orb shows listening, thinking and speaking, with a caption line. | What the user chose. |
| D6 | **A global shortcut is a combination.** It must hold Command, Control or Option plus a key. A single modifier or a plain key (Space, the default in a tab) stays tab-only, and Settings says: "To talk from any app, record a combination such as ⌥Space." | macOS global shortcuts cannot be a bare modifier or Fn without Accessibility permission, and a global Space would break typing everywhere. |
| D7 | **Sign-ins open in the user's browser.** Google, Slack, Stripe and MCP sign-ins go to the default browser; the server's loopback redirect finishes them as today. | PRD SUR-2; the user's own browser holds their sessions and passwords. |

## Starting

1. The main window opens on a splash bundled in the app (`apps/desktop/ui/splash.html`): the orb, "Starting Trillion…".
2. Read `running.json` from the data folder (`TRILLION_HOME`, else `~/.trillion`). A live pid → D3.
3. Find `trillion`: `TRILLION_BIN` if set; else ask the login shell (`$SHELL -lc 'command -v trillion'`, since apps opened from the Finder do not get the shell's PATH); else `~/.local/bin/trillion`, `/opt/homebrew/bin/trillion`, `/usr/local/bin/trillion`. Not found → the splash says "Trillion isn't installed" with the install command and Retry.
4. Pick the port: 7777, else the first free one from 7778 to 7799.
5. Run `trillion run --no-open --port N` with `TRILLION_SESSION_TOKEN` set, the user's login-shell environment otherwise. Keep the last 40 lines of its output.
6. Poll `GET http://127.0.0.1:N/` every 250 ms, for at most 30 s. Answered → navigate the main window to `http://127.0.0.1:N/#token=…`. The child exits or time runs out → the splash shows "Trillion couldn't start", the last lines of output, and Retry.
7. Quit stops the child (SIGTERM, then SIGKILL after 5 s). If the child dies while running, the window shows the splash's stopped state with Restart.

## Windows and the menu bar

- **Main window:** the web app, 1200×800, remembered size and place. Close hides.
- **Menu-bar icon** (template image): Open Trillion, Talk (the shortcut shown), Open at login (a checkbox, off by default, `tauri-plugin-autostart`), Quit Trillion. The icon has three states: idle, listening, speaking.
- **Floating orb** (`orb` window): about 220×120, no frame, transparent, always on top, top-right under the menu bar, not in the Dock or ⌘Tab. Shown when a hotkey conversation starts while the main window is hidden or not focused; hidden 3 s after she finishes. Clicking it shows and focuses the main window. It loads `orb.html` from the server (a second Vite entry in `apps/web`), which draws the orb state and one caption line from events the main page sends.

## The bridge

The page talks to the shell through `apps/web/src/desktop.ts`, which uses Tauri's global (`withGlobalTauri`) so the web app gains no dependency. Outside the app every call is a no-op and `DESKTOP` is false.

- `DESKTOP: boolean`; `<html data-shell="desktop">` set at load.
- `openExternal(url)`: the shell opens it in the default browser (`tauri-plugin-opener`). The page's sign-in buttons use a helper, `signInTab()`, that in a tab keeps today's blank-tab-then-navigate trick (pop-up blockers) and in the app opens the URL in the browser once it is known.
- `setTalkShortcut(hotkey | null, mode)`: the shell registers it globally (`tauri-plugin-global-shortcut`) if it qualifies (D6), and answers `{global: boolean}`. Called at load and whenever the user records a new one.
- `onTalk(handler)`: the shell's `talk` event, `{state: "down" | "up"}`. The page feeds it into the same hotkey logic as a key event, **only when the page does not have focus** (with focus the page already heard the key).
- `reportState(state, caption)`: the page sends orb state (`idle`, `listening`, `thinking`, `speaking`) and her current caption; the shell updates the tray icon and forwards to the orb window, which it shows or hides by D5.
- Capabilities: the remote origin `http://127.0.0.1:*` may call only these commands and events. No shell, no file system, no other plugin is exposed to the page.

## The page in the app

- No "keep this tab in front" hints, no "open in browser" hints.
- Settings, talk shortcut: in the app, a line under it says whether it works from any app, or the D6 sentence.
- Microphone permission: `NSMicrophoneUsageDescription` = "Trillion listens when you talk to her." The webview's capture request is granted for the server origin only.

## Server change

- `Runtime.token` uses `TRILLION_SESSION_TOKEN` when set and at least 32 characters of `[A-Za-z0-9_-]`; otherwise refuses to start ("TRILLION_SESSION_TOKEN must be at least 32 URL-safe characters") rather than fall back silently. Unset: a fresh token as today. Never logged, never written (SPN-11a).
- `trillion run` prints the open link only when it made the token itself.

## The risk, checked first

The voice session runs in the main window's page. A hidden macOS webview may suspend the microphone, audio playback or timers. The first task is a spike: build the shell, hide the window, talk by the global shortcut, and confirm she hears, answers aloud (muted test: audio events, not sound) and can be interrupted. If hidden fails, keep the window "hidden" by moving it off-screen and out of the window list instead. If that fails too, stop and report before building the rest.

## Build and CI

- `apps/desktop`: `src-tauri` (Rust crate `trillion-desktop`), `ui/splash.html`, `package.json` with `@tauri-apps/cli` scripts `dev` and `build` (`--bundles app`, unsigned).
- Pure Rust units with tests: finding the binary (given a fake shell answer and a fake file set), reading the running note, picking a port, the shortcut rule (D6) and mapping a page hotkey to a global shortcut string, the stop sequence.
- CI: a `Desktop (macos-latest)` job runs `cargo test` and `cargo clippy -D warnings` in `apps/desktop/src-tauri` and `tauri build --bundles app` (no signing, not run).
- `docs/TESTING.md`: manual checks below. `docs/architecture.md`: rows for `apps/desktop` and `desktop.ts`.

## Out of scope

Signing and notarizing, bundling Python, Linux and Windows builds, auto-update, the wake word, a single-modifier or Fn shortcut (needs Accessibility), opening a server started elsewhere.

## Testing

- **Rust:** the units above.
- **Python:** the token from the environment is used, a short or malformed one refuses to start, unset makes a fresh one; the open link printed only for a token it made.
- **Web:** `desktop.ts` with a stubbed global: no-ops outside the app; `signInTab` in both modes; `onTalk` ignored while the page has focus and fed to the hotkey logic otherwise; the D6 rule shown in Settings; `reportState` sent on orb changes; the orb page renders state and caption from events.
- **Manual (the user's Mac):** open the app with no server running (splash, then the app); with a terminal server running (D3); with `trillion` missing (the install message); close the window and talk with ⌥Space from another app (the floating orb, she answers, barge-in still works); sign in to Google (opens in the browser, finishes); Quit stops the server; Open at login.
