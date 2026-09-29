-- #20: approved arguments, including whole file contents, were kept forever in the
-- frozen pending_actions.args_json. From here on that column holds a slim copy (long
-- strings replaced by their SHA-256 and length; args_hash still covers the full
-- arguments), and the full arguments live here, kept for thirty days after the action
-- settles and then cleared (spine/retention.py).
--
-- Rows written before this migration keep their full arguments in pending_actions and
-- have no row here; they are read as before and are not cleared. There are no
-- installs yet that this matters for.

CREATE TABLE pending_action_args (
    pending_id  TEXT PRIMARY KEY REFERENCES pending_actions(id),
    owner_id    TEXT NOT NULL REFERENCES owners(id),
    args_json   TEXT,       -- the full arguments; NULL once cleared
    cleared_at  INTEGER     -- when they were cleared
);

-- The one change allowed: clearing the arguments, once. They can never be edited,
-- restored, or moved to another action or owner.
CREATE TRIGGER pending_action_args_clear_only BEFORE UPDATE ON pending_action_args
WHEN NOT (
        NEW.pending_id IS OLD.pending_id
    AND NEW.owner_id IS OLD.owner_id
    AND OLD.args_json IS NOT NULL
    AND NEW.args_json IS NULL
    AND OLD.cleared_at IS NULL
    AND NEW.cleared_at IS NOT NULL
)
BEGIN SELECT RAISE(ABORT, 'approved arguments can only be cleared'); END;

CREATE TRIGGER pending_action_args_no_delete BEFORE DELETE ON pending_action_args
BEGIN SELECT RAISE(ABORT, 'pending_action_args is append-only'); END;
