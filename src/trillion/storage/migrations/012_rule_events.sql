-- Standing rules, kept and dropped (BRN-5, SPN-17). The rules file says which rules
-- hold now; this says how each one came and went, so "did that ever get approved?"
-- has an answer after the chat is cleared, and a dropped rule can be brought back
-- in its own words. A rule kept at once by the user's own "never" has no card, so
-- without this it would have no record at all. Append-only, like the decisions.
CREATE TABLE rule_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    TEXT NOT NULL REFERENCES owners(id),
    rule_id     TEXT NOT NULL,
    text        TEXT NOT NULL,
    event       TEXT NOT NULL CHECK (event IN ('kept', 'dropped')),
    -- said: kept at once, in the user's words, because it only narrows what she does.
    -- card: the user approved it on a card.
    how         TEXT NOT NULL CHECK (how IN ('said', 'card')),
    session_id  TEXT NOT NULL,
    at          INTEGER NOT NULL
);
CREATE INDEX rule_events_owner ON rule_events (owner_id, at);
CREATE TRIGGER rule_events_no_update BEFORE UPDATE ON rule_events
BEGIN SELECT RAISE(ABORT, 'rule_events are append-only'); END;
CREATE TRIGGER rule_events_no_delete BEFORE DELETE ON rule_events
BEGIN SELECT RAISE(ABORT, 'rule_events are append-only'); END;
