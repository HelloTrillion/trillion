-- Spend caps (BRN-7, BRN-8): each usage row says which provider was paid, who spent
-- it, and what its quantity counts.
--
-- provider_id is the id a cap belongs to (see trillion.spend.prices): anthropic,
-- openai, local, custom:<host>, elevenlabs, deepgram or openai-voice:<host>.
-- caller is conversation (someone is talking) or background (scheduled work,
-- watchers, channels, titles).
-- units says what the four token columns hold. A model row keeps tokens there, as
-- before. A voice row (chars) or a hearing row (seconds) puts its characters or
-- seconds in input_tokens and leaves the other three at 0, so the sum of the four
-- columns is always the row's quantity in its unit. A NULL cost_usd still means
-- unpriced: such a row counts by that quantity, never as $0.
ALTER TABLE usage ADD COLUMN provider_id TEXT;
ALTER TABLE usage ADD COLUMN caller TEXT NOT NULL DEFAULT 'conversation'
    CHECK (caller IN ('conversation', 'background'));
ALTER TABLE usage ADD COLUMN units TEXT NOT NULL DEFAULT 'tokens'
    CHECK (units IN ('tokens', 'chars', 'seconds'));

-- Old rows name the provider class, not the cap: anthropic is its own id, but every
-- OpenAI-compatible model (OpenAI, local or custom) wrote "openai-compatible" with no
-- address, so which of them it was cannot be told now. Those keep that name, which no
-- cap uses: they still show in the usage pill, and count against no cap.
UPDATE usage SET provider_id = provider WHERE provider_id IS NULL;

-- The ledger and the usage pill read this month's rows for every provider at once: a
-- range on created_at, which an index with provider_id between could not seek.
CREATE INDEX usage_by_owner_time ON usage (owner_id, created_at);
