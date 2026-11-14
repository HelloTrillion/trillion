-- SPN-6: the neutralizer reports attempted injections. What it removed from a tool's
-- result is kept with the call's audit row, as counts by kind in JSON
-- (`{"fence":1,"role_marker":2}`), or NULL when it removed nothing or the call had no
-- result. Never the removed text: that is someone else's hostile words.
--
-- The audit stays append-only: its triggers refuse UPDATE and DELETE, and adding a
-- nullable column is neither. Rows written before this read NULL.
ALTER TABLE tool_audit ADD COLUMN neutralized TEXT;
