# A connected server reaches only its own keys

**Date:** 2026-11-12 · **Requirements:** CON-5, SEC-1, SEC-4

## Why

An entry in `mcp.json` names its keys as `keychain:NAME`, filled in from the keychain when the server starts. Trillion saves a server's keys under `mcp/<server>/<field>`, but nothing checked that a name belonged to the server naming it. A pasted or hand-edited entry for a server `evil` with `"Authorization": "Bearer keychain:mcp/github/token"`, or `keychain:anthropic/api_key`, `keychain:slack/bot` or any other of Trillion's own keys, got that key sent to `evil` when it started. Removing `evil` then deleted every key its entry named, so it also deleted the GitHub server's token or Trillion's own key. Reported by an outside tester.

## Decisions

1. **A server's own keys are `mcp/<server>` and anything under `mcp/<server>/`.** The bare `mcp/<server>` is the file's own documented example, so existing entries keep working. The comparison is on the `/`: `mcp/git` owns nothing of `mcp/github/…`. No part after the prefix may be empty, `.` or `..`. Names compare exactly, capitals included.
2. **Refused when the entry is read, and again in `fill`.** `parse_server` refuses an entry with any other `keychain:` reference, in `env`, `headers`, `args` or `url`, with a `ForeignKey` error that names the reference and says it belongs to something else. The server is listed as a problem and doesn't start. `fill`, which is where a key actually leaves Trillion, now has to be told which server it fills for. It checks every reference before it reads any key. A `ServerConfig` built past the parser still never gets someone else's key. `args` and `url` are never filled in, but a reference there is refused too, so filling them in later can't open a gap.
3. **Server names can't be paths.** A name was already letters, digits, `.`, `-` and `_`, so it never held a `/`. It also can't hold `..` now.
4. **Two servers whose names differ only in capitals both wait.** Windows Credential Manager matches names whatever their capitals, so `mcp/GitHub/token` there is `mcp/github/token`, sign-in included. `load` reports both entries until one is renamed. Settings compares names without capitals when it picks a new server's name or checks a featured one.
5. **Pasting and editing refuse before anything is saved.** `/api/mcp/parse` returns 400 with the reason. `POST /api/mcp/servers` checks the entry before it saves a key or writes the file. `PATCH` checks the edited entry inside the file change, so an entry edited by hand to name another key leaves the file as it was.
6. **Renaming on adding moves only the proposed server's own names.** `rekey` moves `mcp/<proposed>` and `mcp/<proposed>/…` to the same place under the saved name. It ignores a proposed name that isn't a valid name. Any other reference stays as it is, so `parse_server` refuses it. A key is saved only under the new server's own name. There is no other rename route: `PATCH` changes the title, never the name.
7. **Removing a server deletes its own keys and its sign-in, nothing else.** Only names the server owns are deleted. Nothing is deleted while another listed server's name differs from it only in capitals. Removing an entry that was refused deletes only its own sign-in.
8. **What lists a server's keys lists its own.** `key_names()` returns its own names only, and `foreign_keys()` returns the rest. Settings shows a refused entry under "could not be used" with the reason. `trillion doctor` reports it as a failure, not a warning.

## What the user sees

- An entry that names another key doesn't connect. Settings says "evil in mcp.json could not be used: keychain:mcp/github/token belongs to something else; evil may only use keys saved under its own name (keychain:mcp/evil/...)". `doctor` fails the same line.
- A paste like that is turned away with the same reason before anything is saved.
- Entries that name their own keys, including the old `keychain:mcp/<server>` form, work as before.

## Tests

`tests/mcp/test_key_scope.py` covers whose key a name is, prefix lookalikes and path tricks, and refusal in env, headers (plain, `Bearer` and mixed), args and the address. It covers another server's key and each of Trillion's own (model providers, Google, Slack, Telegram, voice), and that `fill` refuses before it reads any key. It also covers a server built past the parser that still fails without reading the key (local and remote), pastes, capitals, the legacy form, and `doctor`. `tests/api/test_mcp_api.py` covers removal (own keys only, a refused entry, capitals), paste and add refusing before anything is saved, renaming on adding, and editing by hand. All stores are in memory. The new API tests fail on main.

## Platforms

The same on macOS, Windows and Linux. The capitals rule (4) exists for Windows, where Credential Manager matches names without case. It applies everywhere so that an `mcp.json` behaves the same on every system. `docs/platforms.md` is updated.

## Not now

- A way to remove a refused entry from Settings. It is listed with its reason, and removing it from `mcp.json` by hand, or through the API, works.
- Moving keys saved under an old name when a server is renamed by hand in `mcp.json`.
