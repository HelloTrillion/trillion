-- At startup the spine looks for sends a check left in doubt lately (OPS-1): decisions
-- by their event and time, so that look stays quick however long the record grows.
CREATE INDEX decisions_event_at ON decisions (event, at);
