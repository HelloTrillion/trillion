-- A spoken turn's listening cues (PER-11): how many were sent and heard, and for each
-- recognizer report that got none, the first rule that said no. Only counts and rule
-- names, as JSON: nothing anyone said. NULL when cues were not possible.
ALTER TABLE turn_timings ADD COLUMN cues_json TEXT;
