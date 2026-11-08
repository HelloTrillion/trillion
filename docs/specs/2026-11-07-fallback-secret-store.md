# The encrypted key file: keys on a machine with no keychain

**Date:** 2026-11-07 · **Requirements:** SEC-2, SEC-3, SEC-9, SEC-10, ONB-2a, ONB-4, ONB-5, OPS-2 · **Builds on:** SEC-1 (`secrets/store.py`, the keychain)

Today a key lives in the operating system's keychain or nowhere. A headless Linux
server has no Secret Service, and some managed laptops refuse writes to the keychain,
so on those machines the app runs at rung 0 and a key cannot be saved at all. This adds
the second store SEC-2 describes: one encrypted file in Trillion's own folder, chosen
by the user, never by surprise.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **The file is used when it exists, and only then.** `secrets.age` in Trillion's folder is made only by `trillion setup` (asked on a terminal when there is no usable keychain, or `--secrets-file`). When it exists it is the store for every key; when it does not, the keychain is, exactly as before. One store at a time. | Never move keys between stores silently. Which store holds a key is always one plain answer, and `doctor` says it. |
| D2 | **"No usable keychain" is what SEC-1 already says it is**: `keyring`'s `fail` backend, or any backend below priority 1 (the `null` backend tests use is -1). A keychain that says it works but refuses a write is not probed for on every start. The save fails with a message, and the user opts in with `trillion setup --secrets-file`. | No test writes to a real keychain, and no start writes to the user's. A refused save is already a clear error. |
| D3 | **Nothing is moved.** Making the file does not copy keys out of the keychain, and removing it does not copy keys back. Setup says so in one line: keys saved in the keychain before stay there and are no longer read; enter them again in the app. | D1. A copy the user did not see is a key in two places. |
| D4 | **Environment variables still win** (SEC-3). `TRILLION_ANTHROPIC_API_KEY` and friends are read before any store, as now. CI sets them and never needs the file. | Unchanged behaviour; one rule for both stores. |
| D5 | **The test suite keeps its keychain.** The file is only chosen when it exists under `TRILLION_HOME`. Tests set the null keychain and a temporary home, so they behave as before. A test opts in by making the file. | The existing suite must not change. |
| D6 | **Our own documented container, not the age format.** AES-256-GCM, a version byte, Argon2id parameters in the header, all through `cryptography` (already in `uv.lock`; its Argon2id is OpenSSL's, in the wheels for macOS, Linux and Windows). The file keeps the name `secrets.age` because the PRD, the deny floor and its tests name it; the bytes start with `TRLSEC`, not `age-encryption.org`. | age is X25519 or scrypt with ChaCha20-Poly1305; SEC-2 asks for AES-256-GCM and Argon2id. Reading age would need a second, Rust-built library for no gain. No crypto of our own: only the library's AES-GCM and Argon2id. |
| D7 | **Two keys.** A random 256-bit data key encrypts the contents. It is wrapped by a key-encryption key: a random key kept in the keychain (entry `trillion/secrets-file/key`) when one is usable, otherwise one derived from the user's passphrase with Argon2id (64 MiB, 3 passes, 4 lanes, 16-byte salt; RFC 9106's second recommendation). | SEC-2. Changing how the file is unlocked never re-encrypts the keys. |
| D8 | **The passphrase is typed or given, never kept.** `trillion setup` asks for it twice, never echoed. For unattended runs `TRILLION_SECRETS_PASSPHRASE` gives it; the CLI takes it out of the environment the moment it starts, so no tool, MCP server or child process inherits it. It is never logged, stored or printed, and the log scrubber treats `*PASSPHRASE` variables as credentials. | A passphrase in the environment is visible to processes of the same user and may sit in a service file; that is the risk, written in `SECURITY.md` and here. |
| D9 | **A locked file is not a crash** (ONB-4). `trillion run` unlocks at start: by the keychain, by the variable, or by asking once on a terminal (three tries). If it cannot, the app starts anyway, says one line about why, and every key reads as missing, so each feature that needs one says so as it does today. A save is refused with the reason. | The airplane test and rung 0 must hold whatever happened to the key file. |
| D10 | **Owner only, and refused when it is not.** Off Windows the file is `0600` and is refused, with the `chmod 600` that fixes it, when group or others can read it or another user owns it. It is opened without following a link and checked on the open handle. On Windows, `trillion.owner_only.restrict` sets an owner-only access list when that module is present (`windows/hardening`); until then the hook does nothing and Windows relies on the home folder's permissions, as the database does. | SEC-2's mode rule, without a check-then-open gap. |
| D11 | **Writes are atomic and failures never destroy.** A write goes to a temporary file beside it (`.secrets.age.*.tmp`, made `0600`), is flushed to disk, then renamed over the old one (OPS-2). A wrong passphrase, a changed byte (GCM tag) or a truncated file is a clear error, and the file is never rewritten, moved or deleted because of it. | One bad unlock must never cost every key. |
| D12 | **`doctor` names the store.** When the file is in use the Keychain line becomes a "Key file" line: its path, and unlocked (by the keychain or a passphrase), locked, wrong passphrase, damaged or too open, with the fix. `--secrets` lists the keys it holds as before, never a value (SEC-9, SEC-4). | SEC-9. |
| D13 | **The deny floor already refuses the file and its temporary file** (SEC-10): both sit in Trillion's own folder, which the floor protects whole. A spine test reads both by a file tool and checks the refusal is recorded in the audit log. | Proven, not assumed. |

