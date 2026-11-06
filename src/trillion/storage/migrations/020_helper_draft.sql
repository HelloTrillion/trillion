-- What a helper's card drafted beyond its name, job and notes (the your helpers spec,
-- Creating one): `connectors`, the Connected tools entries its job would use, by their
-- catalog ids; `asks`, what it asks the user first, in its hello. Both JSON arrays of
-- text, empty for a helper made before this.
ALTER TABLE helpers ADD COLUMN connectors TEXT NOT NULL DEFAULT '[]'
    CHECK (CASE WHEN json_valid(connectors) THEN json_type(connectors) = 'array' ELSE 0 END);
ALTER TABLE helpers ADD COLUMN asks TEXT NOT NULL DEFAULT '[]'
    CHECK (CASE WHEN json_valid(asks) THEN json_type(asks) = 'array' ELSE 0 END);
