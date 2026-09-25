"""One canonical form for arguments, used by pending actions, audit and the loop guard."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def args_hash(args: Any) -> str:
    return hashlib.sha256(canonical_json(args).encode("utf-8")).hexdigest()
