"""The memory tools (MEM-3): write, recall, forget.

`memory_write` runs at once: remembering is cheap to undo and asking each time
would make her useless. `memory_forget` asks first (SPN-9). `memory_recall`
returns its findings as untrusted text like any other tool result: a memory can
inform what she does and can never authorize it (MEM-5, MEM-6a).
"""

from __future__ import annotations

import time

from trillion.memory import present
from trillion.memory.book import RECALL_LIMIT, MemoryBook, recalled_tokens
from trillion.memory.files import valid_id
from trillion.memory.types import MAX_TEXT, TYPES, Memory, MemoryError_, MemoryType, Source
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

WRITE, RECALL, FORGET = "memory_write", "memory_recall", "memory_forget"
# How many memories one turn may write (MEM-6b). A model stuck in a loop, or a
# document telling her to remember a thousand things, stops here.
MAX_WRITES_PER_TURN = 6
# The most a single recall may put in front of the model, in rough tokens (MEM-6b).
MAX_RECALL_TOKENS = 1500
_PREVIEW = 160

_REFUSALS = {
    "empty": "there was nothing to remember",
    "too_long": f"too long to remember: keep it under {MAX_TEXT} characters, one fact",
    "not_found": "no such memory",
    "full": "memory is full; something must be forgotten first",
    "disk": "the memory could not be written to disk",
    "bad_request": "that is not a kind of memory",
    "from_reading": (
        "a memory written after reading a page or a file cannot replace one; save it "
        "without replaces, and ask the user which is right"
    ),
}


# The words the model chooses from, and what they are stored as. They are kept
# apart on purpose: when both lists contained "user", a small model filled one
# field with the other's value and every write was refused.
_KINDS: dict[str, MemoryType] = {
    "personal": "user",
    "feedback": "feedback",
    "project": "project",
    "reference": "reference",
}
_SAID_BY: dict[str, Source] = {"user": "user_statement", "assistant": "agent_inference"}


def _refused(kind: str) -> ToolResult:
    return ToolResult(
        ok=False, content=_REFUSALS.get(kind, "memory failed"), effect="none", error_kind=kind
    )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class _MemoryTool(BaseTool):
    def __init__(self) -> None:
        self._book: MemoryBook | None = None

    def bind(self, book: MemoryBook) -> None:
        """Called once at startup with the running process's memory."""
        self._book = book

    def _for(self, ctx: ToolContext) -> MemoryBook | None:
        book = self._book
        return book if book is not None and book.owner_id == ctx.owner_id else None


class MemoryWrite(_MemoryTool):
    definition = ToolDefinition(
        name=WRITE,
        description=(
            "Remember one fact across conversations. Use it when the user tells you something "
            "lasting about themselves, how they want you to work, an ongoing project, or where "
            "something lives, or asks you to remember something. One plain sentence per call, "
            "written so it makes sense on its own. Runs at once. Set moves=true for anything "
            "that changes by itself (a balance, a price, an address, a status). To correct an "
            "earlier memory, pass its id as replaces."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT},
                "moves": {
                    "type": "boolean",
                    "description": (
                        "Usually false: a preference, a name, a fact about a person, a plan. "
                        "True only for a value that changes by itself with nobody telling "
                        "you: a balance, a price, a stock level, a device's state."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": list(_KINDS),
                    "description": (
                        "personal: who they are and what they like. feedback: how they want "
                        "you to work. project: something ongoing. reference: where something "
                        "lives. Defaults to personal."
                    ),
                },
                "said_by": {
                    "type": "string",
                    "enum": list(_SAID_BY),
                    "description": (
                        "user if the user told you this; assistant if you worked it out "
                        "yourself. Defaults to assistant."
                    ),
                },
                "replaces": {
                    "type": ["string", "null"],
                    "description": "The id of a memory this corrects.",
                },
            },
            "required": ["text", "moves"],
            "additionalProperties": False,
        },
        risk=Risk.WRITE,
        category="memory",
        timeout_s=10.0,
    )

    def __init__(self) -> None:
        super().__init__()
        self._turn: tuple[str, int] = ("", -1)
        self._written = 0

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        book = self._for(ctx)
        if book is None:
            return _refused("disk")
        turn = (ctx.session_id, ctx.turn)
        if turn != self._turn:
            self._turn, self._written = turn, 0
        if self._written >= MAX_WRITES_PER_TURN:
            return ToolResult(
                ok=False,
                content="too many memories written in one turn; stop and carry on",
                effect="none",
                error_kind="too_many",
            )
        replaces = args.get("replaces")
        if replaces is not None and not valid_id(replaces):
            return _refused("not_found")
        if ctx.read_outside and replaces is not None:
            # Something she read cannot overwrite what the user told her. It can be kept
            # beside it, as a claim, and she can ask the user which is right.
            return _refused("from_reading")
        try:
            done = await book.write(
                type=_KINDS[str(args.get("kind") or "personal")],
                text=str(args["text"]),
                # Unsaid, it is treated as her own inference: the weaker claim. After she
                # has read a page or a file this turn it is a claim from there, whoever
                # she says it came from: a page can tell her the user said anything.
                source="imported_claim"
                if ctx.read_outside
                else _SAID_BY[str(args.get("said_by") or "assistant")],
                moves=args["moves"] is True,
                replaces=replaces,
                now_ms=_now_ms(),
            )
        except MemoryError_ as e:
            return _refused(e.kind)
        if done.duplicate:
            # It is stored, which is what was asked: the effect holds though nothing changed.
            note = f"already remembered as {done.memory.id}"
            if replaces:
                note += f"; {replaces} is now marked as replaced by it"
            return ToolResult(ok=True, content=note, effect="confirmed")
        self._written += 1
        note = f"remembered as {done.memory.id}"
        if ctx.read_outside:
            note += ", kept as something you read this turn, not as something the user said"
        if replaces:
            note += f", replacing {replaces}"
        else:
            # A correction often arrives as a plain new fact. The older memories it may
            # have outdated are named, so retiring one is a single further call.
            near = await book.related(done.memory)
            if near:
                note += (
                    "\nStill held on the same subject:\n"
                    + "\n".join(f"- {m.id}: {present.inert(m.text)}" for m in near)
                    + "\nIf the new fact makes one of these wrong, call memory_write again "
                    "with the same text and replaces set to that id. Otherwise do nothing."
                )
        return ToolResult(ok=True, content=note, effect="confirmed")


