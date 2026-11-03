"""File tools in folders the user granted. Every write is `irreversible` and asks first (D4)."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import os
from pathlib import Path

from trillion.spine import deny_floor
from trillion.spine.contract import (
    ASKS_BY_CARD,
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.lossless import decode
from trillion.tools import beneath
from trillion.tools.beneath import Escaped

MAX_READ_BYTES = 1_000_000
MAX_LIST_ENTRIES = 500
_PREVIEW_CHARS = 60
_EDIT_PREVIEW_CHARS = 40

_PATH_ONLY = {
    "type": "object",
    "properties": {"path": {"type": "string", "minLength": 1}},
    "required": ["path"],
    "additionalProperties": False,
}
_PATH_AND_CONTENT = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "content": {"type": "string", "maxLength": MAX_READ_BYTES},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}
_PATH_OLD_NEW = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "old": {"type": "string", "minLength": 1, "maxLength": MAX_READ_BYTES},
        "new": {"type": "string", "maxLength": MAX_READ_BYTES},
    },
    "required": ["path", "old", "new"],
    "additionalProperties": False,
}


def human_size(n: int) -> str:
    if n < 1000:
        return f"{n} bytes"
    if n < 1_000_000:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _path(args: JSON) -> Path:
    return Path(str(args["path"])).expanduser().resolve(strict=False)


def _text(args: JSON, key: str = "content") -> str:
    """What the model wrote, with any ⟦U+XXXX⟧ token from a lossless read restored to its
    character — so writing back what `file_read` showed reproduces the file exactly."""
    return decode(str(args[key]))


def _bytes(args: JSON) -> bytes:
    return _text(args).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _content_fingerprint(args: JSON) -> str:
    p = _path(args)
    if not p.is_file():
        return "missing"
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _where(p: Path) -> str:
    """The whole destination, from home when it is under home (SPN-2a). The folder name
    alone could not tell two granted folders called Documents apart."""
    try:
        return "~/" + p.relative_to(Path.home()).as_posix()
    except ValueError:
        return p.as_posix()


def _folder(p: Path) -> str:
    """A folder was named where a file was meant: say so, with a file name to use."""
    return f"{_where(p)} is a folder; name a file inside it, such as {_where(p / 'note.txt')}"


# Told to the model with each write tool: the path is the file, never its folder.
_NAME_A_FILE = (
    "`path` names a file inside a granted folder, not a folder, such as ~/Desktop/note.txt. "
)


def _preview(text: str, limit: int = _PREVIEW_CHARS) -> str:
    """The start of some content, on one line, quoted, and marked when cut. The router
    neutralizes and bounds the whole card; this only makes it readable."""
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "…"
    return f"“{flat}”"


def _read_text(p: Path) -> str | None:
    """The file as text, or None if it is too big or not UTF-8 (describe must not raise)."""
    try:
        if p.stat().st_size > MAX_READ_BYTES:
            return None
        return p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _line_changes(old: str, new: str) -> str:
    """How much of the file an overwrite changes, and where it starts."""
    a, b = old.splitlines(), new.splitlines()
    changed, first = 0, None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        changed += max(i2 - i1, j2 - j1)
        first = i1 + 1 if first is None else first
    if first is None:
        return "no lines change"
    return f"{changed} of {len(a)} lines change, first at line {first}"


def _target(tool: BaseTool, args: JSON, ctx: ToolContext) -> Path:
    """The path a tool may act on now, or Escaped (#2).

    The router checked the floor when the action was approved; the folders on the way
    could have changed since. The path is taken again, and the floor applied to it
    again, here. Everything after this opens by walking down from the granted folder
    (`beneath`), so a link that appears after this moment is refused, not followed."""
    raw = Path(str(args["path"])).expanduser()
    target = raw.parent.resolve(strict=False) / raw.name
    denial = deny_floor.check(
        tool.definition,
        {"path": str(target)},
        deny_floor.DenyPolicy.default(),
        ctx.granted_roots,
        read_only_roots=ctx.read_only_roots,
    )
    if denial is not None:
        raise Escaped(denial.reason)
    return target


def _unsafe(e: OSError) -> ToolResult:
    """The file could not be opened inside its folder: nothing was read or written."""
    said = str(e) if isinstance(e, Escaped) else "the file could not be opened safely"
    return ToolResult(ok=False, content=said, effect="none", error_kind=type(e).__name__)


def _matches(p: Path, data: bytes, done: str, ctx: ToolContext | None = None) -> ToolResult:
    """Read the file back: success needs evidence (SPN-17a). With `ctx`, it is read
    back the same way it was written: walked to from its granted folder."""
    try:
        found = (
            beneath.read_bytes(p, ctx.granted_roots, len(data) + 1)
            if ctx is not None
            else (p.read_bytes() if p.is_file() else None)
        )
    except OSError:
        found = None
    if found is not None and _sha(found) == _sha(data):
        return ToolResult(ok=True, content=f"{done} {p.name}", effect="confirmed")
    return ToolResult(ok=False, content="the file does not match", effect="unknown")


class _OffLoop(BaseTool):
    """A file tool's disk work runs on a worker thread, never on the event loop (#3).

    Blocking the loop would hold every other conversation still, and the router's
    timeout could not bite: it waits on the loop. On a thread the timeout stops the
    waiting, and the outcome is recorded as unknown, which it honestly is."""

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        return await asyncio.to_thread(self._execute, args, ctx)

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError


class FileList(_OffLoop):
    definition = ToolDefinition(
        name="file_list",
        description="List the entries of a folder the user granted.",
        input_schema=_PATH_ONLY,
        risk=Risk.READ,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
    )

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if _path(args).is_dir():
            return Precondition.passed()
        return Precondition.failed("not a folder")

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        try:
            found = beneath.entries(_path(args), ctx.granted_roots)
        except OSError as e:
            return _unsafe(e)
        entries = sorted(found, key=lambda e: e[0].lower())
        lines = [name + ("/" if is_dir else "") for name, is_dir in entries[:MAX_LIST_ENTRIES]]
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"… and {len(entries) - MAX_LIST_ENTRIES} more")
        return ToolResult(ok=True, content="\n".join(lines), effect="none")


class FileRead(_OffLoop):
    definition = ToolDefinition(
        name="file_read",
        description="Read a text file in a folder the user granted.",
        input_schema=_PATH_ONLY,
        risk=Risk.READ,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
        # The model may write what it reads back to the file, so it must see it exactly.
        lossless_output=True,
    )

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        p = _path(args)
        if not p.is_file():
            return Precondition.failed("not a file")
        if p.stat().st_size > MAX_READ_BYTES:
            return Precondition.failed(f"larger than {human_size(MAX_READ_BYTES)}")
        return Precondition.passed()

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        # The precheck saw the size; the file can grow before we read. Never read more
        # than the limit plus one byte, and refuse if that byte is there.
        try:
            data = beneath.read_bytes(
                _target(self, args, ctx), ctx.granted_roots, MAX_READ_BYTES + 1
            )
        except OSError as e:
            return _unsafe(e)
        if len(data) > MAX_READ_BYTES:
            return ToolResult(
                ok=False,
                content=f"the file grew past {human_size(MAX_READ_BYTES)} while being read",
                effect="none",
                error_kind="too_large",
            )
        # Strict: decoding with replacement turned unknown bytes into U+FFFD, and a
        # write-back would then have saved them. Refuse rather than change the file.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                ok=False,
                content="not UTF-8 text, so it cannot be read without changing it",
                effect="none",
                error_kind="not_utf8",
            )
        return ToolResult(ok=True, content=text, effect="none")


class FileCreate(_OffLoop):
    definition = ToolDefinition(
        name="file_create",
        description=(
            "Create a new file; never replaces an existing one. " + _NAME_A_FILE + ASKS_BY_CARD
        ),
        input_schema=_PATH_AND_CONTENT,
        risk=Risk.IRREVERSIBLE,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
        recoverable=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        size = human_size(len(_bytes(args)))
        return ActionSummary(text=f"Create {_where(_path(args))} ({size}): {_preview(_text(args))}")

    def target(self, args: JSON) -> str:
        return str(_path(args))

    def fingerprint(self, args: JSON) -> str | None:
        return "present" if os.path.lexists(_path(args)) else "absent"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        p = _path(args)
        if p.is_dir():
            return Precondition.failed(_folder(p))
        if p.exists():
            return Precondition.failed("a file with that name already exists")
        if not p.parent.is_dir():
            return Precondition.failed("the folder does not exist")
        return Precondition.passed()

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        data = _bytes(args)
        try:
            p = _target(self, args, ctx)
            # Exclusive: never overwrites, even in a race.
            fd = beneath.open_file(
                p,
                ctx.granted_roots,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                read_only=ctx.read_only_roots,
            )
        except FileExistsError:
            return ToolResult(
                ok=False, content="a file with that name already exists", effect="none",
                error_kind="FileExistsError",
            )  # fmt: skip
        except OSError as e:
            return _unsafe(e)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return _matches(p, data, "created", ctx)

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        p, data = _path(args), _bytes(args)
        if not p.exists():
            return ToolResult(ok=False, content="the file was not created", effect="none")
        return _matches(p, data, "created")


class FileOverwrite(_OffLoop):
    definition = ToolDefinition(
        name="file_overwrite",
        description=(
            "Replace the whole contents of an existing file. "
            + _NAME_A_FILE
            + ASKS_BY_CARD
            + " Only a tap approves it."
        ),
        input_schema=_PATH_AND_CONTENT,
        risk=Risk.IRREVERSIBLE,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
        recoverable=False,
    )

    def describe(self, args: JSON) -> ActionSummary:
        p = _path(args)
        old = p.stat().st_size if p.is_file() else 0
        new = len(_bytes(args))
        head = f"Replace {_where(p)} ({human_size(old)} → {human_size(new)})"
        current = _read_text(p)
        if current is None:
            return ActionSummary(text=head)
        return ActionSummary(text=f"{head}, {_line_changes(current, _text(args))}")

    def target(self, args: JSON) -> str:
        return str(_path(args))

    def fingerprint(self, args: JSON) -> str | None:
        return _content_fingerprint(args)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        p = _path(args)
        if p.is_file():
            return Precondition.passed()
        return Precondition.failed(_folder(p) if p.is_dir() else "no such file")

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        data = _bytes(args)
        try:
            p = _target(self, args, ctx)
            beneath.replace(
                p, ctx.granted_roots, data, must_exist=True, read_only=ctx.read_only_roots
            )
        except OSError as e:
            return _unsafe(e)
        return _matches(p, data, "replaced", ctx)

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        return _matches(_path(args), _bytes(args), "replaced")


class FileAppend(_OffLoop):
    definition = ToolDefinition(
        name="file_append",
        description=("Add text to the end of an existing file. " + ASKS_BY_CARD),
        input_schema=_PATH_AND_CONTENT,
        risk=Risk.IRREVERSIBLE,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
        recoverable=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        size = human_size(len(_bytes(args)))
        return ActionSummary(
            text=f"Append {size} to {_where(_path(args))}: {_preview(_text(args))}"
        )

    def target(self, args: JSON) -> str:
        return str(_path(args))

    def fingerprint(self, args: JSON) -> str | None:
        return _content_fingerprint(args)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        p = _path(args)
        if p.is_file():
            return Precondition.passed()
        return Precondition.failed(_folder(p) if p.is_dir() else "no such file")

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        data = _bytes(args)
        # Walked to from the granted folder with no link followed on the way or at the
        # end, and refused if the file has a second name elsewhere: an append changes
        # the file itself, so a hard link would carry the write out of the folder.
        try:
            raw = _target(self, args, ctx)
            fd = beneath.open_file(
                raw, ctx.granted_roots, os.O_RDWR | os.O_APPEND, read_only=ctx.read_only_roots
            )
        except OSError as e:
            return _unsafe(e)
        with os.fdopen(fd, "ab+") as f:
            f.write(data)
            f.flush()
            # Read back through the same descriptor: evidence about the file we wrote.
            f.seek(-len(data), os.SEEK_END)
            tail = f.read(len(data)) if data else b""
        if tail == data:
            return ToolResult(ok=True, content=f"appended to {raw.name}", effect="confirmed")
        return ToolResult(ok=False, content="the end of the file does not match", effect="unknown")


class FileEdit(_OffLoop):
    """Replace one exact piece of a file. Whole-file rewrites become the exception.

    `old` must appear exactly once, so the edit can only land where the user was shown."""

    definition = ToolDefinition(
        name="file_edit",
        description=(
            "Replace one exact piece of text in an existing file. `old` must appear exactly "
            "once; include surrounding text to make it unique. " + ASKS_BY_CARD + " Only a tap "
            "approves it."
        ),
        input_schema=_PATH_OLD_NEW,
        risk=Risk.IRREVERSIBLE,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
        recoverable=False,
    )

    def describe(self, args: JSON) -> ActionSummary:
        p, old, new = _path(args), _text(args, "old"), _text(args, "new")
        change = f"{_preview(old, _EDIT_PREVIEW_CHARS)} → {_preview(new, _EDIT_PREVIEW_CHARS)}"
        current = _read_text(p)
        at = current.find(old) if current is not None else -1
        if at < 0 or current is None:
            return ActionSummary(text=f"Edit {_where(p)}: {change}")
        line = current.count("\n", 0, at) + 1
        return ActionSummary(text=f"Edit {_where(p)} at line {line}: {change}")

    def target(self, args: JSON) -> str:
        return str(_path(args))

    def fingerprint(self, args: JSON) -> str | None:
        return _content_fingerprint(args)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        p = _path(args)
        if not p.is_file():
            return Precondition.failed(_folder(p) if p.is_dir() else "no such file")
        if p.stat().st_size > MAX_READ_BYTES:
            return Precondition.failed(f"larger than {human_size(MAX_READ_BYTES)}")
        text = _read_text(p)
        if text is None:
            return Precondition.failed("not UTF-8 text")
        n = text.count(_text(args, "old"))
        if n == 0:
            return Precondition.failed("the text to replace was not found")
        if n > 1:
            return Precondition.failed(
                f"the text to replace appears {n} times; include more of the text around it"
            )
        return Precondition.passed()

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        old, new = _text(args, "old"), _text(args, "new")
        try:
            # Read from the same file it will write: walked to from the granted folder.
            p = _target(self, args, ctx)
            raw = beneath.read_bytes(p, ctx.granted_roots, MAX_READ_BYTES + 1)
        except OSError as e:
            return _unsafe(e)
        try:
            text: str | None = raw.decode("utf-8") if len(raw) <= MAX_READ_BYTES else None
        except UnicodeDecodeError:
            text = None
        if text is None or text.count(old) != 1:
            return ToolResult(
                ok=False,
                content="the text to replace is no longer there exactly once",
                effect="none",
            )
        data = text.replace(old, new, 1).encode("utf-8")
        try:
            beneath.replace(
                p, ctx.granted_roots, data, must_exist=True, read_only=ctx.read_only_roots
            )
        except OSError as e:
            return _unsafe(e)
        return _matches(p, data, "edited", ctx)

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        text = _read_text(_path(args))
        old, new = _text(args, "old"), _text(args, "new")
        if text is not None and text.count(old) == 1 and new not in text:
            return ToolResult(ok=False, content="the edit was not applied", effect="none")
        return None  # cannot tell from the file alone: unknown, never a guessed success
