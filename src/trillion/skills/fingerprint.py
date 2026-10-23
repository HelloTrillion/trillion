"""One hash over a skill's files: what the user approved when they turned it on."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from trillion.skills.parse import _is_link, open_regular, walk


def _field(digest: hashlib._Hash, data: bytes) -> None:
    # Length first, so no file's bytes can pose as the next entry's fields.
    digest.update(len(data).to_bytes(8, "big") + data)


def _entry(path: Path) -> tuple[bytes, bytes]:
    """(kind, digest of the content) for one entry. Links are hashed by where they
    point and never followed; pipes and devices are never opened."""
    if _is_link(path):
        try:  # readlink also reads a junction's target
            return b"link", hashlib.sha256(os.fsencode(os.readlink(path))).digest()
        except OSError:
            return b"link?", b""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return b"special", b""
        with open_regular(path) as f:
            return b"file", hashlib.file_digest(f, "sha256").digest()
    except OSError:
        return b"unreadable", b""


def fingerprint(folder: Path) -> str:
    """SHA-256 over every entry's kind, relative path and content digest, in sorted
    order, each length-prefixed. Retargeting a symlink changes it."""
    digest = hashlib.sha256()
    for rel, path, _ in walk(folder):
        kind, content = _entry(path)
        _field(digest, kind)
        _field(digest, os.fsencode(rel))  # names that aren't UTF-8 still hash
        _field(digest, content)
    return "sha256:" + digest.hexdigest()
