"""How long approved arguments are kept, and what the frozen record keeps forever (#20).

A proposal's full arguments can hold a whole file. The frozen row keeps a slim copy —
every long string replaced by its SHA-256 and length — so what was approved stays
readable (the path, the amount, the target) and provable (`args_hash` covers the full
arguments). The full arguments are kept for thirty days after the action settles, then
cleared.
"""

from __future__ import annotations

import hashlib
from typing import Any

from trillion.spine.confirm import FINAL, Status

RETENTION_DAYS = 30
RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000

# Strings longer than this are replaced in the slim copy. Paths and short notes stay.
SLIM_OVER = 256

# An action that can no longer run on its own. A proposal still waiting for a yes, or
# one approved or executing, keeps everything it would need.
SETTLED: frozenset[Status] = FINAL | {Status.SUPERSEDED, Status.EXPIRED, Status.INVALIDATED}


def slim(value: Any) -> Any:
    """`value` with every long string replaced by a fingerprint of it."""
    if isinstance(value, str) and len(value) > SLIM_OVER:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {"$omitted": {"sha256": digest, "chars": len(value)}}
    if isinstance(value, dict):
        return {k: slim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [slim(v) for v in value]
    return value
