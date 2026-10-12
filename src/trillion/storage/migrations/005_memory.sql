-- What she remembers between conversations (PRD 6.2).
--
-- Each row mirrors one Markdown file under the data folder's memory directory.
-- The files are the source of truth (MEM-1): this table is an index over them,
-- and where the two disagree the file wins and the mismatch is reported.
-- Nothing here is ever deleted (SPN-9): a memory is retracted or superseded.

CREATE TABLE memories (
    id             TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL REFERENCES owners(id),
    type           TEXT NOT NULL CHECK (type IN ('user', 'feedback', 'project', 'reference')),
    text           TEXT NOT NULL,
    source         TEXT NOT NULL
                   CHECK (source IN ('user_statement', 'imported_claim', 'agent_inference')),
    moves          INTEGER NOT NULL CHECK (moves IN (0, 1)),
    status         TEXT NOT NULL CHECK (status IN ('active', 'retracted', 'superseded')),
    version        INTEGER NOT NULL CHECK (version >= 1),
    created_at     INTEGER NOT NULL,
    as_of          INTEGER NOT NULL,
    supersedes     TEXT,
    superseded_by  TEXT,
    -- SHA-256 of the file this row was read from or written to.
    file_digest    TEXT NOT NULL,
    -- 1 when the file was not found by a complete scan. The row is kept and is
    -- not recalled: a missing file is reported, never treated as a deletion.
    file_missing   INTEGER NOT NULL DEFAULT 0 CHECK (file_missing IN (0, 1)),
    updated_at     INTEGER NOT NULL
);
CREATE INDEX memories_owner ON memories (owner_id, status);
