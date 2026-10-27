-- Your day (SUR-1): a conversation per date where she builds today's board, opened
-- only when the user asks. `day` is the local date (YYYY-MM-DD) in the user's zone.
--
-- The kind's CHECK (migration 008) has to take 'day', and SQLite cannot widen a CHECK
-- in place, so the table is made again under its own name and its rows put back. The
-- tables that refer to it are untouched. Foreign keys are checked when this commits:
-- dropping the old table counts each message as missing its conversation, and putting
-- the conversation back counts it found again, so a row that went missing fails it.
-- Dropping and refilling the table looks up every row that refers to it. Without
-- these two indexes that is a full scan of usage and turn_timings per conversation;
-- they also serve any later lookup by conversation, so they stay.
CREATE INDEX IF NOT EXISTS usage_by_conversation ON usage (conversation_id);
CREATE INDEX IF NOT EXISTS turn_timings_by_conversation ON turn_timings (conversation_id);
PRAGMA defer_foreign_keys = ON;
CREATE TABLE conversations_before_012 AS SELECT * FROM conversations;
DROP TABLE conversations;
CREATE TABLE conversations (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    created_at       INTEGER NOT NULL,
    deleted_at       INTEGER,  -- soft delete only (SPN-9)
    -- chat: a page opens it; scheduled: a timed ask ran there; day: the user's day.
    kind             TEXT NOT NULL DEFAULT 'chat'
        CHECK (kind IN ('chat', 'scheduled', 'day')),
    title            TEXT,
    title_source     TEXT CHECK (title_source IN ('words', 'model', 'user')),
    last_message_at  INTEGER,
    day              TEXT,
    -- A day conversation has a date, and only a day conversation does.
    CHECK ((kind = 'day') = (day IS NOT NULL))
);
INSERT INTO conversations
    (id, owner_id, created_at, deleted_at, kind, title, title_source, last_message_at)
    SELECT id, owner_id, created_at, deleted_at, kind, title, title_source, last_message_at
    FROM conversations_before_012;
DROP TABLE conversations_before_012;
CREATE INDEX conversations_by_recent
    ON conversations (owner_id, kind, deleted_at, last_message_at DESC);
-- One live day conversation per date: a deleted one leaves room for a fresh one.
CREATE UNIQUE INDEX conversations_one_day ON conversations (owner_id, day)
    WHERE kind = 'day' AND deleted_at IS NULL;