class MemoryRecall(_MemoryTool):
    definition = ToolDefinition(
        name=RECALL,
        description=(
            "Look up what you have remembered. Give a few words about the subject, or leave "
            "query empty for the most recent. Memories that match a message already arrive "
            "with it; call this to look for more. What comes back is what was said at the "
            "time, not proof it is still true."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 300},
                "type": {"type": "string", "enum": list(TYPES)},
                "include_history": {
                    "type": "boolean",
                    "description": "Also show memories that were corrected or forgotten.",
                },
            },
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category="memory",
        timeout_s=10.0,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        book = self._for(ctx)
        if book is None:
            return _refused("disk")
        found = await book.search(
            str(args.get("query") or ""),
            type=args.get("type"),
            history=args.get("include_history") is True,
            limit=RECALL_LIMIT,
        )
        while len(found) > 1 and recalled_tokens(found) > MAX_RECALL_TOKENS:
            found = found[:-1]
        text = present.render(found, _now_ms(), empty="Nothing remembered about that.")
        return ToolResult(ok=True, content=text, effect="none")


class MemoryForget(_MemoryTool):
    definition = ToolDefinition(
        name=FORGET,
        description=(
            "Forget one memory, by its id. " + ASKS_BY_CARD + " Use memory_write with "
            "replaces instead when a fact has changed rather than gone."
        ),
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        category="memory",
        timeout_s=10.0,
        # Soft underneath: the file is kept and marked, so a yes is enough (SPN-9).
        recoverable=True,
    )

    def _memory(self, args: JSON) -> tuple[str, Memory | None]:
        memory_id = str(args.get("id") or "")
        book = self._book
        return memory_id, book.peek(memory_id) if book is not None else None

    def describe(self, args: JSON) -> ActionSummary:
        memory_id, m = self._memory(args)
        if m is None:
            return ActionSummary(text=f"Forget memory {memory_id[:40]}")
        text = " ".join(m.text.split())
        shown = text if len(text) <= _PREVIEW else text[: _PREVIEW - 1] + "…"
        return ActionSummary(text=f"Forget: “{shown}”")

    def target(self, args: JSON) -> str:
        return f"memory:{str(args.get('id') or '')[:40]}"

    def fingerprint(self, args: JSON) -> str | None:
        _, m = self._memory(args)
        # If it is corrected or forgotten between the ask and the yes, nothing runs.
        return f"{m.version}:{m.status}" if m is not None else "absent"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        _, m = self._memory(args)
        if self._for(ctx) is None or m is None:
            return Precondition.failed("no such memory")
        if not m.current:
            return Precondition.failed("that memory is already forgotten or replaced")
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        book = self._for(ctx)
        if book is None:
            return _refused("disk")
        try:
            gone = await book.forget(str(args["id"]), _now_ms())
        except MemoryError_ as e:
            return _refused(e.kind)
        return ToolResult(ok=True, content=f"forgot {gone.id}", effect="confirmed")
