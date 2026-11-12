-- SPN-2a: a card shows everything its action sends or writes, exactly as it will go,
-- not just a one-line summary cut to 200 characters. That full detail is composed by
-- the host when the action is proposed and kept beside the full arguments it comes
-- from, so it is cleared with them 30 days after the action settles (SPN-17c) and is
-- never kept in the frozen row forever.

ALTER TABLE pending_action_args ADD COLUMN detail TEXT;

-- The one change allowed is still clearing, once, and now clearing takes the detail
-- with the arguments. Nothing can edit, restore or move either.
DROP TRIGGER pending_action_args_clear_only;
CREATE TRIGGER pending_action_args_clear_only BEFORE UPDATE ON pending_action_args
WHEN NOT (
        NEW.pending_id IS OLD.pending_id
    AND NEW.owner_id IS OLD.owner_id
    AND OLD.args_json IS NOT NULL
    AND NEW.args_json IS NULL
    AND NEW.detail IS NULL
    AND OLD.cleared_at IS NULL
    AND NEW.cleared_at IS NOT NULL
)
BEGIN SELECT RAISE(ABORT, 'approved arguments can only be cleared'); END;
