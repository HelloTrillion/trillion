# Security alerts: she tells you who is getting in

**Date:** 2026-11-07 · **Requirements:** SEC-11, SPN-11, SPN-11a, SUR-9 (the door), VOI-17, PER-11 · **Builds on:** [speaking up](2026-10-29-speaking-up.md) (notices, the quiet check), [reminders](2026-10-24-reminders.md) (the door)

When a new device signs in, when something keeps knocking with the wrong key, or when
someone not on the list writes to her on Slack or Telegram, she says so, in her own
words, through the one door. A device she knows is silence. Settings lists the devices
she knows, and the user can forget one.

## What exists today, and what counts as an event

| Where | What happens | Event? |
|---|---|---|
| A page's `hello` with the right token | A session starts. | **Yes, the first time a device is seen.** A known device is silence. |
| A `hello` with no token or the wrong one | Told "unauthenticated", closed (4401). | Counted. Only **repeated** refusals are an event. |
| A socket or request from another origin, or with another `Host` | Refused (403 / 4403). | Counted, as above. |
| An API request with no token or the wrong one | Refused (401). | Counted, as above. |
| Slack or Telegram: someone not on the allow-list writes | No answer; listed in Settings to allow or ignore. | **Yes, the first time that sender writes** (in this run). |
| Pairing a remote device | Does not exist yet: the server binds to `127.0.0.1` and there is no tunnel. | Not now. A pairing attempt becomes one more event kind when the tunnel lands. |
| The keychain fails | Each caller handles it where it happens. | Not now. |

The token is fresh each launch (or the desktop app's own). A browser tab left open over a
restart offers its old token once, is refused, and stops: one refusal, never an alert.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **A device is the page's own id, never its address.** The page keeps a random id in `localStorage` and sends it in its `hello`, with its host family (`desktop`, `chrome`, …). The server keeps only a hash of it. No IP or network is ever read. | SEC-11: an address change from the internet provider must not turn the owner into a stranger. Everything here is loopback anyway. |
| D2 | **The first device ever is the owner's.** With no device known (none ever, forgotten ones included), the first one is kept silently. After that, a device not known is an event. A `hello` with no id counts as one device per host family ("no id, Chrome"). | Setup should not end in an alarm. Leaving out the id must not hide a sign-in. |
| D3 | **Through the one door, as a request to deliver.** Each alert is a notice (source `attention`, key `security:<id>`) handed to the door, so it never interrupts a live reply (VOI-17). | SUR-9. |
| D4 | **Her words, from host facts.** One small model call, no tools, the facts as host data (what happened, which host family, how many refusals); a channel sender's name is someone else's words, so it is wrapped as untrusted. The line passes the speaking-up guard (`words.clean`). With no model, at the hard spend cap, or no usable answer, the notice is shown by its title and its host detail, unspoken, with a system notification. | PER-11: her words or silence, never stock speech. Same rule as a watcher's heads-up and an interrupted send. |
| D5 | **Critical.** A security notice is never held by quiet hours (the other reasons still hold her voice, never the notice), the page marks it as a security notice, and when it is not spoken the computer shows it too. | SEC-11 says `critical`. |
| D6 | **A flood is one message.** Events within a few seconds go out as one notice, and after one is told the next waits at least five minutes, gathering everything meanwhile into one. Refusals alert only when five separate attempts (refusals within two seconds are one attempt) land within ten minutes, and then at most once an hour. | A flood that becomes a hundred lines trains the user to ignore her. |
| D7 | **Forget is soft.** Forgetting a device hides it from the list and makes it unknown again: its next sign-in is an event, and it comes back. | The user can undo it by signing in, and it is never lost silently. |

## What the user sees

- Opening Trillion in a new browser: a moment later, when she is not mid-reply, a line
  such as "Firefox on this computer just signed in for the first time. If that wasn't
  you, forget it in Settings, Devices." It is shown, spoken unless she is holding her
  voice, and marked as a security notice.
- Settings → **Devices**: each known device by its host family, when it was first and
  last seen, "This device" beside the one in use, and **Forget**.
- Someone not on the list writes on Telegram: one line saying so, pointing to Settings
  to allow or ignore them.

## How

- `src/trillion/security/devices.py`: the device book, a small JSON file
  (`devices.json` in the data folder), written atomically, mode 600. Each entry: the
  hash, host family, client, first and last seen, forgotten.
- `src/trillion/security/alerts.py`: `SecurityAlerts`, the one producer. `signed_in`,
  `refused` and `knocked` note events; a batch is composed and written as a notice,
  then handed to the door. It never touches a surface (a structure test checks it).
- `api/app.py` reports sign-ins and refusals; `channels/manager.py` reports a new knock.
- `GET /api/devices`, `POST /api/devices/forget`.

## Not now

- Pairing and remote devices: no tunnel exists yet.
- Knocks are remembered per run (the gate keeps them in memory), so the same stranger
  writing again after a restart is told again, at most once per five minutes.
- A keychain failure as an event.
- The security mark survives in the page only until it reloads: the saved note keeps the
  title, which says it.
