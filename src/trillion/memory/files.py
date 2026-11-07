"""Memories as files the user can open (MEM-1).

Each memory is one Markdown file: a few `key: value` lines, then the text. The
files are the source of truth; the database is an index over them. They are
written whole, through a temporary file and a rename, and read back to confirm
(OPS-2): a sync client can cut an append short, and the loss shows up weeks
later as a memory that is quietly gone.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from trillion.memory.types import MAX_TEXT, SOURCES, STATUSES, TYPES, Memory

_ID = re.compile(r"^mem_[0-9a-f]{12}$")
_FIELD = re.compile(r"^([a-z_]+):\s*(.*?)\s*$")
_FENCE = "---"
# A file larger than this is not one of ours: it is skipped, never truncated.
_MAX_FILE_BYTES = 16_384


class MemoryFileError(ValueError):
    """A file that is not a well-formed memory. The message names what is wrong, not the text."""


def new_id() -> str:
    return f"mem_{uuid.uuid4().hex[:12]}"


def valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID.match(value) is not None


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{ms % 1000:03d}Z"
    )


def _unstamp(raw: str) -> int:
    moment = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return int(moment.timestamp()) * 1000 + moment.microsecond // 1000


def clean(text: str) -> str:
    """The text as it is stored: trimmed, and with no line that could end the header."""
    # Invisible characters go first. Dropped after trimming, one at the end of a line
    # would leave the space before it behind, and the text would change again on reload.
    seen = "".join(ch for ch in text if ch in "\r\n\t" or ch.isprintable())
    lines = [line.rstrip() for line in seen.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def render(m: Memory) -> str:
    head = [
        f"id: {m.id}",
        f"type: {m.type}",
        f"source: {m.source}",
        f"moves: {'true' if m.moves else 'false'}",
        f"status: {m.status}",
        f"version: {m.version}",
        f"created: {_stamp(m.created_at)}",
        f"as_of: {_stamp(m.as_of)}",
    ]
    if m.supersedes:
        head.append(f"supersedes: {m.supersedes}")
    if m.superseded_by:
        head.append(f"superseded_by: {m.superseded_by}")
    if m.origin:
        head.append(f"origin: {m.origin}")
    return "\n".join([_FENCE, *head, _FENCE, "", m.text, ""])


def parse(raw: str) -> Memory:
    lines = raw.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != _FENCE:
        raise MemoryFileError("no header")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == _FENCE)
    except StopIteration:
        raise MemoryFileError("the header is not closed") from None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        m = _FIELD.match(line)
        if m:
            fields[m.group(1)] = m.group(2)
    text = clean("\n".join(lines[end + 1 :]))
    try:
        memory = Memory(
            id=fields["id"],
            type=fields["type"],  # type: ignore[arg-type]
            source=fields["source"],  # type: ignore[arg-type]
            moves={"true": True, "false": False}[fields["moves"]],
            status=fields.get("status", "active"),  # type: ignore[arg-type]
            version=int(fields.get("version", "1")),
            created_at=_unstamp(fields["created"]),
            as_of=_unstamp(fields.get("as_of", fields["created"])),
            supersedes=fields.get("supersedes") or None,
            superseded_by=fields.get("superseded_by") or None,
            origin=fields.get("origin") or None,
            text=text,
        )
    except KeyError as e:
        raise MemoryFileError(f"missing or bad field: {e.args[0]}") from None
    except ValueError:
        raise MemoryFileError("a number or a date is malformed") from None
    problem = check(memory)
    if problem:
        raise MemoryFileError(problem)
    return memory


def check(m: Memory) -> str | None:
    """Why this is not a memory that can be kept, or None."""
    if not valid_id(m.id):
        return "bad id"
    if m.type not in TYPES or m.source not in SOURCES or m.status not in STATUSES:
        return "unknown type, source or status"
    if not m.text or len(m.text) > MAX_TEXT:
        return "the text is empty or too long"
    if m.version < 1 or m.created_at < 0 or m.as_of < 0:
        return "bad version or date"
    for link in (m.supersedes, m.superseded_by):
        if link is not None and (not valid_id(link) or link == m.id):
            return "bad link to another memory"
    if m.origin is not None and not _one_line(m.origin):
        return "bad origin"
    return None


MAX_ORIGIN = 200


def _one_line(text: str) -> bool:
    return 0 < len(text) <= MAX_ORIGIN and text == text.strip() and text.isprintable()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def path_for(root: Path, m: Memory) -> Path:
    return root / m.type / f"{m.id}.md"


def write(root: Path, m: Memory) -> str:
    """Write one memory whole and confirm it. Returns the digest of what is on disk."""
    target = path_for(root, m)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = render(m)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if target.read_text(encoding="utf-8") != text:
        raise OSError("the memory file did not read back as written")
    return digest(text)


@dataclass(frozen=True, kw_only=True)
class Found:
    memory: Memory
    digest: str


@dataclass(frozen=True, kw_only=True)
class Scan:
    found: tuple[Found, ...]
    # Files that are there but could not be understood, by name. Never deleted.
    unreadable: tuple[str, ...]
    # False when a folder could not be listed at all: then nothing absent may be
    # taken as gone (MEM-6b).
    complete: bool


def scan(root: Path) -> Scan:
    found: dict[str, Found] = {}
    unreadable: list[str] = []
    complete = True
    for kind in TYPES:
        folder = root / kind
        try:
            if not folder.is_dir():
                continue
            entries = sorted(p for p in folder.iterdir() if p.suffix == ".md")
        except OSError:
            complete = False
            continue
        for path in entries:
            try:
                if path.is_symlink() or path.stat().st_size > _MAX_FILE_BYTES:
                    raise MemoryFileError("not a plain memory file")
                raw = path.read_text(encoding="utf-8")
                memory = parse(raw)
                if path.stem != memory.id or memory.id in found:
                    raise MemoryFileError("the name and the id disagree")
                if memory.type != kind:
                    # Moved by hand to another folder: the folder is what the user sees.
                    memory = replace(memory, type=kind)
                found[memory.id] = Found(memory=memory, digest=digest(raw))
            except (OSError, UnicodeError, MemoryFileError):
                unreadable.append(f"{kind}/{path.name}")
    return Scan(found=tuple(found.values()), unreadable=tuple(unreadable), complete=complete)
