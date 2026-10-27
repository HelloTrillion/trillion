-- Routines: a timed ask with a target, that builds a board while nobody watches.
--
-- A schedule only gains columns. `target` is NULL for a plain timed ask, 'day' for
-- one that fills today's day conversation, 'own' for one with a conversation of its
-- own (`conversation_id`, made on its first run). `notes` is what the user said to
-- watch or leave out. A routine is paused while `paused_ms` is set, and `failures`
-- counts its failed runs in a row.
ALTER TABLE schedules ADD COLUMN target TEXT CHECK (target IN ('day', 'own'));
ALTER TABLE schedules ADD COLUMN conversation_id TEXT;
ALTER TABLE schedules ADD COLUMN notes TEXT NOT NULL DEFAULT ''
    CHECK (length(notes) <= 1000);
ALTER TABLE schedules ADD COLUMN paused_ms INTEGER;
ALTER TABLE schedules ADD COLUMN failures INTEGER NOT NULL DEFAULT 0;

-- Three CHECKs have to widen, and SQLite cannot widen one in place, so each table is
-- made again under its own name and its rows put back, as migration 013 did: a
-- conversation can be a routine's, a notice can say a board is ready (and which
-- conversation to open), and a run can end as skipped or as having nothing to say.
-- Foreign keys are checked when this commits, so a row that went missing fails it.
-- No trigger names these tables; their indexes are made again below.
PRAGMA defer_foreign_keys = ON;

CREATE TABLE conversations_before_014 AS SELECT * FROM conversations;
DROP TABLE conversations;
CREATE TABLE conversations (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    created_at       INTEGER NOT NULL,
    deleted_at       INTEGER,  -- soft delete only (SPN-9)
    -- chat: a page opens it; scheduled: a timed ask ran there; day: the user's day;
    -- routine: a routine's own, where each run puts its board.
    kind             TEXT NOT NULL DEFAULT 'chat'
        CHECK (kind IN ('chat', 'scheduled', 'day', 'routine')),
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
    FROM conversations_before_014;
DROP TABLE conversations_before_014;
CREATE INDEX conversations_by_recent
    ON conversations (owner_id, kind, deleted_at, last_message_at DESC);
CREATE UNIQUE INDEX conversations_one_day ON conversations (owner_id, day)
    WHERE kind = 'day' AND deleted_at IS NULL;

CREATE TABLE notices_before_014 AS SELECT * FROM notices;
DROP TABLE notices;
CREATE TABLE notices (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    source           TEXT NOT NULL CHECK (source IN ('schedule')),
    source_id        TEXT NOT NULL,
    -- ready: a routine's board is built; `conversation_id` is where to open it.
    kind             TEXT NOT NULL
        CHECK (kind IN ('reminder', 'ask', 'missed', 'failed', 'ready')),
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
    conversation_id  TEXT
);
INSERT INTO notices
    (id, owner_id, source, source_id, kind, title, text, due_ms, late, created_ms, status,
     delivered_ms, delivered_to, notified_ms)
    SELECT id, owner_id, source, source_id, kind, title, text, due_ms, late, created_ms, status,
        delivered_ms, delivered_to, notified_ms
    FROM notices_before_014;
DROP TABLE notices_before_014;
CREATE INDEX notices_held ON notices (owner_id, status, created_ms);

CREATE TABLE task_runs_before_014 AS SELECT * FROM task_runs;
DROP TABLE task_runs;
CREATE TABLE task_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id    TEXT NOT NULL REFERENCES schedules(id),
    due_ms         INTEGER NOT NULL,
    -- skipped: a routine not run (paused, or its day already built by hand);
    -- nothing: a routine that ran and had nothing worth the user's attention.
    status         TEXT NOT NULL CHECK (status IN (
                       'queued', 'running', 'delivered', 'late', 'missed', 'blocked',
                       'failed', 'interrupted', 'cancelled', 'skipped', 'nothing')),
    claimed_by     TEXT,
    claimed_ms     INTEGER,
    started_ms     INTEGER,
    finished_ms    INTEGER,
    timeout_s      REAL NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    result_notice  TEXT REFERENCES notices(id),
    detail         TEXT,
    created_ms     INTEGER NOT NULL
);
-- Ids kept as they were: a run's id is how the scheduler finishes it.
INSERT INTO task_runs
    (id, schedule_id, due_ms, status, claimed_by, claimed_ms, started_ms, finished_ms,
     timeout_s, attempts, result_notice, detail, created_ms)
    SELECT id, schedule_id, due_ms, status, claimed_by, claimed_ms, started_ms, finished_ms,
        timeout_s, attempts, result_notice, detail, created_ms
    FROM task_runs_before_014;
DROP TABLE task_runs_before_014;
CREATE UNIQUE INDEX task_runs_once ON task_runs (schedule_id, due_ms);
CREATE INDEX task_runs_queue ON task_runs (status, due_ms);
