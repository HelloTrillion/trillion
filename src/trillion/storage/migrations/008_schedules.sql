-- Reminders and timed asks (AGT-4, AGT-8, AGT-9, SUR-9). Times are epoch
-- milliseconds, except at_local, which is the user's own wall-clock time.
--
-- A schedule is what the user asked for. A run is one occurrence of it: at most one
-- per due time, however often the scheduler looks. A notice is anything waiting to
-- reach the user; only the door (delivery/door.py) turns one into something seen.

-- A timed ask runs in a conversation of its own, kept like any other, never opened
-- by a page.
ALTER TABLE conversations ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'
    CHECK (kind IN ('chat', 'scheduled'));

CREATE TABLE schedules (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL REFERENCES owners(id),
    kind         TEXT NOT NULL CHECK (kind IN ('reminder', 'ask')),
    -- Short, for lists, and for notices that carry none of her words.
    title        TEXT NOT NULL,
    -- Her words for a reminder; the request, for a timed ask.
    body         TEXT NOT NULL,
    zone         TEXT NOT NULL,
    -- The first time, local, YYYY-MM-DDTHH:MM. Every later one is worked out from it.
    at_local     TEXT NOT NULL,
    -- {"every": ...} as JSON; NULL runs once.
    repeat       TEXT,
    -- NULL once nothing more is due.
    next_due_ms  INTEGER,
    status       TEXT NOT NULL CHECK (status IN ('active', 'done', 'cancelled')),
    created_ms   INTEGER NOT NULL,
    -- The conversation it was set in.
    created_in   TEXT NOT NULL
);
CREATE INDEX schedules_due ON schedules (status, next_due_ms);

-- Words of hers are NULL for a missed or failed notice: those are shown with the
-- task's own title and never spoken (PER-11).
CREATE TABLE notices (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL REFERENCES owners(id),
    source        TEXT NOT NULL CHECK (source IN ('schedule')),
    source_id     TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('reminder', 'ask', 'missed', 'failed')),
    title         TEXT NOT NULL,
    text          TEXT,
    due_ms        INTEGER NOT NULL,
    late          INTEGER NOT NULL CHECK (late IN (0, 1)),
    created_ms    INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('held', 'delivered')),
    delivered_ms  INTEGER,
    -- The conversation it was delivered into.
    delivered_to  TEXT,
    -- When the system notification was shown, once.
    notified_ms   INTEGER
);
CREATE INDEX notices_held ON notices (owner_id, status, created_ms);

-- The queue (AGT-4). `claimed_by` is a per-process id: a run left running by another
-- process was interrupted, and is reclaimed. `detail` is a short reason, never content.
CREATE TABLE task_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id    TEXT NOT NULL REFERENCES schedules(id),
    due_ms         INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN (
                       'queued', 'running', 'delivered', 'late', 'missed', 'blocked',
                       'failed', 'interrupted', 'cancelled')),
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
CREATE UNIQUE INDEX task_runs_once ON task_runs (schedule_id, due_ms);
CREATE INDEX task_runs_queue ON task_runs (status, due_ms);