## What the user sees

- A headless server: `trillion setup` says there is no system keychain and asks whether
  to keep keys in an encrypted file. Yes, then a passphrase twice. It ends
  "keys will be kept in ~/.trillion/secrets.age, unlocked by your passphrase".
- An unattended start: `TRILLION_SECRETS_PASSPHRASE=… trillion setup --secrets-file --no-run`,
  then `trillion run` with the same variable.
- `trillion run` prints `keys:    encrypted file, unlocked` beside the model and folders,
  or `keys:    encrypted file is locked (wrong passphrase): features that need a key are off`.
- `trillion doctor`: `ok    Key file  ~/.trillion/secrets.age, unlocked with a passphrase`,
  or `fail  Key file  … can be read by other users (mode 644)` with
  `-> Fix it with: chmod 600 ~/.trillion/secrets.age`.

## How

- `src/trillion/secrets/fallback.py`: `FileStore`, the `SecretStore` over the file;
  `choose()`, which returns the file store when the file exists and the keychain
  otherwise; `create()`; the container (`seal`/`open_sealed`); private read and atomic
  write.
- File layout, version 1. Header: `TRLSEC`, version `1`, how it is unlocked (`1`
  keychain, `2` passphrase), then for a passphrase the 16-byte salt and Argon2id passes,
  memory (KiB) and lanes as big-endian 32-bit numbers. Then the wrapped data key (12-byte
  nonce, 48 bytes: AES-256-GCM of the data key, the header as associated data), then the
  contents (12-byte nonce, AES-256-GCM of a JSON object of name → value, the header and
  wrapped key as associated data). A fresh random nonce on every write. Parameters above
  sane limits are refused as damaged.
- `cli/main.py`: takes the passphrase out of the environment; `setup --secrets-file`;
  `run` and `doctor` open the store with `choose()`.
- `cli/doctor.py`: the Key file line.
- `pyproject.toml`: `cryptography>=44` becomes a direct dependency (it was already
  installed through other packages; 44 is the first with Argon2id).

## Not now

- Moving keys between the keychain and the file. Delete the file to go back to the
  keychain; enter keys again.
- Changing the passphrase, or switching an existing file between keychain and passphrase.
- Two Trillion processes writing the file at the same moment: each write is atomic, but
  the later one wins.
- The page still says "no system keychain" when the file is locked; it does not yet know
  the file exists.
- Windows owner-only access lists wait for `trillion.owner_only` (D10).
