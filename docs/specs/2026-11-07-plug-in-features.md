# Features plug in; they don't append to shared files

**Date:** 2026-11-07 · **Requirements:** OPS-6 (fix the writer, with a check that refuses the wrong form), CON-7 (plugin discovery instead of hand registration), §11 (contribution model)

## Why

On 2026-11-07 eleven branches built in parallel touched the same few files. Nearly every pull request conflicted with the next one merged. Few of the conflicts were real disagreements. Most were two features each adding one line at the end of the same list:

- `apps/web/src/main.ts`: an import, a `create…()` call, a `panel.add(…)` line and a `handle(msg)` line for every Settings section.
- `apps/web/src/app.css`: a block appended at the end for every feature.
- `apps/web/src/icons.ts`: one icon per section.
- `src/trillion/api/app.py`: an import and an `include_router(…)` per feature.
- `src/trillion/api/runtime.py`: a `registry.get(…)` and `bind(…)` per tool.
- `src/trillion/storage/migrations/`: two branches both took the next number, `021`.
- Eight test files hard-coded the full list of migrations, so every new migration edited all eight.

In a contributor project this is the common case, not an accident: every outside pull request would hit it. The fix is structural. A new feature lives in files of its own and plugs in, and a check refuses a pull request that goes back to appending.

## Decisions

1. **Settings sections plug in from `apps/web/src/features/`.** Each file there exports `feature`, which gets a small context (`panel`, `api`, `send`, `refresh`, `openConversation`, `on(handler)`) and registers itself. `main.ts` loads them all with `import.meta.glob`; nobody edits `main.ts` to add one. A section says where it goes with `after: '<section id>'`, so its place doesn't depend on file order.
2. **A feature brings its own styles and icon.** CSS sits beside the feature (`features/<name>.css`) and is imported by it, so it lands after `app.css` in the cascade, exactly where an appended block would have been. Icons register with `addIcon(name, paths)`.
3. **Routers plug in with `ROUTES`.** A module under `trillion.api` that defines `ROUTES = (factory, …)` is found and mounted; `app.py` isn't edited. The routers that predate this stay listed.
4. **Tools connect themselves with `attach(rt)`.** A registered tool that defines `async def attach(self, rt)` is given the runtime once it is built. `runtime.py` isn't edited. The tools that predate this stay bound by hand, and move over as they are touched.
5. **New migrations are named by time, not by the next number.** `YYYYMMDDHHMM_name.sql` (twelve digits) sorts after every three-digit migration and can't collide across branches. The three-digit ones stay as they are.
6. **Tests ask for the migration list instead of copying it.** `trillion.storage.migrate.names()` lists them; a test that means "everything after 016" says `names(after="016")`. A shipped migration's checksum lives in its own file (`tests/unit/shipped_migrations/<name>.sha256`), so a new migration adds a file instead of a line to a shared table.
7. **A check keeps it this way** (`scripts/check_plug_in.py`, in CI). The hook counts in the shared files may only go down: `panel.add(`, `handle(msg)` and `import` lines in `main.ts`, `include_router(` in `app.py`, `.bind(` in `runtime.py`, entries in `icons.ts`, and the line count of `app.css`. A three-digit migration newer than the ones that exist is refused. Each failure says what to do instead. When someone moves an old hook out, they lower the number in the script, which only ever shrinks.

## What moves now

As examples, and to bring the counts down: Memories (with its notes switch), Confirmations and Devices become features, with their CSS and icons; the confirmations, memories, security and voiceprint routers use `ROUTES`; `week_roast` connects itself with `attach`. Everything else moves when someone next works on it.

## Not now

- `session.py` (4,461 lines, the most-changed file) still takes each new client message type by hand. A registry for message handlers there is the next step, and a bigger one.
- `pyproject.toml`'s tool entry points stay the public plugin interface (CON-7).
- `docs/PRD.md` rows still conflict when two branches edit neighbouring rows; that is a coordination question, not a code one.
