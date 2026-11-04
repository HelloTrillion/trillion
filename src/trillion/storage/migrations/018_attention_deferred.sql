-- Spend caps (the spend caps spec): a find whose watcher was refused at a hard cap
-- before anything was said about it is deferred. It is not told, not silent and not
-- lost: the watcher's next read, once the cap lifts, claims it afresh. Rows are never
-- deleted, so the status is widened instead.
CREATE TABLE attention_finds_before_018 AS SELECT * FROM attention_finds;
DROP TABLE attention_finds;
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
    (owner_id, key, watcher, first_seen_ms, notice_id, status, dismissed_ms, detail)
    SELECT owner_id, key, watcher, first_seen_ms, notice_id, status, dismissed_ms, detail
    FROM attention_finds_before_018;
DROP TABLE attention_finds_before_018;
CREATE INDEX attention_finds_recent ON attention_finds (owner_id, first_seen_ms DESC);
