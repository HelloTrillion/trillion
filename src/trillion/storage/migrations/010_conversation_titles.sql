-- Conversation titles and recency, for the thread list (SUR-1). The store is their
-- one writer. A title is shown as text only: it can hold the user's words or the
-- model's, so the page never treats it as markup.
ALTER TABLE conversations ADD COLUMN title TEXT;
-- 'words': the user's first words; 'model': she named it; 'user': the user renamed it.
ALTER TABLE conversations ADD COLUMN title_source TEXT
    CHECK (title_source IN ('words', 'model', 'user'));
-- Epoch ms of the newest message; NULL until the first one. The list sorts on it.
ALTER TABLE conversations ADD COLUMN last_message_at INTEGER;
CREATE INDEX conversations_by_recent
    ON conversations (owner_id, kind, deleted_at, last_message_at DESC);

-- Existing conversations: recency from their newest message, and a title from the
-- first thing the user typed or said.
UPDATE conversations SET last_message_at =
    (SELECT MAX(created_at) FROM messages m WHERE m.conversation_id = conversations.id);
UPDATE conversations SET
    title = (
        SELECT trim(substr(trim(replace(replace(replace(json_extract(b.value, '$.text'), char(10), ' '), char(13), ' '), char(9), ' ')), 1, 60))
        FROM messages m, json_each(m.blocks_json) b
        WHERE m.conversation_id = conversations.id AND m.kind = 'user'
          AND json_extract(b.value, '$.t') = 'text'
        ORDER BY m.id LIMIT 1
    ),
    title_source = 'words'
WHERE EXISTS (
    SELECT 1 FROM messages m, json_each(m.blocks_json) b
    WHERE m.conversation_id = conversations.id AND m.kind = 'user'
      AND json_extract(b.value, '$.t') = 'text');
