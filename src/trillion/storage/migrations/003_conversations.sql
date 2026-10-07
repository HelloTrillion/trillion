-- Conversations (Track D). Transcripts live only here, in the database the user
-- owns; they are never written to a log. Times are epoch milliseconds.

CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL REFERENCES owners(id),
    created_at  INTEGER NOT NULL,
    deleted_at  INTEGER  -- soft delete only (SPN-9)
);

-- One row per message, in the order it was said. `kind` records who committed it:
--   user          the user typed or said it; advances the turn counter (VOI-15a)
--   tap           the user tapped a confirmation card; also a committed user input
--   assistant     the model's reply, with any tool calls it made
--   tool_results  what the spine returned for those calls
CREATE TABLE messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         TEXT NOT NULL REFERENCES owners(id),
    conversation_id  TEXT NOT NULL REFERENCES conversations(id),
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    kind             TEXT NOT NULL CHECK (kind IN ('user', 'tap', 'assistant', 'tool_results')),
    blocks_json      TEXT NOT NULL,
    -- An assistant turn exactly as its provider returned it, replayed to that
    -- provider unchanged (signed reasoning is only valid that way). NULL otherwise.
    raw_json         TEXT,
    created_at       INTEGER NOT NULL
);
CREATE INDEX messages_by_conversation ON messages (conversation_id, id);

-- Tokens per model turn (BRN-4). cost_usd is NULL when the model's price is not
-- known: an unknown cost is recorded as unknown, never as zero.
CREATE TABLE usage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id            TEXT NOT NULL REFERENCES owners(id),
    conversation_id     TEXT REFERENCES conversations(id),
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    cache_read_tokens   INTEGER NOT NULL,
    cache_write_tokens  INTEGER NOT NULL,
    cost_usd            REAL,
    created_at          INTEGER NOT NULL
);
