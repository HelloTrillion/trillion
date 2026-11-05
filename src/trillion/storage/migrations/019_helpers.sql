-- Your helpers (AGT-1, AGT-2): specialists the user makes under her, each with a name,
-- a one-line job, notes it learns (AGT-7) and a conversation of its own.
--
-- The conversation kind's CHECK has to take 'helper', and SQLite cannot widen a CHECK
-- in place, so the table is made again under its own name and its rows put back, as
-- migration 014 did. The tables that refer to it are untouched. Foreign keys are
-- checked when this commits, so a row that went missing fails it. No trigger names
-- this table; its indexes are made again below.
PRAGMA defer_foreign_keys = ON;

CREATE TABLE conversations_before_019 AS SELECT * FROM conversations;
DROP TABLE conversations;
CREATE TABLE conversations (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    created_at       INTEGER NOT NULL,
    deleted_at       INTEGER,  -- soft delete only (SPN-9)
    -- chat: a page opens it; scheduled: a timed ask ran there; day: the user's day;
    -- routine: a routine's own, where each run puts its board; helper: a helper's own.
    kind             TEXT NOT NULL DEFAULT 'chat'
        CHECK (kind IN ('chat', 'scheduled', 'day', 'routine', 'helper')),
    title            TEXT,
    title_source     TEXT CHECK (title_source IN ('words', 'model', 'user')),
    last_message_at  INTEGER,
    day              TEXT,
    -- A day conversation has a date, and only a day conversation does.
    CHECK ((kind = 'day') = (day IS NOT NULL))
);
INSERT INTO conversations
    (id, owner_id, created_at, deleted_at, kind, title, title_source, last_message_at, day)
    SELECT id, owner_id, created_at, deleted_at, kind, title, title_source, last_message_at, day
    FROM conversations_before_019;
DROP TABLE conversations_before_019;
CREATE INDEX conversations_by_recent
    ON conversations (owner_id, kind, deleted_at, last_message_at DESC);
CREATE UNIQUE INDEX conversations_one_day ON conversations (owner_id, day)
    WHERE kind = 'day' AND deleted_at IS NULL;

-- A helper's name is unique per owner whatever its case, among those not deleted.
-- `notes` grows a line at a time, as a routine's do. Paused while `paused_ms` is set.
-- Deleted only softly (SPN-9): `deleted_at` hides it, and its schedules keep pointing
-- at it for their runs' history.
CREATE TABLE helpers (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    name             TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    job              TEXT NOT NULL CHECK (length(job) BETWEEN 1 AND 200),
    notes            TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 1000),
    conversation_id  TEXT NOT NULL REFERENCES conversations(id),
    paused_ms        INTEGER,
    created_at       INTEGER NOT NULL,
    deleted_at       INTEGER
);
CREATE UNIQUE INDEX helpers_by_name ON helpers (owner_id, lower(name))
    WHERE deleted_at IS NULL;

-- A routine or timed ask that belongs to a helper; NULL for any other.
ALTER TABLE schedules ADD COLUMN helper_id TEXT REFERENCES helpers(id);
CREATE INDEX schedules_by_helper ON schedules (helper_id) WHERE helper_id IS NOT NULL;
