-- Her board (SUR-1): the widgets she put up in a conversation's workspace, saved so
-- reopening the conversation brings them back. The board store is the one writer.
-- The JSON is drawn as text only: it can quote what she read (SPN-5).
CREATE TABLE boards (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    owner_id        TEXT NOT NULL REFERENCES owners(id),
    board_json      TEXT NOT NULL,
    updated_at      INTEGER NOT NULL
);
