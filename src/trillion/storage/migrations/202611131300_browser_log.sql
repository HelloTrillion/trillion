-- Her browser's task log (the her browser spec, decision 13; CMP-9): what each browsing
-- task did, so "what did it actually do?" has an answer the chat alone can't give. It
-- holds origins and actions only, never a page's text and never a value typed into a
-- field: `origin` is a scheme and host, and `detail` is a short host-made word (a
-- refusal's kind, a card's answer), or the query given to the fixed search engine,
-- which the spec has the log show. Field values live only in the decision record of
-- the card that showed them.
--
-- Append-only, like the decisions. Nothing is ever deleted (SPN-9): at 30 days a row's
-- origin and detail are cleared, once, and only its event and time are left.
CREATE TABLE browser_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id  TEXT NOT NULL REFERENCES owners(id),
    task_id   TEXT NOT NULL,
    at        INTEGER NOT NULL,
    event     TEXT NOT NULL CHECK (event IN (
        'started', 'opened', 'searched', 'followed', 'back', 'typed', 'pressed', 'sent',
        'refused', 'chip', 'card', 'step', 'control', 'download', 'signin', 'done',
        'stopped'
    )),
    origin    TEXT CHECK (origin IS NULL OR length(origin) <= 300),
    detail    TEXT CHECK (detail IS NULL OR length(detail) <= 200)
);
CREATE INDEX browser_log_task ON browser_log (owner_id, task_id, id);
CREATE INDEX browser_log_at ON browser_log (owner_id, at);
CREATE TRIGGER browser_log_clear_only BEFORE UPDATE ON browser_log
WHEN NOT (
        NEW.id IS OLD.id
    AND NEW.owner_id IS OLD.owner_id
    AND NEW.task_id IS OLD.task_id
    AND NEW.at IS OLD.at
    AND NEW.event IS OLD.event
    AND NEW.origin IS NULL
    AND NEW.detail IS NULL
    AND OLD.at <= CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 30 * 86400000
)
BEGIN SELECT RAISE(ABORT, 'browser_log is append-only: a row 30 days old can only be cleared'); END;
CREATE TRIGGER browser_log_no_delete BEFORE DELETE ON browser_log
BEGIN SELECT RAISE(ABORT, 'browser_log is append-only'); END;
