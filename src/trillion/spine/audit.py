"""SPN-12: what the audit log records. Argument hashes only, never argument values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class AuditRecord:
    owner_id: str
    call_id: str
    tool: str
    args_hash: str
    risk: str
    decision: str
    outcome: str
    duration_ms: int
    session_id: str
    at: int
    # SPN-6: what the neutralizer removed from the result, as JSON counts by kind
    # (`{"fence": 1, "role_marker": 2}`), or None. Never the text it removed.
    neutralized: str | None = None


KEY_BYTES = 32


def audit_key(path: Path) -> bytes:
    """This install's audit key, made on first use and kept owner-only (#4).

    It only has to stay the same across restarts so that two audit rows for the same
    arguments still match. It is not a secret anyone else needs: it never leaves the
    machine, and losing it only means older rows can no longer be matched to newer."""
    try:
        key = path.read_bytes()
        if len(key) == KEY_BYTES:
            return key
    except FileNotFoundError:
        pass
    key = os.urandom(KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key
