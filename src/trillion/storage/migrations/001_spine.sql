-- Spine tables (Track C). Every table carries owner_id so a hosted tier is a
-- deployment change, not a rewrite (PRD §8.4). Times are epoch milliseconds.

CREATE TABLE owners (
    id          TEXT PRIMARY KEY,
    created_at  INTEGER NOT NULL
);
INSERT INTO owners (id, created_at) VALUES ('local', 0);

CREATE TABLE pending_actions (
    id                TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL REFERENCES owners(id),
    tool              TEXT NOT NULL,
    args_json         TEXT NOT NULL,
    args_hash         TEXT NOT NULL,
    summary           TEXT NOT NULL,
    target            TEXT NOT NULL,
    risk              TEXT NOT NULL CHECK (risk IN ('irreversible', 'spend')),
    recoverable       INTEGER NOT NULL CHECK (recoverable IN (0, 1)),
    session_id        TEXT NOT NULL,
    created_turn      INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN (
                          'proposed', 'approved', 'executing', 'succeeded', 'failed',
                          'unknown', 'rejected', 'superseded', 'expired', 'invalidated')),
    expires_at        INTEGER NOT NULL,
    represented_from  TEXT REFERENCES pending_actions(id),
    effect_status     TEXT,
    outcome_detail    TEXT,
    precondition_token TEXT,  -- Tool.fingerprint() at proposal; compared before execution
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);
CREATE INDEX pending_actions_open
    ON pending_actions (owner_id, session_id, tool, target, status);
CREATE INDEX pending_actions_status ON pending_actions (status);

-- SPN-17: one immutable row per resolution event.
CREATE TABLE decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    TEXT NOT NULL REFERENCES owners(id),
    pending_id  TEXT NOT NULL REFERENCES pending_actions(id),
    event       TEXT NOT NULL,
    decided_by  TEXT NOT NULL,
    via         TEXT NOT NULL,
    at          INTEGER NOT NULL
);
CREATE INDEX decisions_pending ON decisions (pending_id);
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;

-- delivered_at is written once, when the action renders on a surface.
CREATE TABLE decision_deliveries (
    pending_id    TEXT PRIMARY KEY REFERENCES pending_actions(id),
    owner_id      TEXT NOT NULL REFERENCES owners(id),
    surface       TEXT NOT NULL,
    delivered_at  INTEGER NOT NULL
);
CREATE TRIGGER decision_deliveries_no_update BEFORE UPDATE ON decision_deliveries
BEGIN SELECT RAISE(ABORT, 'decision_deliveries are append-only'); END;
CREATE TRIGGER decision_deliveries_no_delete BEFORE DELETE ON decision_deliveries
BEGIN SELECT RAISE(ABORT, 'decision_deliveries are append-only'); END;

-- SPN-12: argument hashes only.
CREATE TABLE tool_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     TEXT NOT NULL REFERENCES owners(id),
    call_id      TEXT NOT NULL,
    tool         TEXT NOT NULL,
    args_hash    TEXT NOT NULL,
    risk         TEXT NOT NULL,
    decision     TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL,
    session_id   TEXT NOT NULL,
    at           INTEGER NOT NULL
);
CREATE TRIGGER tool_audit_no_update BEFORE UPDATE ON tool_audit
BEGIN SELECT RAISE(ABORT, 'tool_audit is append-only'); END;
CREATE TRIGGER tool_audit_no_delete BEFORE DELETE ON tool_audit
BEGIN SELECT RAISE(ABORT, 'tool_audit is append-only'); END;
