# Trillion for Mac

Trillion as a Mac app instead of a browser tab. It is a thin Tauri v2 shell around the same web app. It starts your installed `trillion run`, shows a splash orb until the server answers, lives in the menu bar, and makes the talk shortcut work from any app.

This is an unsigned development build. Signing, notarizing and bundling Python come later. The design is in [`docs/specs/2026-10-28-desktop-app.md`](../../docs/specs/2026-10-28-desktop-app.md).

## Run in development

You need Rust, pnpm and uv. Build the web app first, since the server serves it:

```sh
pnpm -C apps/web build
pnpm -C apps/desktop dev
```

`dev` sets `TRILLION_BIN` to `scripts/trillion-dev`, which runs this checkout's `trillion` with `uv run`. The app starts it the same way it starts an installed one.

## Build

```sh
pnpm -C apps/desktop bundle
```

The app lands at `apps/desktop/src-tauri/target/release/bundle/macos/Trillion.app`. It is not signed, so the first time you open it, right-click it and choose Open.

## Where it looks for `trillion`

1. `TRILLION_BIN`, if set.
2. Your login shell (`$SHELL -lc 'command -v trillion'`). Apps opened from the Finder do not get your shell's PATH, so the app asks the shell.
3. `~/.local/bin/trillion`, `/opt/homebrew/bin/trillion`, `/usr/local/bin/trillion`.

If none is found, the splash says "Trillion isn't installed" with the install command and Retry.

If a server is already running from a terminal, the app does not start a second one. The splash says so, with Open in browser and Retry.

## The talk shortcut

To talk from any app, the shortcut must be a combination: Command, Control or Option plus a key, such as ⌥Space. Shift may be added. A single key, a single modifier or Fn works only while the window is in front. Common combinations that every app uses, such as ⌘Space or ⌘C, are not taken. Settings says whether your shortcut works from any app.

macOS may accept a shortcut that another app already uses, so Settings can say it works from any app while the other app still gets the keys. If it does nothing, record a different combination.

While you talk from another app, a small floating orb shows her state and a caption. It never takes focus, so the app you are in stays in front.

## Closing and quitting

Closing the window hides it. She keeps running in the menu bar.

Quit (menu bar or ⌘Q) stops the server: SIGTERM to its process group, then SIGKILL after 5 s.

The app holds the server's input open and never writes to it. If the app goes away, even by a force quit, the input closes and the server stops itself within a few seconds. The app also keeps a note of the server it started (no token), so the next launch can stop one left behind.

## The session token

The app makes the session token and hands it to the server in `TRILLION_SESSION_TOKEN`. It is kept in memory only and never written down. Before it shows the page, the app checks `/api/desktop/hello`, which only a server holding that token can answer.

## macOSPrivateApi

`tauri.conf.json` turns on `macOSPrivateApi`, which the transparent floating orb needs. Apps that use it cannot go on the Mac App Store. Trillion is distributed outside it.
