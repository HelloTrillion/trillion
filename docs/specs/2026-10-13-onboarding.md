# Onboarding: she learns your name and you choose hers

**Date:** 2026-10-13 · **Requirements:** ONB-3 (first part), ONB-9, PER-1a, PER-2, PER-6 · **Phase 0 item:** 3

On a first run she says who she is, asks what to call you, asks what you want to call her, and asks how blunt you want her. The app writes `USER.md` and `IDENTITY.md`, and from the next turn she answers to the name you gave. This is the first slice of onboarding. Walking the capability ladder (model, voice, listening) and demonstrating the confirmation rule are the next one.

## The conversation

It happens in the transcript, typed, in three questions:

1. **Your name.** "I'm Sam." and "call me Sam" both mean Sam.
2. **Her name.** Keep the one she has (one click, or "keep it") or type a new one.
3. **Tone.** Blunt, balanced or gentler, as buttons or typed.

Then the app saves, and she says what was saved, the timezone the browser reported, and what comes next. **Skip for now** is offered at every step (ONB-9). The **Names** button in the header runs the same three questions again later, starting from the current answers.

**Her lines here are authored, not composed.** On a first run there may be no model to compose anything, and the names must not be written by her in any case. The script lives in `apps/web/src/onboarding.ts` as a pure state machine. It uses the name she currently has and never a hardcoded one, and her last line says the script is over.

Onboarding starts only in an empty conversation, on the first `state` after a `session` message that says `onboarding: "due"`. On a first run with no model it comes before the model dialog, which opens when the introduction ends or is skipped.

## The privileged setup path (PER-2)

`trillion.persona.setup` is the only code that writes the identity files. It is reached through `PUT /api/settings/persona`, which needs the session token and the app's own origin like the rest of Settings. No tool reaches it, and the deny floor refuses any tool that names the persona folder.

| Endpoint | What it does |
|---|---|
| `GET /api/settings/persona` | Current names, timezone, directness, and `onboarding`: `due`, `skipped` or `done`. |
| `PUT /api/settings/persona` | Validates, writes both files, adopts them, and sends `identity` to every open page. |
| `POST /api/settings/persona/skip` | Remembers that the user put it off. Does nothing once they are introduced. |

Each file is written to a temporary file and renamed into place, so it is all of the new text or all of the old.

```
---
user_name: Sam
timezone: America/Chicago
directness: blunt
---
```

## What a name may be

A name goes into every prompt and into a `key: value` line, so it is one line of at most 40 characters, with no braces or angle brackets and no leading quote, dash or `#`. Runs of whitespace, including line breaks, collapse to one space before anything else, so a pasted second line cannot add a field. A refusal returns a code (`name_empty`, `name_too_long`, `name_not_allowed`, `timezone_not_allowed`) and writes nothing. A hand-edited file that breaks these rules reads as not set up.

## Directness

`balanced` is the soul as written. `blunt` and `gentle` each add one authored paragraph (`persona/prompts/tone_*.md`) after the soul. An overlay adjusts her and never replaces her (PER-7 is the fuller version of this, at P1).

## Checked

- `tests/unit/test_persona_setup.py`: round trip, the file text, every refused name and timezone, a hand edit that breaks the rules, the overlays, and that no registered tool names the setup path.
- `tests/api/test_onboarding.py`: due on a fresh install, token and origin required, both files written and the next system prompt uses both names, refusals write nothing, skip survives a restart, a rename takes effect on the next turn, and the socket takes no such message.
- `tests/spine/test_deny_floor.py`: a tool that targets `USER.md` or `IDENTITY.md` is refused.
- `apps/web/test/onboarding.test.ts`: every step, the ways people say a name, skip, a failed save, and the rename flow.
- By hand on macOS with a brand-new data folder: the first run with no model, a reload, a rename, a skip, and a real reply from `gemma4:e4b` that used both names.

## Not checked

- Windows and Linux by hand.
- A data folder the app cannot write to, beyond the `not_saved` code path being covered by a unit of logic and not by a real read-only folder.

## Known limits

- **The timezone is stored and not yet used.** The core runs on the user's machine, so `now` already comes from the local clock. Using the stored zone needs time zone data that Windows does not ship.
- **Her scripted lines are never spoken**, even with a voice chosen. Onboarding runs as text.
- **The soul is not copied into the data folder.** The PRD says onboarding copies `SOUL.md` there; this slice leaves her on the shipped one, so testers keep getting updates to it. That needs a decision before Phase 0 is done.
- An introduction is offered only in an empty conversation. If a tab resumes a conversation with history, the Names button is the way in.

## Left for later slices

- The ladder walk: a model, then a voice, then a way to listen, with costs and the free choice named at each step (ONB-3, ONB-10).
- Demonstrating the confirmation rule.
- A first composed greeting once a model exists.
- `trillion setup` and `trillion doctor` (ONB-2, ONB-5).
