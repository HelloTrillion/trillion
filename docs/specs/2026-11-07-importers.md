# Importers: bring what OpenClaw or Hermes knew

**Date:** 2026-11-07 · **Requirements:** CON-8 · **Respects:** PER-1, PER-1a, PER-2, MEM-1, MEM-3, MEM-6a, MEM-6b, CON-6, SPN-5, SPN-6, ONB-8

People who used another assistant have told it a lot: their name, their timezone, what
they like, the skills they wrote. `trillion import openclaw` and `trillion import
hermes` carry that over, so Trillion does not start from nothing. Her soul is hers: a
`SOUL.md` from the other app is never imported, and the output says so.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Read-only on the source, and a dry run by default.** The command prints exactly what it would do. `--apply` does it. | The other app is still the user's. A preview costs nothing and shows what is about to arrive. |
| D2 | **The soul is never imported.** `SOUL.md`, and the other app's working rules (`AGENTS.md`, `TOOLS.md`, `HEARTBEAT.md`, `BOOT.md`, `BOOTSTRAP.md`), are listed as left out, with why. | CON-8. Trillion's personality is authored (PER-1). Another app's rules would steer her from outside the privileged path. |
| D3 | **Names and timezone go through the privileged setup path.** The user's name, their timezone and the name they gave the other agent are written by `persona.setup.save`, the one writer of `USER.md` and `IDENTITY.md` (PER-2). A value the user already set is never replaced without showing old and new and asking: an interactive yes, or `--replace-profile`. With neither, the current value stays and the output says so. | The user set those on purpose. The command line is not model-facing, so it may use the setup path. |
| D4 | **Everything else becomes memories, as imported claims.** Each entry is a Trillion memory with source `imported_claim` (MEM-6a) and its origin in the file's header (`origin: openclaw USER.md`). Its `as_of` is when the source knew it (a daily note's date, else the file's modified time), never the import time. | An imported line is something read, not something the user said. Dated by the import, it would look newer than what the user told her last month. |
| D5 | **An imported claim never outranks a user statement.** Recall puts imported claims after other memories that match, and her prompt says the user's own word wins. | MEM-6a. |
| D6 | **Bounded and deduplicated.** At most 500 new memories a run, each at most 1,200 characters (an entry longer than that is left out and counted). An entry already held, in any state, is skipped: a memory the user forgot stays forgotten. Running again adds only what is new, so a large import finishes over a few runs. | MEM-6b. Running the same import twice changes nothing. |
| D7 | **Skills are copied, off.** A skill that passes Trillion's own skill parser (CON-6) is copied into `~/.trillion/skills/<name>/`, which Settings → Skills lists like any folder. It starts off, as every skill does. What it declares it needs (tools, programs, environment variables, scripts) is printed so the user can decide later. A skill that fails the parser, holds a link, or has the name of one already there with different files is left out with the reason. | A skill steers her; only text the user approved byte for byte does (the skills spec, D3). |
| D8 | **Imported text is data.** Nothing from the source is followed. A memory saying "ignore your rules" is stored as an imported claim and reaches her through the usual untrusted presentation (SPN-5, MEM-5). Text printed to the terminal has control characters removed. | SPN-5, SPN-6. |
| D9 | **No network, no secrets.** The importer reads Markdown files and skill folders only. It never opens the other app's config, `.env`, credentials or databases. | ONB-8. Keys belong in the keychain, typed by the user. |

## Where each app keeps things

Checked on 2026-11-07 against the public repositories and docs. Anything missing is
skipped quietly; a missing folder is "nothing found", not an error.

