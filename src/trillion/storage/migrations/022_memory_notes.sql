-- Notes from chats (MEM-4): which messages of a conversation the memory noter has
-- read. Once a conversation goes quiet she reads what was said since her last pass
-- and keeps the few lasting facts in it as memories of her own inference. One row
-- per pass, so the same words are never read twice.
--
-- outcome:
--   saved    at least one memory was kept
--   nothing  read, and nothing worth keeping (or all of it already known)
--   failed   the model gave no usable answer; these messages are not read again
-- A pass refused at a spend cap is not recorded: it is read once the cap lifts.
-- Append-only, like the summaries in `compactions`.
CREATE TABLE memory_notes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    conversation_id  TEXT NOT NULL REFERENCES conversations(id),
    -- The last message the pass read.
    upto_message_id  INTEGER NOT NULL REFERENCES messages(id),
    outcome          TEXT NOT NULL CHECK (outcome IN ('saved', 'nothing', 'failed')),
    saved            INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL
);
CREATE INDEX memory_notes_by_conversation ON memory_notes (conversation_id, id);
CREATE TRIGGER memory_notes_no_update BEFORE UPDATE ON memory_notes
BEGIN SELECT RAISE(ABORT, 'memory_notes are append-only'); END;
CREATE TRIGGER memory_notes_no_delete BEFORE DELETE ON memory_notes
BEGIN SELECT RAISE(ABORT, 'memory_notes are append-only'); END;
