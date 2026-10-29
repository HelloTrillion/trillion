-- Speaking up: watchers that tell the user something first (AGT-5, the speaking up spec).
--
-- A watcher's notice comes from `attention`, with `source_id` the find's key. It is a
-- heads_up: her one line about a meeting, an email or a payment. `link` is where its
-- button opens (https only), and `fresh_until_ms` is when it stops being worth saying
-- out loud: delivered after that, it is shown and not spoken.
--
-- The source and kind CHECKs have to widen, and SQLite cannot widen one in place, so
-- the table is made again under its own name and its rows put back, as 014 did. A
-- run's `result_notice` points here: foreign keys are checked when this commits, so a
-- row that went missing fails it. No trigger names this table; its index is made again.
PRAGMA defer_foreign_keys = ON;

CREATE TABLE notices_before_015 AS SELECT * FROM notices;
DROP TABLE notices;
CREATE TABLE notices (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    source           TEXT NOT NULL CHECK (source IN ('schedule', 'attention')),
    source_id        TEXT NOT NULL,
    -- heads_up: a watcher's line; its words are hers, written from someone else's.
    kind             TEXT NOT NULL
        CHECK (kind IN ('reminder', 'ask', 'missed', 'failed', 'ready', 'heads_up')),
    title            TEXT NOT NULL,
    text             TEXT,
    due_ms           INTEGER NOT NULL,
    late             INTEGER NOT NULL CHECK (late IN (0, 1)),
    created_ms       INTEGER NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('held', 'delivered')),
    delivered_ms     INTEGER,
    -- The conversation it was delivered into.
    delivered_to     TEXT,
    -- When the system notification was shown, once.
    notified_ms      INTEGER,
    -- The conversation a routine's notice opens; NULL for any other.
    conversation_id  TEXT,
    -- A meeting's join link. Only https: it is opened by a tap, and came from an invite.
    link             TEXT CHECK (link IS NULL OR (link LIKE 'https://_%' AND length(link) <= 2000)),
    -- After this it is shown, not spoken. NULL: spoken whenever it arrives.
    fresh_until_ms   INTEGER
);
INSERT INTO notices
    (id, owner_id, source, source_id, kind, title, text, due_ms, late, created_ms, status,
     delivered_ms, delivered_to, notified_ms, conversation_id)
    SELECT id, owner_id, source, source_id, kind, title, text, due_ms, late, created_ms, status,
        delivered_ms, delivered_to, notified_ms, conversation_id
    FROM notices_before_015;
DROP TABLE notices_before_015;
CREATE INDEX notices_held ON notices (owner_id, status, created_ms);

-- Everything a watcher found, so each is told at most once, across restarts (D5).
-- told: it became a notice; silent: judged not worth saying (D6); dismissed: the user
-- set it aside, kept for the dedup window as a soft delete.
CREATE TABLE attention_finds (
    owner_id       TEXT NOT NULL REFERENCES owners(id),
    key            TEXT NOT NULL,
    watcher        TEXT NOT NULL,
    first_seen_ms  INTEGER NOT NULL,
    notice_id      TEXT REFERENCES notices(id),
    status         TEXT NOT NULL CHECK (status IN ('told', 'silent', 'dismissed')),
    dismissed_ms   INTEGER,
    PRIMARY KEY (owner_id, key)
);
CREATE INDEX attention_finds_recent ON attention_finds (owner_id, first_seen_ms DESC);

-- Each watcher's place: when it last looked, where to read on from, what last went
-- wrong (its kind, never content), and the user's notes on what to watch (AGT-7).
CREATE TABLE attention_state (
    owner_id       TEXT NOT NULL REFERENCES owners(id),
    watcher        TEXT NOT NULL,
    last_check_ms  INTEGER,
    cursor         TEXT,
    last_error     TEXT,
    notes          TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 1000),
    PRIMARY KEY (owner_id, watcher)
);
