"""One authenticated conversation over the realtime protocol (PRD §8.3).

A session owns a conversation and the turn counter the confirmation gate
depends on. Only a committed user input advances that counter: a typed
message, or a tap on a confirmation card (VOI-15a). Conversation text is
never logged here; logs carry ids, types and statuses only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from trillion.api.runtime import OWNER, Runtime
from trillion.brain.loop import LoopEvent, ReplyDelta, ToolEnded, ToolStarted, run_turn
from trillion.brain.types import ContextBlock, Msg, TextBlock
from trillion.persona.prompt import PendingNote, system_blocks, volatile_context
from trillion.spine.confirm import Status
from trillion.spine.contract import Risk, ToolContext
from trillion.storage.sqlite.conversation_store import USER_INPUTS, Kind

logger = logging.getLogger(__name__)

PROTOCOL = 1
MAX_TEXT = 32_000
SURFACE = "web"
Send = Callable[[dict[str, Any]], Awaitable[None]]


class Session:
    def __init__(self, rt: Runtime, send: Send, conversation_id: str) -> None:
        self._rt = rt
        self._raw_send = send
        self.id = conversation_id
        self._history: list[Msg] = []
        self._turn = 0
        self._task: asyncio.Task[None] | None = None
        # Notes about taps that arrived mid-turn, delivered to the model afterwards.
        self._queued: list[str] = []
        self._closed = False
        self._orb = ""

    # ── Lifecycle ────────────────────────────────────────────────────────

    @classmethod
    async def start(cls, rt: Runtime, send: Send, resume: str | None) -> Session:
        resumed = bool(resume) and await rt.conversations.exists(str(resume), OWNER)
        session = cls(rt, send, str(resume) if resumed else uuid.uuid4().hex)
        shown: list[dict[str, str]] = []
        if resumed:
            for kind, msg in await rt.conversations.history(session.id, OWNER):
                session._history.append(msg)
                session._turn += kind in USER_INPUTS
                text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
                if kind in ("user", "assistant") and text:
                    shown.append({"role": msg.role, "text": text})
        else:
            await rt.conversations.create(session.id, OWNER, rt.clock())
        await session._send(
            "session",
            id=session.id,
            protocol=PROTOCOL,
            agent_name=rt.identity.agent_name,
            model=session._model(),
            granted_roots=[str(r) for r in rt.granted_roots],
        )
        rt.sessions.add(session)
        await session._send("history", messages=shown)
        for a in await session._open_actions():
            await session._send_card(a.id)
        await session._state("idle")
        return session

    def _model(self) -> dict[str, str] | None:
        p = self._rt.provider
        return {"provider": p.name, "model": p.model} if p is not None else None

    async def model_changed(self) -> None:
        """Settings changed. The next turn uses the new model; this one finishes as it was."""
        await self._send("model", model=self._model())

    async def close(self) -> None:
        self._rt.sessions.discard(self)
        self._closed = True
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ── Incoming messages ────────────────────────────────────────────────

    async def handle(self, msg: Mapping[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "turn.text":
            await self._on_text(msg.get("text"))
        elif kind == "interrupt":
            if self._task is not None:
                self._task.cancel()
        elif kind in ("confirm", "cancel"):
            await self._on_tap(msg.get("pending_id"), kind)
        elif kind == "confirm.shown":
            pid = msg.get("pending_id")
            if isinstance(pid, str) and pid:
                await self._rt.router.mark_delivered(pid, OWNER, SURFACE)
        else:
            await self._error("unknown_type")

    async def _on_text(self, text: object) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            await self._error("bad_message")
        elif self._rt.provider is None:
            await self._error("no_model")
        elif self._task is not None:
            await self._error("busy")
        else:
            self._turn += 1
            await self._save_input("user", text)
            self._begin()

    async def _on_tap(self, pending_id: object, decision: Literal["confirm", "cancel"]) -> None:
        if not isinstance(pending_id, str) or not pending_id:
            await self._error("bad_message")
            return
        # A tap is the user committing an input, so it is its own turn. That is what
        # lets a card be approved straight away while the model still cannot confirm
        # anything in the turn it proposed it (SPN-2e).
        self._turn += 1
        response = await self._rt.router.resolve(
            pending_id, decision, self._ctx(f"tap-{uuid.uuid4().hex}", via="tap")
        )
        await self._send(
            "confirm.resolved",
            pending_id=pending_id,
            status=response.status,
            reason=response.reason.value if response.reason is not None else None,
            detail=response.detail or None,
        )
        action = "Approve" if decision == "confirm" else "Decline"
        note = f"[host] The user tapped {action} on the card for {pending_id}. Result: "
        self._queued.append(note + response.to_model())
        if self._task is None:
            await self._flush()

    # ── A turn ───────────────────────────────────────────────────────────

    def _begin(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _flush(self) -> None:
        """Tell the model what the taps did, in one message, and let it answer."""
        if not self._queued or self._closed:
            return
        note, self._queued = "\n".join(self._queued), []
        await self._save_input("tap", note)
        if self._rt.provider is not None:
            self._begin()

    async def _run(self) -> None:
        rt = self._rt
        provider = rt.provider
        if provider is None:  # pragma: no cover - callers check before starting a turn
            return
        speaking = False

        async def emit(ev: LoopEvent) -> None:
            nonlocal speaking
            if isinstance(ev, ReplyDelta):
                if not speaking:
                    speaking = True
                    await self._state("speaking")
                await self._send("reply.delta", text=ev.text)
            elif isinstance(ev, ToolStarted):
                speaking = False
                await self._state("processing")
                await self._send("tool.start", call_id=ev.call_id, name=ev.name, risk=ev.risk)
            elif isinstance(ev, ToolEnded):
                r = ev.response
                await self._send(
                    "tool.end",
                    call_id=ev.call_id,
                    ok=r.status in ("succeeded", "confirmation_required"),
                    summary=r.reason.value if r.reason is not None else r.status,
                )
                if r.status == "confirmation_required" and r.pending_id:
                    await self._send_card(r.pending_id)

        final = "idle"
        try:
            await self._state("processing")
            done = await run_turn(
                provider=provider,
                router=rt.router,
                registry=rt.registry,
                system=system_blocks(rt.identity, rt.soul, rt.granted_roots),
                history=self._history,
                ctx=self._ctx,
                emit=emit,
                save=self._save_from_loop,
            )
            await rt.conversations.record_usage(
                owner_id=OWNER,
                conversation_id=self.id,
                provider=provider.name,
                model=provider.model,
                usage=done.usage,
                cost_usd=done.cost_usd,
                now_ms=rt.clock(),
            )
            if done.stop == "provider_error":
                final = "error"
                await self._error("provider", kind=done.error_kind)
            elif done.stop != "end":
                await self._send("notice", code="stopped", reason=done.stop)
            u = done.usage
            await self._send(
                "turn.complete",
                stop=done.stop,
                cost_usd=done.cost_usd,
                usage={
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read_tokens": u.cache_read_tokens,
                    "cache_write_tokens": u.cache_write_tokens,
                },
            )
        except asyncio.CancelledError:
            await self._send("interrupted")
        except Exception as e:
            # The class name only: an exception message can carry conversation text.
            logger.error("turn failed in session %s: %s", self.id, type(e).__name__)
            final = "error"
            await self._error("internal")
        finally:
            self._task = None
            await self._state(final)
            await self._flush()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _ctx(
        self, call_id: str, via: Literal["voice", "text", "tap", "channel"] = "text"
    ) -> ToolContext:
        return ToolContext(
            owner_id=OWNER,
            session_id=self.id,
            turn=self._turn,
            call_id=call_id,
            via=via,
            granted_roots=self._rt.granted_roots,
        )

    async def _save(self, kind: Kind, msg: Msg) -> None:
        self._history.append(msg)
        await self._rt.conversations.append(self.id, OWNER, kind, msg, self._rt.clock())

    async def _save_input(self, kind: Kind, text: str) -> None:
        """A user input, with the context it was said in. Both are saved together and
        replayed unchanged on every later turn: history is only ever appended to."""
        pending = [
            PendingNote(pending_id=a.id, summary=a.summary) for a in await self._open_actions()
        ]
        context = volatile_context(datetime.now().astimezone(), pending)
        blocks = (ContextBlock(text=context), TextBlock(text=text))
        await self._save(kind, Msg(role="user", blocks=blocks))

    async def _save_from_loop(self, msg: Msg) -> None:
        await self._save("assistant" if msg.role == "assistant" else "tool_results", msg)

    async def _open_actions(self) -> list[Any]:
        now = self._rt.clock()
        return [
            a
            for a in await self._rt.spine_store.list_by_status(Status.PROPOSED)
            if a.owner_id == OWNER and a.session_id == self.id and a.expires_at > now
        ]

    async def _send_card(self, pending_id: str) -> None:
        a = await self._rt.spine_store.get_pending(pending_id)
        if a is None or a.owner_id != OWNER:
            return
        await self._send(
            "confirm.request",
            pending_id=a.id,
            tool=a.tool,
            summary=a.summary,
            expires_at=a.expires_at,
            # Spend and anything unrecoverable can only be approved here, by a tap (SPN-14).
            tap_only=a.risk is Risk.SPEND or not a.recoverable,
        )

    async def _state(self, orb: str) -> None:
        if orb != self._orb:
            self._orb = orb
            await self._send("state", orb=orb)

    async def _error(self, code: str, **extra: Any) -> None:
        await self._send("error", code=code, **extra)

    async def _send(self, kind: str, /, **body: Any) -> None:
        """Never raises: a closed socket must not break a turn that is still recording."""
        if self._closed:
            return
        try:
            await self._raw_send({"type": kind, **body})
        except Exception as e:
            logger.debug("send of %s failed: %s", kind, type(e).__name__)
