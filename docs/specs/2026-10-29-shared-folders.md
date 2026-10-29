# Shared folders

**Date:** 2026-10-29 · **Requirements:** CON-3a (shared folders change while she runs), CON-3b (a folder browser in the workspace), CON-3, SPN-2c, SPN-14, SEC-10, OPS-2, OPS-7, MEM-5, AGT-1, AGT-3 · **Builds on:** `docs/specs/2026-10-27-routines.md` (routines, the timed ask), `docs/specs/2026-10-29-speaking-up.md` (the Platforms section)

Today the user shares folders only by starting Trillion with `trillion run --grant
<folder>`, and every conversation and routine reaches all of them. After this, the
user shares a folder from Settings or by asking her ("let's find a folder for the
website project"), sees and moves through their own folders in a panel on the page
by voice or by clicking, and can give a folder to one routine only. She can propose
a share; only the user's tap makes one. Everything is served by the core, so it works
the same in a browser and in the desktop app on macOS, Windows and Linux; the desktop
app adds the system's own folder picker.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **A share is a record, not a startup flag.** Each share has an id, a name, a path, an access level (`read` or `change`) and a scope (`everyone`, or one routine). Shares are kept in `~/.trillion/folders.json`, written whole (OPS-2), and read live: a change applies from the next tool call, with no restart. | The user asked to manage folders while she runs. A file the user can read matches how skills folders are kept. |
| D2 | **She proposes, the user taps.** `folder_share` is `irreversible` and not recoverable, so it raises a card and needs a tap (SPN-14). The card names the full path, the access and the scope. A spoken "yes" never approves it. | The folder name may come from something she read (an email, a web page). A share widens what every later file tool can reach. |
| D3 | **Narrowing never asks.** Removing a share, lowering it from `change` to `read`, or narrowing it from everyone to one routine happens at once, from Settings or from `folder_unshare`, even after a turn read something from outside. | The same rule as AGT-7's notes: making her reach smaller is always safe. |
| D4 | **Some folders can never be shared**: a home folder itself, anyone else's home folder (every folder beside the user's own, such as `/Users/Guest` or `C:\Users\Public`, and `/root`), any folder that holds a home folder, a drive or volume root and any mount point (another drive starts there), any folder that holds drives, the system folders of D6 and `/Applications` and anything in them, the apps' own folders in a home folder (`~/Library`, `~\AppData`) and anything in them, except iCloud Drive (`~/Library/Mobile Documents/com~apple~CloudDocs`) and each provider's folder in `~/Library/CloudStorage` (Dropbox, Google Drive, OneDrive, Box), which hold the user's own files and follow every other rule, any folder that holds Trillion's data directory or is inside it, any folder the deny floor protects (SEC-10: `.ssh`, keychains, secrets), a hidden folder or any folder inside one, and a link (the share is of the folder it resolves to, checked as that folder). A share whose folder is later replaced by a link reaches nothing. The same rule holds in Settings, on the card, and at the moment the card is approved (SPN-2c). | A share of a home folder is a share of everything. OPS-7 already fences search away from roots and home folders. |
| D5 | **The browser shows folder names, nothing else.** No file names, sizes, dates or contents, no hidden folders (a name starting with `.`, or marked hidden on Windows), no folders the deny floor protects. At most 200 names per folder, sorted, with "and N more" past that. | Enough to find a folder by voice, and the least that leaves the computer: the names go to the model provider so she can follow "open Clients". |
| D6 | **The browser has a fence.** It starts at the places list (D8) and can open only folders under the home folder or a mounted drive, never system folders (`/System`, `/Library`, `/usr`, `/etc`, `/bin`, `/sbin`, `/var`, `/private`, `/proc`, `/sys`, `/dev`, `C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`, `C:\ProgramData`) and never Trillion's data directory. | Folder names under system folders are no use for a project and some are sensitive. |
| D7 | **Browsing only when the user asked.** `folders_browse` runs only on a turn the user started by speaking or typing (`via` voice or text), never in a routine, a timed ask, a widget ask, a refresh or a channel, and never after the turn has read something from outside (MEM-5). Clicking in the panel is the user's own act and needs no turn. | An injected "list their folders" in an email must find nothing to call. |
| D8 | **The places list**: Home, Desktop, Documents, Downloads (each only if it exists), the folders already shared, and mounted drives: `/Volumes/*` on macOS (the boot volume left out), drive letters with a disk on Windows, `/media/<user>/*`, `/run/media/<user>/*` and `/mnt/*` on Linux. | The places people keep projects, on each system. |
| D9 | **Scope follows today's behaviour by default.** A share for `everyone` reaches her own conversations, every routine and every agent, as `--grant` does now. A share for one routine reaches only that routine's runs. Agents (AGT-1) do not exist yet; the scope field takes `agent:<slug>` when they do, and this spec builds only `everyone` and `routine:<id>`. | Changing what existing routines can reach would break them silently. |
| D10 | **`--grant` still works, for that run only.** A command-line share is named after its folder, has `change` access and scope `everyone`, is shown in Settings marked "This run only", and can be removed for the run. It is never written to `folders.json`. | Scripts and the desktop dev launcher already use it; one path keeps working. |
| D11 | **`read` access holds at the deny floor**, not in each tool. A write tool (`file_create`, `file_append`, `file_edit`, `file_overwrite`) on a path whose nearest share is `read` is refused with "that folder is shared read only". Where shares nest, the innermost share decides. | One place enforces it for every tool, including MCP servers and skills that name paths. |

