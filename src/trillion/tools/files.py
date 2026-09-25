"""File tools in folders the user granted. Every write is `irreversible` and asks first (D4)."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.neutralize import neutralize

MAX_READ_BYTES = 1_000_000
MAX_LIST_ENTRIES = 500

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


def human_size(n: int) -> str:
    if n < 1000:
        return f"{n} bytes"
    if n < 1_000_000:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _path(args: JSON) -> Path:
    return Path(str(args["path"])).expanduser().resolve(strict=False)


def _bytes(args: JSON) -> bytes:
    return str(args["content"]).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _content_fingerprint(args: JSON) -> str:
    p = _path(args)
    if not p.is_file():
        return "missing"
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _where(p: Path) -> str:
    return f"{p.name} in {p.parent.name or p.parent}"


def _matches(p: Path, data: bytes, done: str) -> ToolResult:
    """Read the file back: success needs evidence (SPN-17a)."""
    if p.is_file() and _sha(p.read_bytes()) == _sha(data):
        return ToolResult(ok=True, content=f"{done} {p.name}", effect="confirmed")
    return ToolResult(ok=False, content="the file does not match", effect="unknown")


class FileList(BaseTool):
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

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        entries = sorted(_path(args).iterdir(), key=lambda e: e.name.lower())
        lines = [e.name + ("/" if e.is_dir() else "") for e in entries[:MAX_LIST_ENTRIES]]
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"… and {len(entries) - MAX_LIST_ENTRIES} more")
        return ToolResult(ok=True, content="\n".join(lines), effect="none")


class FileRead(BaseTool):
    definition = ToolDefinition(
        name="file_read",
        description="Read a text file in a folder the user granted.",
        input_schema=_PATH_ONLY,
        risk=Risk.READ,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
    )

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        p = _path(args)
        if not p.is_file():
            return Precondition.failed("not a file")
        if p.stat().st_size > MAX_READ_BYTES:
            return Precondition.failed(f"larger than {human_size(MAX_READ_BYTES)}")
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        text = _path(args).read_text(encoding="utf-8", errors="replace")
        clean, _ = neutralize(text)
        return ToolResult(ok=True, content=clean, effect="none")


class FileCreate(BaseTool):
    definition = ToolDefinition(
        name="file_create",
        description="Create a new file. Asks the user first; never replaces an existing file.",
        input_schema=_PATH_AND_CONTENT,
        risk=Risk.IRREVERSIBLE,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
        recoverable=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text=f"Create {_where(_path(args))} ({human_size(len(_bytes(args)))})")

    def target(self, args: JSON) -> str:
        return str(_path(args))

    def fingerprint(self, args: JSON) -> str | None:
        return "present" if os.path.lexists(_path(args)) else "absent"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        p = _path(args)
        if p.exists():
            return Precondition.failed("a file with that name already exists")
        if not p.parent.is_dir():
            return Precondition.failed("the folder does not exist")
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        p, data = _path(args), _bytes(args)
        with p.open("xb") as f:  # exclusive: never overwrites, even in a race
            f.write(data)
        return _matches(p, data, "created")

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        p, data = _path(args), _bytes(args)
        if not p.exists():
            return ToolResult(ok=False, content="the file was not created", effect="none")
        return _matches(p, data, "created")


class FileOverwrite(BaseTool):
    definition = ToolDefinition(
        name="file_overwrite",
        description="Replace the whole contents of an existing file. Needs a tap to confirm.",
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
        return ActionSummary(text=f"Replace {_where(p)} ({human_size(old)} → {human_size(new)})")

    def target(self, args: JSON) -> str:
        return str(_path(args))

    def fingerprint(self, args: JSON) -> str | None:
        return _content_fingerprint(args)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if _path(args).is_file():
            return Precondition.passed()
        return Precondition.failed("no such file")

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        p, data = _path(args), _bytes(args)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            shutil.copymode(p, tmp)  # mkstemp creates 0600; keep the file's own mode
            os.replace(tmp, p)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return _matches(p, data, "replaced")

    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        return _matches(_path(args), _bytes(args), "replaced")


class FileAppend(BaseTool):
    definition = ToolDefinition(
        name="file_append",
        description="Add text to the end of an existing file. Asks the user first.",
        input_schema=_PATH_AND_CONTENT,
        risk=Risk.IRREVERSIBLE,
        category="files",
        timeout_s=10.0,
        path_args=("path",),
        recoverable=True,
    )

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(
            text=f"Append {human_size(len(_bytes(args)))} to {_where(_path(args))}"
        )

    def target(self, args: JSON) -> str:
        return str(_path(args))

    def fingerprint(self, args: JSON) -> str | None:
        return _content_fingerprint(args)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if _path(args).is_file():
            return Precondition.passed()
        return Precondition.failed("no such file")

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        data = _bytes(args)
        # The floor checked where the path resolves. Open the path as given and refuse a
        # final symlink, so a link swapped in after that check is never followed.
        raw = Path(str(args["path"])).expanduser()
        try:
            fd = os.open(raw, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW)
        except OSError as e:
            return ToolResult(
                ok=False,
                content="the file could not be opened safely for appending",
                effect="none",
                error_kind=type(e).__name__,
            )
        with os.fdopen(fd, "ab+") as f:
            f.write(data)
            f.flush()
            # Read back through the same descriptor: evidence about the file we wrote.
            size = os.fstat(fd).st_size
            tail = os.pread(fd, len(data), size - len(data)) if data else b""
        if tail == data:
            return ToolResult(ok=True, content=f"appended to {raw.name}", effect="confirmed")
        return ToolResult(ok=False, content="the end of the file does not match", effect="unknown")
