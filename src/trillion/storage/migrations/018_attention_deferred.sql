-- Spend caps (the spend caps spec): a find whose watcher was refused at a hard cap
-- before anything was said about it is deferred. Rows are never deleted, so the
-- status is widened instead.
--
-- Everything a watcher found, so each is told at most once, across restarts (D5).
-- claimed: found, and being told now (one left so by a stop or a failure is never told
-- again); told: it became a notice; silent: judged not worth saying (D6); dismissed:
-- the user set it aside, kept for the dedup window as a soft delete; deferred: refused
-- at a hard spend cap before a word was said, so not known, and claimed afresh by the
-- watcher's next read once the cap lifts.
--
-- Rebuilt to widen the CHECK, with each row's rowid kept: recent finds are listed in
-- rowid order within a time.
ALTER TABLE attention_finds RENAME TO attention_finds_before_018;
DROP INDEX attention_finds_recent;
CREATE TABLE attention_finds (
    owner_id       TEXT NOT NULL REFERENCES owners(id),
    key            TEXT NOT NULL,
    watcher        TEXT NOT NULL,
    first_seen_ms  INTEGER NOT NULL,
    -- Recorded before the notice is written, so a stop between the two can be put
    -- right at the next start: hence no foreign key.
    notice_id      TEXT,
    status         TEXT NOT NULL
        CHECK (status IN ('claimed', 'told', 'silent', 'dismissed', 'deferred')),
    dismissed_ms   INTEGER,
    -- What it was, in the host's words (as the notice's detail), for recent finds.
    detail         TEXT CHECK (detail IS NULL OR length(detail) <= 300),
    PRIMARY KEY (owner_id, key)
);
INSERT INTO attention_finds
    (rowid, owner_id, key, watcher, first_seen_ms, notice_id, status, dismissed_ms, detail)
    SELECT rowid, owner_id, key, watcher, first_seen_ms, notice_id, status, dismissed_ms,
        detail
    FROM attention_finds_before_018;
DROP TABLE attention_finds_before_018;
CREATE INDEX attention_finds_recent ON attention_finds (owner_id, first_seen_ms DESC);