## Shares

```json
{"version": 1, "shares": [
  {"id": "f_3k9a", "name": "Website project", "path": "/Users/sam/Documents/Clients/Website",
   "access": "change", "scope": "everyone", "added_ms": 1761750000000}
]}
```

- `name`: 1–60 characters, no newlines, unique (case-insensitive); defaults to the
  folder's own name. `path`: absolute and resolved (links followed, `canonical` as
  the deny floor uses it). Two shares of the same path are one share; the later
  request changes it.
- A share whose folder is gone stays listed as "Not found" and reaches nothing until
  it is back. Nothing is removed for being missing (MEM-6b's rule: a failed read
  never implies deletion).
- A damaged `folders.json` is never overwritten: no saved shares apply (this run's
  `--grant` shares still do, as they were never in it), Settings says the file is
  damaged and where it is, and the user fixes or removes it by hand. Writes go to a
  temporary file, then rename, then read back (OPS-2).
- **One writer.** `trillion/files/shares.py` is the only module that writes
  `folders.json`, called from the Settings routes and from `folder_share` and
  `folder_unshare` once approved (`scripts/check_one_writer.py` gains the rule).
- **What a turn gets.** The host builds each `ToolContext` with the shares in scope
  for it: her own turns get `everyone`; a routine run gets `everyone` plus its own.
  `granted_roots` stays the paths; a new `ToolContext.read_only_roots` carries the
  `read` ones for D11. Both are read at the moment of the call, and again when an
  approved card executes (SPN-2c): a card approved after its folder was removed or set
  to `read` is refused at execution, and says why.
- **Her prompt** lists the shares in scope as `- Website project: ~/Documents/Clients/Website (read and change)`,
  built fresh each turn, so she can say "I'll put it in your Website project folder".
- A removed routine's shares are removed with it.

## Tools

