-- Whether a spoken turn used a draft started while the user was finishing (VOI-12):
-- used, dropped (one was started and let go) or none. Only that word: a draft's
-- text is never kept anywhere.
ALTER TABLE turn_timings ADD COLUMN draft TEXT NOT NULL DEFAULT 'none'
    CHECK (draft IN ('used', 'dropped', 'none'));
