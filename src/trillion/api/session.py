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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from trillion import __version__
from trillion.api import listening
from trillion.api.listening import FOLLOW_UP_S, Listener
from trillion.api.runtime import OUT_OF_TURN, OWNER, Runtime
from trillion.api.speech import SendBytes, Speaker, describe
from trillion.api.trace import Trace, Via, ahead_ms
from trillion.brain import compact
from trillion.brain.loop import (
    CallTimed,
    LoopEvent,
    ReplyDelta,
    ToolEnded,
    ToolStarted,
    run_turn,
)
from trillion.brain.types import ContextBlock, Msg, ProviderError, TextBlock
from trillion.delivery.door import Delivery, Facts, NotFree, free
from trillion.memory import present
from trillion.persona.prompt import (
    PendingNote,
    compaction,
    degraded,
    greeting,
    volatile_context,
)
from trillion.spine.confirm import Status
from trillion.spine.contract import Risk, ToolContext
from trillion.spine.untrusted import is_outside, wrap
from trillion.storage.sqlite.conversation_store import USER_INPUTS, Kind
from trillion.voice.endpoint import NOTHING_AFTER_S, Verdict
from trillion.voice.types import VoiceError

logger = logging.getLogger(__name__)

PROTOCOL = 1
MAX_TEXT = 32_000
# How long a barge-in waits for her reply to stop before listening. An action she was
# carrying out runs to its end first; this bounds the wait, not the action.
BARGE_WAIT_S = 3.0
# A microphone that sends nothing at all for this long is treated as not working.
NO_AUDIO_S = 3.0
# One frame of audio from the page is small; anything much larger is not audio.
MAX_AUDIO_FRAME = 64_000
SURFACE = "web"
__all__ = ["OUT_OF_TURN", "PROTOCOL", "Session"]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class Session:
    def __init__(
        self, rt: Runtime, send: Send, conversation_id: str, send_bytes: SendBytes | None = None
    ) -> None:
        self._rt = rt
        self._raw_send = send
        self._raw_send_bytes = send_bytes
        # The page can silence her; then nothing is synthesized, so nothing is paid for.
        self._muted = False
        self.id = conversation_id
        # The messages the model is sent, after the latest summary, each with its kind
        # and its id in the store. The whole conversation stays in the store.
        self._history: list[Msg] = []
        self._kinds: list[Kind] = []
        self._ids: list[int] = []
        self._summary: str | None = None
        # The turns in which she read something from outside: a page, a file, or a
        # timed ask's answer, which may quote an email (MEM-5).
        self._outside: set[int] = set()
        self._turn = 0
        self._task: asyncio.Task[None] | None = None
        # Notes about taps that arrived mid-turn, delivered to the model afterwards.
        self._queued: list[str] = []
        self._closed = False
        self._greeted = False
        self._orb = ""
        # The orb's state between turns: idle, or error after a failure.
        self._rest = "idle"
        # Memories already brought along in this conversation: each rides once, with
        # the message that first called for it, and is never repeated.
        self._brought: set[str] = set()
        self._listener: Listener | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._last_audio = 0.0
        # How the current turn was said. A spoken yes is recorded as spoken (SPN-14).
        self._via: Literal["voice", "text"] = "text"
        # How long turns take (ONB-11): the next turn's start, the running turn's
        # trace, and the last one saved, which the page's "audible" may still reach.
        self._arrived: Trace | None = None
        self._trace: Trace | None = None
        self._last_trace: Trace | None = None
        self._timed = 0
        self._mic_setup: dict[str, Any] = {}
        self._warming: asyncio.Future[Any] | None = None
        # What the door judges "free" by (VOI-17): the last turn sent to be heard and
        # when its audio finished sending, and the last one the page said it played.
        self._spoken: tuple[int, int] | None = None
        self._played: int | None = None
        # She is saying a sentence of her own outside a turn: a leg is down (VOI-14).
        self._saying = False
        # Speaking notices, when that is the session's task: user input stops it.
        self._notice_task: asyncio.Task[None] | None = None
        # User input being taken in: nothing is delivered over it (VOI-17).
        self._taking = 0
        # Until the page has its history and cards, a notice would arrive before them.
        self._started = False
        # The last turn whose number speech went out under.
        self._voiced: int | None = None
        # When the user last did something here. The live page is the latest (SUR-9).
        self.active_ms = rt.clock()

    # ── Lifecycle ────────────────────────────────────────────────────────

    @classmethod
    async def start(
        cls, rt: Runtime, send: Send, resume: str | None, send_bytes: SendBytes | None = None
    ) -> Session:
        resumed = bool(resume) and await rt.conversations.exists(str(resume), OWNER)
        if resumed:
            # One conversation, one live session. Two would each keep their own copy of
            # the history and interleave what they save. The older one lets its turn
            # finish recording, and only then is the history read.
            other = rt.by_conversation.get(str(resume))
            if other is not None:
                await other.replaced()
        session = cls(rt, send, str(resume) if resumed else uuid.uuid4().hex, send_bytes)
        shown: list[dict[str, str]] = []
        if resumed:
            # The page and the turn count get every message; the model gets the latest
            # summary and what came after it (BRN-5).
            for kind, msg in await rt.conversations.history(session.id, OWNER):
                session._turn += kind in USER_INPUTS
                text = "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))
                if kind in ("user", "assistant") and text:
                    shown.append({"role": msg.role, "text": text})
            summary, rows = await rt.conversations.context(session.id, OWNER)
            session._summary = summary
            if rt.web is not None:
                # The addresses the user gave earlier in this conversation stay openable.
                for kind, msg in await rt.conversations.history(session.id, OWNER):
                    if kind == "user":
                        for b in msg.blocks:
                            if isinstance(b, TextBlock):
                                rt.web.ledger.said(session.id, b.text)
            for message_id, kind, msg in rows:
                session._ids.append(message_id)
                session._kinds.append(kind)
                session._history.append(msg)
        else:
            await rt.conversations.create(session.id, OWNER, rt.clock())
        await session._send(
            "session",
            id=session.id,
            protocol=PROTOCOL,
            agent_name=rt.identity.agent_name,
            onboarding=rt.onboarding(),
            version=__version__,
            model=session._model(),
            speech=session._speech(),
            listening=session._listening(),
            granted_roots=[str(r) for r in rt.granted_roots],
        )
        rt.sessions.add(session)
        rt.by_conversation[session.id] = session
        await session._send("history", messages=shown)
        for a in await session._open_actions():
            await session._send_card(a.id)
        session._started = True
        await session._state("idle")
        return session

    def _model(self) -> dict[str, str] | None:
        p = self._rt.provider
        return {"provider": p.name, "model": p.model} if p is not None else None

    def _speech(self) -> dict[str, Any] | None:
        rt = self._rt
        s = rt.speech
        return describe(rt.tts, s.kind if s else None, s.voice if s else None)

    def _listening(self) -> dict[str, Any] | None:
        rt = self._rt
        s = rt.listening
        return listening.describe(rt.stt, s.kind if s else None, s.choice if s else None)

    async def settings_changed(self) -> None:
        """Settings changed. The next turn uses them; one in flight finishes as it was."""
        await self._send("model", model=self._model())
        await self._send("speech", speech=self._speech())
        await self._send("listening", listening=self._listening())

    async def identity_changed(self) -> None:
        """The user renamed her or themselves. The page follows; the next turn uses it."""
        await self._send("identity", agent_name=self._rt.identity.agent_name)

    async def replaced(self) -> None:
        """This conversation was opened somewhere else. Say so, then stop for good."""
        await self._send("error", code="replaced")
        await self.close()

    async def close(self) -> None:
        self._rt.sessions.discard(self)
        if self._rt.by_conversation.get(self.id) is self:
            del self._rt.by_conversation[self.id]
        await self._stop_listening()
        self._closed = True
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ── Incoming messages ────────────────────────────────────────────────

    async def handle(self, msg: Mapping[str, Any]) -> None:
        if self._closed:
            return
        kind = msg.get("type")
        if kind == "turn.text":
            await self._on_text(msg.get("text"))
        elif kind == "interrupt":
            # Before anything that awaits: a notice that arrives meanwhile is not stopped.
            task = self._task
            # Stopped: the page drops what it was playing and never reports it played.
            self._spoken = None
            if self._listener is not None:
                # Stopping while she listens throws the words away: nothing is sent.
                await self._stop_listening()
                await self._send("mic.stop", reason="cancelled")
                await self._state(self._rest)
            if task is not None:
                task.cancel()
            else:
                # Nothing to stop, but what was held for the speech just dropped may come.
                await self._offer()
        elif kind == "listen.start":
            await self._on_listen(NOTHING_AFTER_S)
        elif kind == "barge":
            await self._on_barge()
        elif kind == "listen.stop":
            # The user says they are done: what was heard so far is the message.
            if self._listener is not None:
                await self._conclude("end")
        elif kind in ("confirm", "cancel"):
            await self._on_tap(msg.get("pending_id"), kind)
        elif kind == "greet":
            await self._on_greet()
        elif kind == "speech.mute":
            self._muted = msg.get("muted") is True
        elif kind == "mic.opened":
            # What the page's audio setup reports: numbers and a state, nothing heard.
            if msg.get("silent") is True:
                logger.warning("the page's microphone gave exact silence: %s", dict(msg))
            self._mic_setup = {
                k: msg.get(k)
                for k in ("rate", "device_rate", "state", "channels", "device", "track", "silent")
                if isinstance(msg.get(k), int | float | str | bool)
            }
        elif kind == "playback.started":
            await self._audible(msg.get("turn_id"), msg.get("in_ms"))
        elif kind == "playback.ended":
            turn_id = msg.get("turn_id")
            if isinstance(turn_id, int) and not isinstance(turn_id, bool):
                self._played = turn_id
            # She has finished being heard, which is what reopens the microphone (VOI-8,
            # VOI-15b): only after a spoken turn, and never over a turn still running.
            if self._via == "voice" and self._task is None and self._listener is None:
                await self._on_listen(FOLLOW_UP_S, follow_up=True)
            await self._offer()
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
        else:
            async with self._taking_input():
                await self._take_text(text)

    async def _take_text(self, text: str) -> None:
        if self._task is not None:
            await self._error("busy")
        else:
            self._spoken = None
            await self._stop_listening()
            self._via = "text"
            self._turn += 1
            self.active_ms = self._rt.clock()
            self._arrived = self._starting("text")
            await self._save_input("user", text)
            self._begin()

    async def _on_greet(self) -> None:
        """The introduction is over: she says hello in her own words, once (ONB-3).

        The page only asks. What she is told is authored here and carries nothing
        the page sent, and it is refused anywhere but the start of a conversation
        with someone who has introduced themselves."""
        rt = self._rt
        if rt.provider is None or rt.profile is None:
            return
        async with self._taking_input():
            if self._task is not None or self._history or self._greeted:
                return
            self._greeted = True
            await self._stop_listening()
            self._via = "text"
            self._arrived = self._starting("greet")
            self._queued.append(greeting(rt.identity))
            self._begin()

    # ── Listening ────────────────────────────────────────────────────────

    async def _on_barge(self) -> None:
        """The user talked over her (VOI-9): her reply stops, what she had said is kept
        and marked interrupted, and she listens. The page heard them start and holds
        their first words until listening opens, so nothing they said is lost.

        Stopping her is not cancelling work (VOI-15c): an action already approved
        finishes and is recorded before she listens."""
        self._spoken = None
        async with self._taking_input():
            task = self._task
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError, Exception):
                    await asyncio.wait_for(asyncio.shield(task), BARGE_WAIT_S)
            await self._on_listen(NOTHING_AFTER_S)

    async def _on_listen(self, patience_s: float, *, follow_up: bool = False) -> None:
        async with self._taking_input():
            await self._listen(patience_s, follow_up=follow_up)

    async def _listen(self, patience_s: float, *, follow_up: bool) -> None:
        rt = self._rt
        if self._listener is not None:
            return
        if rt.stt is None or rt.listening is None:
            if not follow_up:
                await self._error("no_listening")
        elif rt.provider is None:
            await self._error("no_model")
        elif self._task is not None:
            await self._error("busy")
        else:
            self._spoken = None
            listener = Listener(
                rt.stt, rt.listening.choice, on_partial=self._partial, patience_s=patience_s
            )
            self._listener = listener
            self._last_audio = asyncio.get_running_loop().time()
            self._warm()
            await self._state("arming")
            # The page opens the microphone now; what it sends while the recognizer is
            # still connecting is held, so the first word is not lost.
            await self._send("mic.start", follow_up=follow_up)
            try:
                await listener.start()
            except VoiceError as e:
                await self._listening_failed(e.kind)
                return
            if self._listener is listener:
                self._watchdog = asyncio.create_task(self._watch(listener))
                await self._state("listening")

    async def audio(self, pcm: bytes) -> None:
        """A binary frame from the page: microphone audio, 16 kHz mono PCM16 (VOI-1)."""
        listener = self._listener
        if self._closed or listener is None or len(pcm) > MAX_AUDIO_FRAME:
            return
        self._last_audio = asyncio.get_running_loop().time()
        try:
            verdict = await listener.audio(pcm)
        except VoiceError as e:
            await self._listening_failed(e.kind)
            return
        if verdict is not None and self._listener is listener:
            await self._conclude(verdict)

    async def _watch(self, listener: Listener) -> None:
        """A microphone that goes silent on the wire never reaches a verdict by itself."""
        loop = asyncio.get_running_loop()
        while self._listener is listener:
            await asyncio.sleep(0.5)
            if self._listener is listener and loop.time() - self._last_audio > NO_AUDIO_S:
                self._watchdog = None
                await self._conclude("inaudible")
                return

    async def _partial(self, text: str) -> None:
        await self._send("transcript.partial", text=text)

    async def _stop_listening(self) -> Listener | None:
        listener, self._listener = self._listener, None
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        if listener is not None:
            await listener.abandon()
        return listener

    async def _listening_failed(self, kind: str) -> None:
        logger.warning("listening failed in session %s: %s", self.id, kind)
        await self._stop_listening()
        await self._send("mic.stop", reason="failed")
        # Typing still works: one leg down never takes the conversation with it (VOI-14).
        await self._hearing_down(kind)
        await self._state(self._rest)

    async def _hearing_down(self, kind: str) -> None:
        """She cannot hear: say so in one sentence, aloud if her voice works (VOI-14)."""
        said = degraded("hearing", kind)
        await self._error("listening", kind=kind, sentence=said)
        rt = self._rt
        if rt.tts is None or rt.speech is None or self._muted:
            return
        # Not a spoken turn any more: the end of this sentence must not reopen the
        # microphone that just failed, or one fault would repeat itself forever.
        self._via = "text"
        heard_as = rt.out_of_turn()
        speaker = Speaker(rt.tts, rt.speech.voice, heard_as, self._send, self._send_bytes)
        self._saying = True
        try:
            await speaker.feed(said)
            await speaker.finish()
            self._spoken = (heard_as, rt.clock())
        finally:
            self._saying = False

    async def _conclude(self, verdict: Verdict) -> None:
        """Listening is over. Either there is something to answer, or the reason is said."""
        # Until the answer is the session's task, nothing is delivered in between.
        async with self._taking_input():
            await self._concluded(verdict)

    async def _concluded(self, verdict: Verdict) -> None:
        listener, self._listener = self._listener, None
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        if listener is None:
            return
        await self._send("mic.stop", reason=verdict)
        if verdict == "inaudible":
            # Enough to tell a paused page from a muted microphone, and nothing heard.
            logger.warning(
                "listening ended (%s): %d frames, %.1fs of audio, loudest %.4f, page audio %s",
                verdict,
                listener.frames,
                listener.heard_s,
                listener.peak,
                self._mic_setup or "not reported",
            )
        if verdict == "inaudible":
            # Said out loud rather than failing silently (VOI-4).
            await listener.abandon()
            await self._hearing_down("inaudible")
            await self._state(self._rest)
        elif verdict == "nothing":
            await listener.abandon()
            await self._state(self._rest)
        else:
            await self._state("processing")
            # The turn starts when they stopped talking, not when that was noticed.
            quiet = listener.quiet_s
            trace = self._starting("voice", ago=quiet)
            trace.endpoint_s = quiet
            self._task = asyncio.create_task(self._heard(listener, trace))

    async def _heard(self, listener: Listener, trace: Trace) -> None:
        """Collect the words, then answer them. Runs as the session's one task."""
        answer = False
        try:
            said = await listener.end()
            text = said.text[:MAX_TEXT].strip()
            if text:
                trace.mark("heard", self._now())
                self._arrived = trace
                await self._send("transcript.final", text=text)
                self._via = "voice"
                self._turn += 1
                self.active_ms = self._rt.clock()
                await self._save_input("user", text)
                answer = True
            else:
                # Sound, but no words in it. Said, so the silence is not a mystery.
                await self._send("notice", code="listening", reason="unheard")
        except VoiceError as e:
            logger.warning("listening failed in session %s: %s", self.id, e.kind)
            await self._hearing_down(e.kind)
        except asyncio.CancelledError:
            await listener.abandon()
            await self._send("interrupted")
        except Exception as e:
            logger.error("listening failed in session %s: %s", self.id, type(e).__name__)
            await self._error("internal")
        if answer:
            await self._run()
        else:
            self._task = None
            await self._state(self._rest)

    async def _on_tap(self, pending_id: object, decision: Literal["confirm", "cancel"]) -> None:
        if not isinstance(pending_id, str) or not pending_id:
            await self._error("bad_message")
            return
        self.active_ms = self._rt.clock()
        async with self._taking_input():
            await self._tapped(pending_id, decision)

    async def _tapped(self, pending_id: str, decision: Literal["confirm", "cancel"]) -> None:
        # A tap is the user's own act on one card, so it is allowed at once: it is judged
        # as the turn after the newest thing said. It does not advance the turn the model
        # acts in. If it did, tapping one card mid-turn would let the model confirm
        # another in the very turn it proposed it (SPN-2e).
        response = await self._rt.router.resolve(
            pending_id,
            decision,
            self._ctx(f"tap-{uuid.uuid4().hex}", via="tap", turn=self._turn + 1),
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
            self._begin()

    # ── A turn ───────────────────────────────────────────────────────────

    def _begin(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """Turns, one after another, until nothing is waiting.

        `_task` stays set for the whole of it, so a second turn can never start beside
        this one: a message that arrives meanwhile is refused as busy, and a tap's note
        is picked up by the loop."""
        try:
            while not self._closed:
                if self._queued:
                    # What the taps did, in one message, for her to answer.
                    note, self._queued = "\n".join(self._queued), []
                    await self._save_input("tap", note)
                if self._rt.provider is None:
                    break
                await self._compact_if_long()
                await self._one_turn()
                if not self._queued:
                    break
        finally:
            # Only its own: never a task that started after this one was let go.
            if self._task is asyncio.current_task():
                self._task = None
        await self._state(self._rest)

    async def _one_turn(self) -> None:
        rt = self._rt
        # The turn she acts in is fixed when it starts. Nothing that happens while she
        # works, a tap least of all, can move her into a later one.
        turn = self._turn
        provider = rt.provider
        if provider is None:  # pragma: no cover - callers check before starting a turn
            return
        speaking = False
        speaker: Speaker | None = None
        heard_as = turn
        if rt.tts is not None and rt.speech is not None and not self._muted:
            # A turn's number is heard once: a tap's answer in the same turn gets its own,
            # or the page's "played" for the first would pass for it (VOI-17).
            heard_as = rt.out_of_turn() if self._voiced == turn else turn
            self._voiced = turn
            speaker = Speaker(rt.tts, rt.speech.voice, heard_as, self._send, self._send_bytes)
        # A turn nobody started (a tap's answer) starts now.
        trace, self._arrived = self._arrived or self._starting("tap"), None
        trace.turn = turn
        trace.heard_as = heard_as
        self._trace = trace

        async def emit(ev: LoopEvent) -> None:
            nonlocal speaking
            if isinstance(ev, CallTimed):
                trace.call(ev)
            elif isinstance(ev, ReplyDelta):
                trace.mark("first_text", self._now())
                if not speaking:
                    speaking = True
                    await self._state("speaking")
                await self._send("reply.delta", text=ev.text)
                if speaker is not None:
                    await speaker.feed(ev.text)
            elif isinstance(ev, ToolStarted):
                trace.tools += 1
                if speaker is not None:
                    await speaker.pause()
                speaking = False
                await self._state("processing")
                await self._send("tool.start", call_id=ev.call_id, name=ev.name, risk=ev.risk)
            elif isinstance(ev, ToolEnded):
                r = ev.response
                if is_outside(ev.name, rt.registry.definition(ev.name)) and r.status == "succeeded":
                    # From here to the end of the turn, what she writes to memory is
                    # something she read, not something the user said.
                    self._outside.add(turn)
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
            # Rules and the skills that are on, read once for this turn, off the event loop.
            system = await asyncio.to_thread(rt.system)
            done = await run_turn(
                provider=provider,
                router=rt.router,
                registry=rt.registry,
                system=system,
                history=compact.with_summary(self._summary, self._history),
                ctx=lambda call_id: self._ctx(call_id, turn=turn),
                emit=emit,
                save=self._save_from_loop,
            )
            # The model failed: her one sentence for it, spoken if her voice still works
            # and shown either way (VOI-14). Authored, since there is nobody to compose it.
            down = (
                degraded("model", done.error_kind or "other")
                if done.stop == "provider_error"
                else None
            )
            if speaker is not None:
                if down is not None:
                    await speaker.pause()
                    await speaker.feed(down)
                await speaker.finish()
                self._spoken = (heard_as, rt.clock())
            await rt.conversations.record_usage(
                owner_id=OWNER,
                conversation_id=self.id,
                provider=provider.name,
                model=provider.model,
                usage=done.usage,
                cost_usd=done.cost_usd,
                now_ms=rt.clock(),
            )
            if down is not None:
                final = "error"
                await self._error("provider", kind=done.error_kind, sentence=down)
            elif done.stop != "end":
                await self._send("notice", code="stopped", reason=done.stop)
            # Kept before the page hears the turn is over, so the next one never waits on it.
            await self._keep_trace(trace, done.stop, voiced=speaker is not None)
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
            if speaker is not None:
                await speaker.stop()
                # Stopped: nothing of it is still to be heard.
                self._spoken = None
            await self._keep_trace(trace, "interrupted", voiced=speaker is not None)
            await self._send("interrupted")
        except Exception as e:
            # The class name only: an exception message can carry conversation text.
            logger.error("turn failed in session %s: %s", self.id, type(e).__name__)
            final = "error"
            await self._keep_trace(trace, "error", voiced=speaker is not None)
            await self._error("internal")
        self._rest = final

    # ── Timing (ONB-11) ──────────────────────────────────────────────────

    def _warm(self) -> None:
        """While they talk, open the connections her answer will need: the model's and
        the voice's. Nothing is sent; a provider with nothing to open is skipped."""
        rt = self._rt
        calls = [
            warm()
            for p in (rt.provider, None if self._muted else rt.tts)
            if p is not None and callable(warm := getattr(p, "warm", None))
        ]
        if calls and (self._warming is None or self._warming.done()):
            self._warming = asyncio.ensure_future(asyncio.gather(*calls))
        else:
            for call in calls:
                call.close()

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _starting(self, via: Via, *, ago: float = 0.0) -> Trace:
        return Trace(via=via, t0=self._now() - ago)

    async def _keep_trace(self, trace: Trace, stop: str, *, voiced: bool) -> None:
        """Save how the turn went, once. A timing that cannot be saved costs nothing else."""
        if self._trace is not trace:
            return
        trace.mark("saved", self._now())
        self._trace = None
        self._last_trace = trace
        rt = self._rt
        provider = rt.provider
        # A playback report can come while the row is being saved: it marks the trace,
        # but there is no row to update yet. It is written once the row exists.
        had_played = "played" in trace.marks
        row = trace.row(
            conversation_id=self.id,
            first_in_session=self._timed == 0,
            provider=provider.name if provider is not None else "",
            model=provider.model if provider is not None else "",
            voice=rt.speech.kind if voiced and rt.speech is not None else None,
            ears=rt.listening.kind if trace.via == "voice" and rt.listening is not None else None,
            stop=stop,
        )
        self._timed += 1
        try:
            trace.row_id = await rt.conversations.record_timing(OWNER, row, rt.clock())
            if not had_played and "played" in trace.marks:
                played = trace.ms(trace.marks["played"])
                await rt.conversations.timing_played(OWNER, trace.row_id, played)
        except Exception as e:
            logger.warning("timing not saved in session %s: %s", self.id, type(e).__name__)

    async def _audible(self, turn_id: object, in_ms: object) -> None:
        """The page scheduled the first sound of a turn's reply, to be heard this far ahead."""
        if isinstance(turn_id, bool) or not isinstance(turn_id, int):
            return
        at = self._now() + ahead_ms(in_ms) / 1000
        running, last = self._trace, self._last_trace
        if running is not None and running.heard_as == turn_id:
            running.mark("played", at)
        elif last is not None and last.heard_as == turn_id and last.mark("played", at):
            # Heard after the turn was saved: a long first sentence, or a slow page.
            if last.row_id is not None:
                with contextlib.suppress(Exception):
                    await self._rt.conversations.timing_played(OWNER, last.row_id, last.ms(at))

    # ── Helpers ──────────────────────────────────────────────────────────

    def _ctx(
        self,
        call_id: str,
        via: Literal["voice", "text", "tap", "channel"] | None = None,
        *,
        turn: int | None = None,
    ) -> ToolContext:
        at = self._turn if turn is None else turn
        return ToolContext(
            owner_id=OWNER,
            session_id=self.id,
            turn=at,
            call_id=call_id,
            via=via or self._via,
            granted_roots=self._rt.granted_roots,
            read_outside=at in self._outside,
        )

    async def _save(self, kind: Kind, msg: Msg) -> None:
        self._history.append(msg)
        self._kinds.append(kind)
        message_id = await self._rt.conversations.append(
            self.id, OWNER, kind, msg, self._rt.clock()
        )
        self._ids.append(message_id)

    async def _compact_if_long(self) -> None:
        """Summarize the oldest turns once the history outgrows the model (BRN-5).

        Before a turn, never during one. If the summary cannot be written, the turn
        goes ahead on the full history: a long prompt is better than a lost one."""
        rt = self._rt
        provider = rt.provider
        if provider is None or compact.estimate(self._history) <= rt.compact_at:
            return
        cut = compact.cut_point(self._kinds, self._history, rt.compact_keep)
        if cut is None:
            return
        try:
            summary = await asyncio.wait_for(
                compact.summarize(
                    provider, compaction(rt.identity), self._summary, self._history[:cut]
                ),
                compact.TIMEOUT_S,
            )
        except (ProviderError, compact.CompactionFailed, TimeoutError) as e:
            logger.warning("compaction in session %s failed: %s", self.id, type(e).__name__)
            return
        await rt.conversations.compact(self.id, OWNER, self._ids[cut - 1], summary.text, rt.clock())
        await rt.conversations.record_usage(
            owner_id=OWNER,
            conversation_id=self.id,
            provider=provider.name,
            model=provider.model,
            usage=summary.usage,
            cost_usd=pricing.cost(summary.usage) if (pricing := provider.pricing()) else None,
            now_ms=rt.clock(),
        )
        self._summary = summary.text
        del self._history[:cut], self._kinds[:cut], self._ids[:cut]
        await self._send("notice", code="compacted")

    async def _save_input(self, kind: Kind, text: str) -> None:
        """A user input, with the context it was said in. Both are saved together and
        replayed unchanged on every later turn: history is only ever appended to."""
        pending = [
            PendingNote(pending_id=a.id, summary=a.summary) for a in await self._open_actions()
        ]
        spoken = kind == "user" and self._via == "voice"
        # The user's own clock, so "at four" means four where they are (AGT-9).
        zone = self._rt.zone()
        context = volatile_context(
            datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone(),
            pending,
            self._remembered(text) if kind == "user" else "",
            # Said aloud: she is told it is a transcript, and that she will be heard (#40).
            spoken=self._rt.identity if spoken else None,
            zone=zone or "",
        )
        blocks = (ContextBlock(text=context), TextBlock(text=text))
        if kind == "user" and self._rt.web is not None:
            # Only what the user said themselves: never a tap's note, never a page.
            self._rt.web.ledger.said(self.id, text)
        await self._save(kind, Msg(role="user", blocks=blocks))

    def _remembered(self, said: str) -> str:
        """What she has saved that bears on this message, as untrusted text (MEM-5).

        It is saved with the message like the rest of its context, so the history
        replays byte for byte. A correction or a forgetting therefore shows from the
        next message on, not in messages already sent (MEM-6b)."""
        rt = self._rt
        fresh = [m for m in rt.memory.recall(said) if m.id not in self._brought]
        text, used = present.alongside(fresh, rt.clock())
        self._brought.update(m.id for m in used)
        return wrap("memory_recall", text) if used else ""

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
        if orb in ("idle", "error"):
            # At rest: anything held for this page may arrive now.
            await self._offer()

    # ── Notices (SUR-9, VOI-17) ──────────────────────────────────────────

    def facts(self) -> Facts:
        """What the door judges "free" by. Read from what is running, never a flag."""
        task = self._task
        spoken = self._spoken
        return Facts(
            turn_running=(task is not None and not task.done())
            or self._saying
            or self._taking > 0
            or not self._started,
            listening=self._listener is not None,
            spoken_turn=spoken[0] if spoken is not None else None,
            spoken_ms=spoken[1] if spoken is not None else None,
            heard_turn=self._played,
            active_ms=self.active_ms,
        )

    @contextlib.asynccontextmanager
    async def _taking_input(self) -> AsyncIterator[None]:
        """Around taking in what the user did: nothing is delivered until it is the
        session's task or has come to nothing, and notice speech gives way to it."""
        # Counted before the first await, so a tick in any gap sees the page busy.
        self._taking += 1
        try:
            await self._hush()
            yield
        finally:
            self._taking -= 1
        if self._taking == 0 and self._task is None:
            # It came to nothing: what was held for it may arrive now.
            await self._offer()

    async def _hush(self) -> None:
        """Stop speaking notices, and wait until that has stopped. They stay saved."""
        task = self._notice_task
        if task is not None and task is self._task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _offer(self) -> None:
        door = self._rt.door
        if door is None or self._closed:
            return
        try:
            await door.offer(self)
        except Exception as e:
            # The notices stay held; the next tick's flush tries again.
            logger.warning("notices not offered in session %s: %s", self.id, type(e).__name__)

    async def deliver(self, notices: Sequence[Delivery]) -> None:
        """The door hands over notices while this page is free. All or none: a page
        that is no longer free raises `NotFree` before anything happens, and the door
        holds them again. The ones with her words are saved as her messages in one
        statement, so they survive a reload; then each is sent, and spoken if her
        voice is on, best effort. A missed or failed one is shown by its title, never
        spoken (PER-11). Speech is queued, never waited for."""
        rt = self._rt
        if self._closed or not free(self.facts(), rt.clock()):
            raise NotFree
        said = [n.text for n in notices if n.text]
        if said:
            await self._save_all(
                "assistant", [Msg(role="assistant", blocks=(TextBlock(text=t),)) for t in said]
            )
        if any(n.kind == "ask" and n.text for n in notices):
            # A timed ask's answer can quote what it read: whatever she does next on the
            # strength of it is something she read, not something the user said (MEM-5).
            self._outside.update({self._turn, self._turn + 1})
        # Saved: from here nothing raises, or the door would hold them and they would be
        # saved twice. `_send` already logs a failed send and goes on.
        for n in notices:
            await self._send(
                "notice",
                code="schedule",
                id=n.id,
                kind=n.kind,
                title=n.title,
                text=n.text,
                due_ms=n.due_ms,
                late=n.late,
            )
        if said and rt.tts is not None and rt.speech is not None and not self._muted:
            if self._task is None and not self._closed:
                self._task = self._notice_task = asyncio.create_task(self._say_notices(said))

    async def _save_all(self, kind: Kind, msgs: Sequence[Msg]) -> None:
        """Like `_save`, for several at once: in the history before the write, so a turn
        that starts meanwhile has them, and taken out again if the write fails."""
        n = len(msgs)
        self._history.extend(msgs)
        self._kinds.extend([kind] * n)
        try:
            ids = await self._rt.conversations.append_many(
                self.id, OWNER, kind, msgs, self._rt.clock()
            )
        except BaseException:
            del self._history[-n:], self._kinds[-n:]
            raise
        self._ids.extend(ids)

    async def _say_notices(self, said: list[str]) -> None:
        """Her words for the notices, through the normal speech path. Runs as the
        session's task, so nothing else starts over it, and "stop" stops it."""
        rt = self._rt
        if rt.tts is None or rt.speech is None:  # pragma: no cover - checked by deliver
            self._task = None
            return
        turn = rt.out_of_turn()
        # Not a spoken turn: when she has said it, the microphone stays closed.
        self._via = "text"
        speaker = Speaker(rt.tts, rt.speech.voice, turn, self._send, self._send_bytes)
        try:
            await self._state("speaking")
            for text in said:
                await speaker.feed(text)
                await speaker.pause()
            await speaker.finish()
            self._spoken = (turn, rt.clock())
        except asyncio.CancelledError:
            await speaker.stop()
            self._spoken = None
        except Exception as e:
            # Shown already and saved: only the speaking failed.
            logger.warning("notice speech failed in session %s: %s", self.id, type(e).__name__)
        finally:
            if self._task is asyncio.current_task():
                self._task = None
            if self._notice_task is asyncio.current_task():
                self._notice_task = None
        await self._state(self._rest)

    async def _error(self, code: str, **extra: Any) -> None:
        await self._send("error", code=code, **extra)

    async def _send_bytes(self, data: bytes) -> None:
        if self._closed or self._raw_send_bytes is None:
            return
        if self._trace is not None:
            self._trace.mark("first_audio", self._now())
        try:
            await self._raw_send_bytes(data)
        except Exception as e:
            logger.debug("send of audio failed: %s", type(e).__name__)

    async def _send(self, kind: str, /, **body: Any) -> None:
        """Never raises: a closed socket must not break a turn that is still recording."""
        if self._closed:
            return
        if kind == "reply.sentence" and self._trace is not None:
            self._trace.mark("first_sentence", self._now())
        try:
            await self._raw_send({"type": kind, **body})
        except Exception as e:
            logger.debug("send of %s failed: %s", kind, type(e).__name__)