**OpenClaw** ([agent workspace](https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent-workspace.md), [environment](https://github.com/openclaw/openclaw/blob/main/docs/help/environment.md), [skills](https://docs.openclaw.ai/tools/skills), [templates](https://github.com/openclaw/openclaw/tree/main/docs/reference/templates))

- Home: `$OPENCLAW_STATE_DIR`, else `~/.openclaw` (older installs: `~/.clawdbot`, `~/.moltbot`). Workspace: `$OPENCLAW_WORKSPACE_DIR`, else `<home>/workspace` (also tried: `workspace-main`, `workspace.default`).
- `USER.md` (about the user: lines such as `- **Name:**`, `- **What to call them:**`, `- **Timezone:**`) → name and timezone (D3); the rest → `user` memories.
- `IDENTITY.md` (the agent's name, vibe, emoji) → only `- **Name:**`, as her name (D3). The vibe is persona and is left out.
- `MEMORY.md` → `reference` memories. `memory/YYYY-MM-DD.md` daily notes → `project` memories, dated by the file name.
- Skills: `<workspace>/skills/<name>/SKILL.md` and `<home>/skills/<name>/SKILL.md`. Their header may carry `metadata.openclaw.requires.bins` and `.env`, which are shown.
- Left out: `SOUL.md`, `AGENTS.md`, `TOOLS.md`, `HEARTBEAT.md`, `BOOT.md`, `BOOTSTRAP.md`; never read: `openclaw.json`, `.env`, `credentials/`, sessions, any `.sqlite`.

**Hermes Agent** ([memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory), [skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [`hermes_constants.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_constants.py), [`tools/memory_tool_store.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/memory_tool_store.py), [OpenClaw migration guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/migrate-from-openclaw.md))

- Home: `$HERMES_HOME`, else `~/.hermes`. A profile (`~/.hermes/profiles/<name>`) has the same layout; pass it with `--from`.
- `memories/USER.md`: entries separated by a line holding only `§`. An entry like `Name: …` or `Timezone: …` → D3; the rest → `user` memories.
- `memories/MEMORY.md`: the same format → `reference` memories.
- Skills: `skills/<category>/<name>/SKILL.md` (and `skills/<name>/SKILL.md`). `required_environment_variables` and `metadata.hermes.requires_tools` are shown.
- Left out: `SOUL.md`; never read: `config.yaml`, `.env`, `state.db`.

Uncertain: OpenClaw's own docs say `workspace`, the Hermes guide says it was renamed
`workspace-main`, so both are tried. OpenClaw's `TOOLS.md` and `HEARTBEAT.md` are
retired but still on older installs. Markdown entries are split here by list item and
paragraph; neither app documents a format for `MEMORY.md`.

## What the user sees

```
$ trillion import openclaw
Importing from OpenClaw: ~/.openclaw/workspace
Dry run: nothing is changed.

Left out
  SOUL.md      Trillion keeps her own soul. Yours stays where it is.
  AGENTS.md    OpenClaw's working rules, not something you told it.

About you (written through setup, like onboarding)
  your name    Sam  (not set yet)
  timezone     Europe/Berlin  (now America/Toronto: kept unless you confirm)

Memories: 24 to add as imported claims; 3 already held
  + user       Prefers short replies in the morning.   USER.md
  ...

Skills: 2 to copy, off until you turn them on in Settings → Skills
  + pdf-forms  needs: programs uv; environment GEMINI_API_KEY; scripts Trillion won't run
  - Bad_Name   left out: its name must be lowercase letters, digits and hyphens

Run again with --apply to import.
```

`--from PATH` reads another folder (the app's home or its workspace). `--apply` writes;
it asks before replacing a name or timezone the user set, unless `--replace-profile`
is given, and keeps the current value when it cannot ask. If Trillion is running, the
command says to restart it so she picks up the changes.

## How

- `src/trillion/importers/`: `sources.py` finds the folders and reads each app into one
  `Found` (profile fields, entries, skills, what was left out). `plan.py` compares it
  with what Trillion holds and prints the plan. `apply.py` writes it: names through
  `persona.setup.save`, memories through `MemoryBook.write` (file and row, MEM-1),
  skills copied through a staging folder and parsed again before they are moved in.
- `Memory` gains `origin`, written in the file's header and not in the index: the file
  is the truth (MEM-1), and the index needs no migration.
- `skills.library.Library` also lists the skill folders in `~/.trillion/skills`. The
  user cannot add Trillion's own folder; this one subfolder holds only copied skills,
  and no tool can write there (the deny floor covers the data folder).
- `memory.rank` places imported claims after other matches; `memory.present` labels
  them "from something you read, not from the user; what the user says wins".

## Not now

- Importing from the other app's config: model choice, MCP servers, channels, keys.
- Session history and transcripts.
- A Settings page for imports. The command line is enough for a one-time move.
- Turning imported skills on in the same step.

## Testing

Fixture folders for both apps (`tests/importers/`): dry run changes nothing; apply
writes names, memories and skills; `SOUL.md` is refused; a conflicting name or
timezone is kept without a yes and replaced with `--replace-profile`; a skill that
fails the parser is left out; injection text is stored as an imported claim and
presented as untrusted; a second run adds nothing; recall puts a user statement
before an imported claim.
