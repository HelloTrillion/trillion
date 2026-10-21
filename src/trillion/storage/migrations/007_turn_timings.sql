-- How long each turn took, for `trillion doctor --trace` (ONB-11) and the latency
-- bench (EVL-3). Numbers only: nothing anyone said is kept here.
--
-- Every *_ms column counts milliseconds from the start of the turn: the end of the
-- user's speech for a spoken turn, the moment the message arrived for any other.
-- NULL means it never happened in that turn (no voice, no audio, interrupted).
--
--   endpoint_ms        how long the quiet lasted before listening decided they had
--                      finished (spoken turns; part of heard_ms, not added to it)
--   heard_ms           the words were ready to answer
--   first_text_ms      her first words were on screen
--   first_sentence_ms  her first sentence was handed to the voice
--   first_audio_ms     the first audio of it was sent to the page
--   played_ms          the page reported that first audio audible
--   saved_ms           the reply was complete and saved
--
-- calls_json lists each model call in the turn: when it started, its first token
-- and its end (all from the turn's start), and its tokens, cached ones included.
-- A row is a few hundred bytes: a year of daily use is a few megabytes.

CREATE TABLE turn_timings (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id           TEXT NOT NULL REFERENCES owners(id),
    conversation_id    TEXT NOT NULL REFERENCES conversations(id),
    turn               INTEGER NOT NULL,
    via                TEXT NOT NULL CHECK (via IN ('voice', 'text', 'tap', 'greet')),
    -- 1 for the first turn after the page connected: connections are still cold.
    first_in_session   INTEGER NOT NULL,
    provider           TEXT NOT NULL,
    model              TEXT NOT NULL,
    -- The voice and the ears in use, by kind. NULL when off (or muted, for the voice).
    voice              TEXT,
    ears               TEXT,
    stop               TEXT NOT NULL,
    tools              INTEGER NOT NULL,
    endpoint_ms        INTEGER,
    heard_ms           INTEGER,
    first_text_ms      INTEGER,
    first_sentence_ms  INTEGER,
    first_audio_ms     INTEGER,
    played_ms          INTEGER,
    saved_ms           INTEGER,
    calls_json         TEXT NOT NULL,
    created_at         INTEGER NOT NULL
);
CREATE INDEX turn_timings_by_owner ON turn_timings (owner_id, id);
