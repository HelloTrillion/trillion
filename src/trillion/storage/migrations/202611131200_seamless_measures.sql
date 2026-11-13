-- The Seamless measures (docs/specs/2026-11-12-seamless.md), counted on this computer
-- only (ONB-6). Numbers and times, never anything anyone said or any name: no message
-- text, no tool, no arguments, no card summary.

-- The first run: when onboarding began (the app opened with no persona yet), whether a
-- model was ready then, how many onboarding steps asked the user something before her
-- first reply, and when that first reply reached the page. One row per owner, written
-- once and finished once. An install from before this has no row: "not recorded".
CREATE TABLE seamless_first_run (
    owner_id        TEXT PRIMARY KEY REFERENCES owners(id),
    opened_at       INTEGER NOT NULL,
    model_ready     INTEGER NOT NULL CHECK (model_ready IN (0, 1)),
    questions       INTEGER NOT NULL DEFAULT 0 CHECK (questions >= 0),
    first_reply_at  INTEGER
);

-- A turn in which a tool took over two seconds: when, whether her voice was on, and
-- whether her first words were heard before two seconds into that tool. A row older
-- than 90 days is written over by the next one (nothing is hard-deleted, SPN-9).
CREATE TABLE seamless_tool_turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        TEXT NOT NULL REFERENCES owners(id),
    at              INTEGER NOT NULL,
    voiced          INTEGER NOT NULL CHECK (voiced IN (0, 1)),
    spoke_in_time   INTEGER NOT NULL CHECK (spoke_in_time IN (0, 1))
);
CREATE INDEX seamless_tool_turns_at ON seamless_tool_turns (owner_id, at);
