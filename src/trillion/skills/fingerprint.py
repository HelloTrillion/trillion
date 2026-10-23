"""One hash over a skill's files: what the user approved when they turned it on."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from trillion.skills.parse import walk


def fingerprint(folder: Path) -> str:
    """SHA-256 over every file's relative path and bytes, in sorted order. A symlink
    is hashed by where it points, never followed, so retargeting one changes it."""
    digest = hashlib.sha256()
    for rel, path, _ in walk(folder):
        digest.update(rel.encode() + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.readlink(path).encode())
        else:
            try:
                digest.update(b"file\0" + path.read_bytes())
            except OSError:
                digest.update(b"unreadable\0")
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()
