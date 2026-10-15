-- Conversations grown too long for the model are summarized from the front (BRN-5).
--
-- No message is changed or removed: the page still shows all of them, and the
-- record of what was said stays whole. What the model is sent is the latest
-- summary for the conversation, then every message after `upto_message_id`.
-- The user's standing rules are not here and not in any summary: they live in
-- their own file and reach the model on every turn.

CREATE TABLE compactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    conversation_id  TEXT NOT NULL REFERENCES conversations(id),
    -- The last message the summary covers.
    upto_message_id  INTEGER NOT NULL REFERENCES messages(id),
    summary          TEXT NOT NULL,
    created_at       INTEGER NOT NULL
);
CREATE INDEX compactions_by_conversation ON compactions (conversation_id, id);
CREATE TRIGGER compactions_no_update BEFORE UPDATE ON compactions
BEGIN SELECT RAISE(ABORT, 'compactions are append-only'); END;
CREATE TRIGGER compactions_no_delete BEFORE DELETE ON compactions
BEGIN SELECT RAISE(ABORT, 'compactions are append-only'); END;
