-- Settings the user chose (which model, which endpoint). Never a credential:
-- keys live in the operating system's keychain (SEC-1, SEC-4).

CREATE TABLE preferences (
    owner_id    TEXT NOT NULL REFERENCES owners(id),
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (owner_id, key)
);
