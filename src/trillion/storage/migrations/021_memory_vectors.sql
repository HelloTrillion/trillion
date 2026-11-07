-- What each memory means, as numbers, for recall by meaning (MEM-2).
--
-- Derived data: made from a memory's text by a local model, remade whenever that
-- text changes, and never the truth about anything (the files are, MEM-1). A row
-- counts only while `text_digest` matches the memory's text now and `model` is the
-- model in use. `vector` is little-endian float32, one per dimension, unit length.

CREATE TABLE memory_vectors (
    memory_id    TEXT PRIMARY KEY REFERENCES memories(id),
    owner_id     TEXT NOT NULL REFERENCES owners(id),
    model        TEXT NOT NULL,
    -- SHA-256 of the text the vector was made from.
    text_digest  TEXT NOT NULL,
    dimensions   INTEGER NOT NULL CHECK (dimensions > 0),
    vector       BLOB NOT NULL CHECK (length(vector) = 4 * dimensions),
    updated_at   INTEGER NOT NULL
);
CREATE INDEX memory_vectors_owner ON memory_vectors (owner_id, model);