| Tool | Risk | What it does |
|---|---|---|
| `folders_browse(place)` | read | Opens or moves the browser. `place` is `places`, a path, a name from the folder the browser now shows ("Clients"), or `up`. Returns the folder's path and its folder names (D5), wrapped as untrusted. Sends the page the panel (below). Refused outside D7, or past the fence (D6), with the reason in words. |
| `folder_share(path, name?, access?, scope?)` | irreversible, not recoverable | Raises the card: "Share ~/Documents/Clients/Website with Trillion as Website project, read and change, for everyone". `path` may be `this`, meaning the folder the browser shows. `access` defaults to `read`; `scope` defaults to `everyone`, or a routine named by its title. Checks D4 when called and again when approved. On approval writes the share. |
| `folder_unshare(name, access?, scope?)` | write | With only `name`, removes the share. With `access: read`, lowers it. With a routine `scope`, narrows it. Refuses a change that would widen (D3: that is `folder_share`'s job, on a card). Never asks. |

All three carry `category="files"`. `folders_browse` has a 5 s timeout and lists one
folder only (no walking down); `folder_share` and `folder_unshare` have 5 s. The
descriptions say plainly that the browser shows names only and that a share is made
on a card the user taps.

## The panel

- A **Folders panel** in the workspace, beside the board, opened by the session's
  `folders` frame: `{"type": "folders", "path": "...", "crumbs": [...], "folders": [...], "more": 0, "shared": "f_3k9a" | null, "places": [...] | null}`.
  The panel is not a board widget; the page draws it.
- It shows a breadcrumb, the folder names as rows with a folder icon, a "Shared as
  Website project" tag on folders already shared, and two actions: **Share this
  folder…** and **Close**.
- **Clicking a row** opens that folder through `POST /api/folders/browse` (the
  user's own act, no turn, the same fence). The session then tells her where the
  browser now is with a one-line host note on her next turn ("The folder browser
  shows ~/Documents/Clients"), so "share this one" means what the user sees.
- **Share this folder…** opens a small form in the panel: name (prefilled with the
  folder's name), access (Read only / Read and change), who (Everyone / a routine).
  **Share** saves it through `POST /api/folders`: the user filled the form and
  tapped, so there is no card. The panel never sends a share the user did not
  submit.
- By keyboard: arrows move between rows, Enter opens, Backspace goes up, Escape
  closes. The panel announces the folder it shows in a live region (SUR-6).
- At phone width the panel takes the full width over the board.

## Settings → Shared folders

The existing panel, which today only lists folders and tells the user about
`--grant`, becomes:

- one card per share: name, path, access, who, "Not found" when missing, "This run
  only" for `--grant` shares; actions **Change** (name, access, who) and **Remove**.
- **Add a folder**: in the desktop app, the system folder picker; in a browser, the
  Folders panel opened at the places list. Both end in the same form as the panel.
- a line under the list: "Only folder names are shown to Trillion while you browse,
  and they go to your model provider so she can follow you. Files in a shared folder
  are read only when she uses a file tool."
- a damaged `folders.json`: the line "Your shared folders file is damaged, so no
  folders are shared. It is at <path>."

Routes: `GET /api/folders` (shares and problems), `POST /api/folders` (add or change),
`DELETE /api/folders/{id}`, `POST /api/folders/browse` (`{path}` or `{place:
"places"}`). All take the session token, as every other settings route does.
Widening from a route is the user's own act in Settings; from the model it is always
the card.

## Desktop app

`apps/desktop` adds Tauri's dialog plugin and one command, `pick_folder`, that opens
the system folder picker (`NSOpenPanel` on macOS, the common file dialog on Windows,
the XDG portal or GTK dialog on Linux) and returns the chosen path to the page through
the existing bridge. The page then shows the form. The picker's answer is a path the
user chose, but it goes through `POST /api/folders` and D4 like any other.

## Platforms

| Part | macOS | Windows | Linux | Browser |
|---|---|---|---|---|
| Shares, the card, Settings | Yes | Yes | Yes | Yes |
| Folder browser | Yes | Yes | Yes | Yes (drawn by the page from the core's listing) |
| Places | Home, Desktop, Documents, Downloads, `/Volumes/*` | Home, Desktop, Documents, Downloads (known folders, so a redirected Documents is found), drive letters | Home, XDG Desktop/Documents/Download (`xdg-user-dir`, falling back to the English names), `/media/<user>/*`, `/run/media/<user>/*`, `/mnt/*` | Same as the core's system |
| Hidden folders left out | Name starts with `.`, or `UF_HIDDEN` | Hidden or system attribute | Name starts with `.` | — |
| Names compared | Case-insensitive (as the deny floor does) | Case-insensitive | Case-sensitive | — |
| System folder picker | `NSOpenPanel` (desktop app) | Common item dialog (desktop app) | XDG portal, GTK fallback (desktop app) | Not available: a browser never gives the page a real path, so the panel is used |

`docs/platforms.md` gains these rows.

## Testing

- `tests/unit/files/test_shares.py`: add, change, remove; names unique; same path is
  one share; whole-file write and read-back; damaged file never overwritten, no
  shares apply; missing folder kept as "Not found"; `--grant` shares never written.
- `tests/unit/files/test_share_rules.py`: D4 refuses home, a drive root, the data
  directory and its parents, protected folders, and a link to any of them; a link to
  an allowed folder shares the folder it resolves to.
- `tests/unit/files/test_browse.py`: names only, hidden left out per system (fake
  stat results), protected left out, 200 cap with the count, the fence (D6) per
  system, `up` stops at the places list, places per system with fake environments.
- `tests/spine/test_read_only_share.py`: each write tool refused in a `read` share,
  allowed in a `change` share nested inside it, refused in a `read` share nested in a
  `change` one.
- `tests/api/test_folders_tools.py`: `folders_browse` refused for `schedule`,
  `routine`, `ask`, `refresh`, `channel` and after an outside read; `folder_share`
  raises a card with the exact path, needs a tap, is refused by a spoken yes;
  approval rechecks D4 (the folder replaced by a link to `~/.ssh` between card and
  tap is refused); a card approved after the share's folder was removed is refused at
  execution; `folder_unshare` never asks and refuses widening; a routine reaches only
  `everyone` plus its own shares; her prompt lists the shares in scope.
- `tests/api/test_folders_api.py`: the routes, the token, D4 on the route, the
  browse route's fence.
- `apps/web/test/folders.test.ts`: the panel from a frame, click to open, keyboard,
  the form, the live region; Settings cards, Change, Remove, "This run only", the
  damaged-file line, the desktop picker when the bridge has it and the panel when it
  does not.
- A real-system test on each CI runner: the places list and one browse of the home
  folder return within 2 s with no file names in the result.
- `docs/TESTING.md` gains the manual checks: "show me my Documents", "open <a
  folder>", "go back", "share this one for the website project", tap the card, ask
  her to create a file there; then set it to read only in Settings and ask again; in
  the desktop app, Add a folder with the system picker.

## Notes

- Folder names under the home folder can be personal ("Divorce", "Medical"). They
  leave only while the user is browsing and only to the model provider already in
  use; with a local model nothing leaves. The Settings line says this.
- Search (`file_search`, OPS-7) is not built yet; when it is, it searches only inside
  the shares in scope, and this spec's D4 is the same fence it needs.
- Agents (AGT-1) get a `folders` field in their manifest naming shares by name when
  they are built; the scope field is ready for it (D9).
