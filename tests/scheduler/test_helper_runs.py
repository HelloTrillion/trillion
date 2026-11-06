"""A helper's runs (the your helpers spec): a handoff runs in its chat with its overlay
and ends with one ready line named for it; its own routines run as any routine, in its
chat. Paused, capped or deleted, it is not run."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.brain.fakes import say, use
from tests.delivery.test_door import FakeNotify, FakePage
from tests.scheduler.conftest import FakeClock, ms, schedule
from tests.scheduler.test_ask import Recording
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import routine_run
from trillion.brain.types import (
    ContextBlock,
    ModelEvent,
    ProviderError,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
)
from trillion.delivery.door import READY, Door
from trillion.helpers.pausing import pause_helper, resume_helper
from trillion.helpers.store import OwnHelper
from trillion.persona.prompt import helper_overlay, system_blocks
from trillion.scheduler import runner
from trillion.scheduler.ask import HANDOFF_RUN, Asker, RoutineDeps
from trillion.scheduler.runner import Scheduler, handoff_title
from trillion.scheduler.when import Repeat
from trillion.secrets.store import MemoryStore
from trillion.spend.caller import current
from trillion.spend.types import Work
from trillion.spine.contract import ToolContext

OWNER = "local"
NOW = ms("2026-11-05T09:00")
DAY = 86_400_000
JOB = "Keeps track of who owes you and chases late payments."
CTX = ToolContext(owner_id=OWNER, session_id="chat-1", turn=1, call_id="t1", via="text")


class Seen(Recording):
    """A scripted model that also keeps the work each call was made as."""

    def __init__(self, *replies: list[ModelEvent] | Exception) -> None:
        super().__init__(*replies)
        self.work: list[Work | None] = []

    async def stream(self, *, tools: Sequence[ToolSchema], **kw: Any) -> AsyncIterator[ModelEvent]:
        self.work.append(current().work)
        async for ev in super().stream(tools=tools, **kw):
            yield ev


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(granted,),
        persona=tmp_path,
        secrets=MemoryStore(),
        env={},
    )
    yield runtime
    await runtime.close()


class Rig:
    def __init__(
        self, rt: Runtime, provider: Seen, *, capped: bool = False, page: bool = True
    ) -> None:
        assert rt.schedules is not None and rt.helpers is not None
        self.rt, self.provider, self.capped = rt, provider, capped
        self.schedules, self.helpers = rt.schedules, rt.helpers
        self.clock = FakeClock(NOW)
        rt.clock = self.clock  # type: ignore[method-assign]
        metered = rt.meter.model(provider, None)
        self.asker = Asker(
            provider=lambda: metered,
            router=rt.router,
            registry=rt.registry,
            conversations=rt.conversations,
            system=lambda scope: system_blocks(rt.identity, rt.soul, rt.shared(scope)),
            identity=lambda: rt.identity,
            shares=rt.reach,
            zone=lambda: "America/New_York",
            clock=self.clock,
            owner=OWNER,
            routines=RoutineDeps(boards=lambda: rt.boards),
            helpers=lambda: rt.helpers,
        )
        self.page = FakePage("chat-1")
        self.notify = FakeNotify()
        pages = [self.page] if page else []
        self.door = Door(self.schedules, pages, clock=self.clock, owner=OWNER, notify=self.notify)
        self.door.helpers = rt.helpers

        async def held(work: Work) -> bool:
            return self.capped and work == "helpers"

        self.scheduler = Scheduler(
            self.schedules, self.clock, self.asker, self.door, routines=self.asker, held=held
        )

    async def made(self, name: str = "Invoices") -> OwnHelper:
        h = await self.helpers.create(OWNER, name, JOB, NOW)
        assert isinstance(h, OwnHelper)
        return h

    async def ask(self, h: OwnHelper, request: str = "Which invoices are overdue?") -> str:
        tool = self.rt.registry.get("helper_ask")
        assert tool is not None
        r = await tool.execute({"helper": h.id, "request": request}, CTX)
        assert r.ok, r.content
        (s,) = [s for s in await self.schedules.for_helper(OWNER, h.id) if s.target is None]
        return s.id

    async def runs(self) -> list[tuple[str, str | None]]:
        async with self.schedules._conn.execute(
            "SELECT status, detail FROM task_runs ORDER BY id"
        ) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    def system(self, call: int = 0) -> str:
        return "".join(b.text for b in self.provider.calls[call][0])


def texts(msg: Any) -> list[str]:
    return [b.text for b in msg.blocks if isinstance(b, TextBlock)]


def routine_of(h: OwnHelper, **kw: Any) -> Any:
    base: dict[str, Any] = {
        "id": "r0000001",
        "kind": "ask",
        "title": "Overdue check",
        "body": "Check which invoices went overdue.",
        "target": "own",
        "at_local": "2026-11-05T09:00",
        "next_due_ms": NOW,
        "repeat": Repeat(every="day"),
        "helper_id": h.id,
    }
    return schedule(**{**base, **kw})


# ── Handoffs ─────────────────────────────────────────────────────────────


async def test_a_handoff_runs_in_its_chat_and_a_ready_line_names_it(rt: Runtime) -> None:
    it = Rig(rt, Seen(say("3 invoices are overdue, $4,200 in all. The oldest is March.")))
    h = await it.made()
    sid = await it.ask(h)
    await it.scheduler.tick()

    history = await rt.conversations.history(h.conversation_id, OWNER)
    assert [k for k, _ in history] == ["tap", "assistant"]
    asked = history[0][1]
    # The request is the host's, never the user's words.
    assert ContextBlock(text=HANDOFF_RUN) in asked.blocks
    assert texts(asked) == ["Which invoices are overdue?"]
    # Its chat shows it again as the host's line.
    assert routine_run(asked) == "Which invoices are overdue?"
    assert texts(history[1][1]) == ["3 invoices are overdue, $4,200 in all. The oldest is March."]

    (got,) = it.page.got
    title = "Invoices: 3 invoices are overdue, $4,200 in all."
    assert (got.kind, got.title, got.text) == ("ready", title, title)
    assert got.conversation_id == h.conversation_id
    assert (got.helper_id, got.helper_name) == (f"own:{h.id}", "Invoices")
    # Model-written from what it read: whatever she does next counts as read.
    assert got.handoff
    assert await it.runs() == [("delivered", None)]
    s = await it.schedules.get(sid)
    assert s is not None and s.status == "done"

    # Her instructions, the scheduled overlay, then its own, then the handoff line.
    system = it.system()
    overlay = helper_overlay(h, rt.identity.agent_name)
    assert system.index("A TIMED ASK") < system.index(overlay) < system.index("Needs you:")
    # Background work of a helper, for the spend gate.
    assert it.provider.work == ["helpers"]


async def test_nothing_to_say_still_says_so(rt: Runtime) -> None:
    it = Rig(rt, Seen(say("  ")))
    h = await it.made()
    await it.ask(h)
    await it.scheduler.tick()
    (got,) = it.page.got
    assert (got.kind, got.title) == ("ready", "Invoices: nothing to report")
    assert got.text == "Invoices: nothing to report."
    assert await it.runs() == [("delivered", None)]


async def test_needs_you_reads_as_it_needs_you(rt: Runtime) -> None:
    it = Rig(rt, Seen(say("Needs you: approve sending 3 reminders. They're drafted.")))
    h = await it.made()
    await it.ask(h)
    await it.scheduler.tick()
    (got,) = it.page.got
    assert got.title == "Invoices needs you: approve sending 3 reminders."
    assert got.conversation_id == h.conversation_id


async def test_a_carded_tool_is_refused_and_the_run_still_ends_with_its_line(
    rt: Runtime,
) -> None:
    target = rt.granted_roots[0] / "made.txt"
    it = Rig(
        rt,
        Seen(
            use("file_create", {"path": str(target), "content": "x"}, "c1"),
            use("helper_ask", {"helper": "x", "request": "y"}, "c2"),
            say("Needs you: let me write the summary file."),
        ),
    )
    h = await it.made()
    await it.ask(h)
    await it.scheduler.tick()
    history = await rt.conversations.history(h.conversation_id, OWNER)
    results = [
        json.loads(b.content)
        for _, m in history
        for b in m.blocks
        if isinstance(b, ToolResultBlock)
    ]
    assert [(r["status"], r["reason"]) for r in results] == [
        ("refused", "unattended"),
        # Its context says it is a helper's run: it never hands work on.
        ("refused", "not_for_helpers"),
    ]
    assert not target.exists()
    (got,) = it.page.got
    assert got.title == "Invoices needs you: let me write the summary file."


async def test_a_deleted_helper_fails_the_run_with_why(rt: Runtime) -> None:
    it = Rig(rt, Seen(say("never")))
    h = await it.made()
    await it.ask(h)
    # Gone between the ask and its run, its handoff not yet cancelled.
    async with it.helpers._conn.execute(
        "UPDATE helpers SET deleted_at = ? WHERE id = ?", (NOW, h.id)
    ):
        pass
    await it.helpers._conn.commit()
    await it.scheduler.tick()
    assert it.provider.calls == []
    assert await it.runs() == [("failed", "helper deleted")]
    (got,) = it.page.got
    assert got.kind == "failed" and (got.helper_id, got.helper_name) == (None, None)


async def test_a_lock_screen_shows_its_name_never_its_words(rt: Runtime) -> None:
    it = Rig(rt, Seen(say("3 invoices are overdue, $4,200 in all.")), page=False)
    h = await it.made()
    await it.ask(h)
    await it.scheduler.tick()
    assert it.notify.shown == [("Invoices", READY)]


async def test_a_model_failure_is_one_failed_notice_and_no_ready_line(rt: Runtime) -> None:
    it = Rig(rt, Seen(ProviderError("rate_limited")))
    h = await it.made()
    await it.ask(h)
    await it.scheduler.tick()
    assert await it.runs() == [("failed", "the model failed: rate_limited")]
    assert [got.kind for got in it.page.got] == ["failed"]


async def test_a_capped_helper_is_not_run(rt: Runtime) -> None:
    it = Rig(rt, Seen(say("never")))
    h = await it.made()
    await it.ask(h)
    it.capped = True
    await it.scheduler.tick()
    assert it.provider.calls == []
    assert await it.runs() == [("skipped", "capped")]
    assert it.page.got == []


# ── Its routines ─────────────────────────────────────────────────────────


async def test_its_routine_runs_in_its_chat_with_its_overlay(rt: Runtime) -> None:
    it = Rig(rt, Seen(say("Two went overdue today.")))
    h = await it.made()
    await it.schedules.create(routine_of(h))
    await it.scheduler.tick()
    history = await rt.conversations.history(h.conversation_id, OWNER)
    assert [k for k, _ in history] == ["tap", "assistant"]
    system = it.system()
    assert helper_overlay(h, rt.identity.agent_name) in system
    # A routine's reply stays in its chat: no handoff line.
    assert "Needs you:" not in system
    assert it.provider.work == ["helpers"]
    (got,) = it.page.got
    assert (got.kind, got.title) == ("ready", "Your Overdue check is ready")
    assert not got.handoff
    assert got.conversation_id == h.conversation_id
    assert (got.helper_id, got.helper_name) == (f"own:{h.id}", "Invoices")


async def test_a_paused_helpers_routine_waits_and_resumes_without_a_replay(
    rt: Runtime,
) -> None:
    it = Rig(rt, Seen(say("Two went overdue today.")))
    h = await it.made()
    await it.schedules.create(routine_of(h))
    assert await pause_helper(it.helpers, it.schedules, OWNER, h.id, NOW - 1)
    await it.scheduler.tick()
    it.clock.advance(3 * DAY)
    await it.scheduler.tick()
    assert it.provider.calls == [] and await it.runs() == []

    assert await resume_helper(it.helpers, it.schedules, runner, OWNER, h.id, it.clock())
    await it.scheduler.tick()
    # Nothing it was paused through is run late.
    assert it.provider.calls == [] and await it.runs() == []
    it.clock.now = ms("2026-11-09T09:00")
    await it.scheduler.tick()
    assert len(it.provider.calls) == 1
    assert await it.runs() == [("delivered", None)]


# ── The ready line's title ───────────────────────────────────────────────


def test_the_title_is_its_first_sentence_within_140() -> None:
    assert handoff_title("Invoices", "One.  Two.") == "Invoices: One."
    one = handoff_title("Invoices", "Line one\nstill   one. Two.")
    assert one == "Invoices: Line one still one."
    assert handoff_title("Invoices", "") == "Invoices: nothing to report"
    assert handoff_title("Invoices", "Needs you:") == "Invoices needs you"
    assert handoff_title("Invoices", "needs you: approve it. Then more.") == (
        "Invoices needs you: approve it."
    )
    long = handoff_title("Invoices", "x" * 300)
    assert len(long) == 140 and long.endswith("…")
