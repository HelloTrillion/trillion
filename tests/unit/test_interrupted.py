"""She tells the user about a send a crash interrupted, only when in doubt (OPS-1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from tests.brain.fakes import USAGE, ScriptedProvider, say
from trillion.api.runtime import Runtime, open_runtime
from trillion.brain.types import (
    ModelEvent,
    Msg,
    Pricing,
    ProviderError,
    SystemBlock,
    TextBlock,
    ToolSchema,
    Usage,
)
from trillion.delivery.interrupted import NOT_SENT, TITLE, UNKNOWN, tell
from trillion.scheduler.store import Notice
from trillion.secrets.store import MemoryStore
from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.router import NOT_SENT as NOT_SENT_DETAIL
from trillion.spine.router import Router, Settled
from trillion.spine.store import DecisionRow
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore
from trillion.storage.sqlite.spine_store import SqliteSpineStore
from trillion.tools.record import describe

OWNER = "local"
SUMMARY = "Send email to Sam: Lunch on Friday"


def action(pid: str = "pa1", status: Status = Status.INVALIDATED) -> PendingAction:
    return PendingAction(
        id=pid,
        owner_id=OWNER,
        tool="gmail_send",
        args={},
        args_hash="h",
        summary=SUMMARY,
        target="sam@example.com",
        risk=Risk.IRREVERSIBLE,
        recoverable=False,
        session_id="s1",
        created_turn=1,
        status=status,
        created_at=1,
        expires_at=10**12,
        represented_from=None,
        outcome_detail=NOT_SENT_DETAIL,
    )


ACTIONS = {"pa1": action()}


async def get_pending(pid: str) -> PendingAction | None:
    return ACTIONS.get(pid)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteScheduleStore]:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        yield SqliteScheduleStore(conn)
    finally:
        await conn.close()


async def test_not_sent_makes_one_notice_with_her_line(store: SqliteScheduleStore) -> None:
    said = "The email to Sam didn't go out before I stopped. Want me to send it?"
    p = ScriptedProvider(say(said))
    made, usage = await tell(
        store, p, OWNER, [Settled(id="pa1", outcome="not_sent")], get_pending, now_ms=1
    )
    (n,) = await store.held(OWNER)
    assert made == [n]
    assert n.title == TITLE == "A send was interrupted"
    assert n.text and "send it" in n.text
    assert n.source_id == "action:pa1"
    assert usage == USAGE
    ((system, _),) = p.calls
    assert system[0].text == NOT_SENT


async def test_unknown_makes_one_notice(store: SqliteScheduleStore) -> None:
    p = ScriptedProvider(say("I can't tell whether the email to Sam went. Worth a look in Sent."))
    await tell(store, p, OWNER, [Settled(id="pa1", outcome="unknown")], get_pending, now_ms=1)
    (n,) = await store.held(OWNER)
    assert n.text and "Sent" in n.text
    ((system, _),) = p.calls
    assert system[0].text == UNKNOWN
    assert "not offer" in UNKNOWN.lower() or "never offer" in UNKNOWN.lower()


async def test_succeeded_and_interrupted_say_nothing(store: SqliteScheduleStore) -> None:
    p = ScriptedProvider()
    made, usage = await tell(
        store,
        p,
        OWNER,
        [Settled(id="a", outcome="succeeded"), Settled(id="b", outcome="interrupted")],
        get_pending,
        now_ms=1,
    )
    assert made == [] and usage == Usage()
    assert await store.held(OWNER) == []
    assert p.calls == []


async def test_told_once(store: SqliteScheduleStore) -> None:
    p = ScriptedProvider(say("It didn't go out. Send it?"), say("It didn't go out. Send it?"))
    s = [Settled(id="pa1", outcome="not_sent")]
    await tell(store, p, OWNER, s, get_pending, now_ms=1)
    (n,) = await store.held(OWNER)
    # Delivered, then told again: still one, and no second paid call.
    assert await store.mark_delivered(n.id, "page", 2)
    made, _ = await tell(store, p, OWNER, s, get_pending, now_ms=3)
    assert made == [] and len(p.calls) == 1
    assert await store.held(OWNER) == []


async def test_no_model_is_shown_not_spoken(store: SqliteScheduleStore) -> None:
    await tell(store, None, OWNER, [Settled(id="pa1", outcome="unknown")], get_pending, now_ms=1)
    (n,) = await store.held(OWNER)
    assert n.text is None and n.title == TITLE


async def test_a_failed_call_is_shown_not_spoken(store: SqliteScheduleStore) -> None:
    p = ScriptedProvider(ProviderError("down"))
    await tell(store, p, OWNER, [Settled(id="pa1", outcome="not_sent")], get_pending, now_ms=1)
    (n,) = await store.held(OWNER)
    assert n.text is None


async def test_a_line_with_an_address_is_never_said(store: SqliteScheduleStore) -> None:
    p = ScriptedProvider(say("The email to sam@example.com didn't go out. Send it?"))
    await tell(store, p, OWNER, [Settled(id="pa1", outcome="not_sent")], get_pending, now_ms=1)
    (n,) = await store.held(OWNER)
    assert n.text is None


async def test_an_action_that_is_not_there_is_not_told(store: SqliteScheduleStore) -> None:
    p = ScriptedProvider()
    await tell(store, p, OWNER, [Settled(id="gone", outcome="unknown")], get_pending, now_ms=1)
    assert await store.held(OWNER) == [] and p.calls == []


async def test_the_summary_reaches_the_model_as_untrusted(store: SqliteScheduleStore) -> None:
    p = ScriptedProvider(say("It didn't go out. Send it?"))
    await tell(store, p, OWNER, [Settled(id="pa1", outcome="not_sent")], get_pending, now_ms=1)
    ((_, messages),) = p.calls
    (block,) = messages[0].blocks
    assert isinstance(block, TextBlock)
    start = block.text.index("<untrusted_tool_result")
    end = block.text.index("</untrusted_tool_result>")
    assert start < block.text.index(SUMMARY) < end
    assert p.tool_calls == [[]]


class Hangs:
    """A model that never answers: the tell is stopped mid-call."""

    name = "hangs"
    model = "hangs-1"

    def pricing(self) -> Pricing | None:
        return None

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        await asyncio.sleep(3600)
        yield say("late")[0]


async def test_the_notice_is_written_before_her_line_is_asked_for(
    store: SqliteScheduleStore,
) -> None:
    # A quit while the model is still writing her line: the notice is already kept,
    # shown by its title, with what it was on the page only.
    task = asyncio.create_task(
        tell(store, Hangs(), OWNER, [Settled(id="pa1", outcome="not_sent")], get_pending, now_ms=1)
    )
    for _ in range(200):
        if await store.has_notice(OWNER, "action:pa1"):
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (n,) = await store.held(OWNER)
    assert n.title == TITLE and n.text is None and n.detail == SUMMARY


async def test_her_line_fills_only_a_notice_not_yet_delivered(
    store: SqliteScheduleStore,
) -> None:
    n = await store.attention_notice(OWNER, "action:x", title=TITLE, text=None, now_ms=1)
    assert await store.fill_notice_text(n.id, "First.")
    assert not await store.fill_notice_text(n.id, "Second.")  # it has words already
    m = await store.attention_notice(OWNER, "action:y", title=TITLE, text=None, now_ms=1)
    assert await store.mark_delivered(m.id, "page", 2)
    assert not await store.fill_notice_text(m.id, "Too late.")
    kept = await store.notice(m.id)
    assert kept is not None and kept.text is None


def test_the_record_says_plainly_it_never_went_and_names_its_id() -> None:
    # She finds it here when the user says yes: its id, and that nothing went out.
    decisions = [
        DecisionRow(pending_id="pa1", event="approved", decided_by="s1", via="tap", at=1),
        DecisionRow(
            pending_id="pa1", event="recovered:not_sent", decided_by="system", via="text", at=2
        ),
    ]
    said = describe(action(), None, decisions, now_ms=3)
    assert said.startswith("pa1: ")
    assert "had changed" not in said
    assert "not sent" in said and "interrupted before it went out" in said


def test_any_other_invalidated_action_reads_as_before() -> None:
    said = describe(action(), None, [], now_ms=3)
    assert "cancelled because what it would change had changed" in said


# ── The runtime checks again every so often, and tells ──────────────────


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
        env={"TZ": "UTC"},
    )
    assert runtime.door is not None
    runtime.door.notify = lambda title, body, *, silent=False: True  # never shown for real
    yield runtime
    await runtime.close()


async def test_the_runtime_checks_again_and_tells_then_stops(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = 0

    async def check() -> list[Settled]:
        nonlocal checks
        checks += 1
        return [Settled(id="pa1", outcome="unknown")]

    offered: list[Notice] = []

    async def added(n: Notice) -> None:
        offered.append(n)

    assert rt.door is not None and rt.schedules is not None
    monkeypatch.setattr(rt.router, "check_unchecked", check)
    monkeypatch.setattr(rt.spine_store, "get_pending", get_pending)
    monkeypatch.setattr(rt.door, "added", added)
    rt.provider = None
    rt.check_every_s = 0.01
    rt.start_background()
    for _ in range(200):
        if checks >= 2:
            break
        await asyncio.sleep(0.01)
    await rt.stop_background()
    assert checks >= 2
    # Told once, however many checks returned it, and offered to the door.
    (n,) = await rt.schedules.held(OWNER)
    assert n.title == TITLE and [o.id for o in offered] == [n.id]
    stopped = checks
    await asyncio.sleep(0.05)
    assert checks == stopped


async def test_what_startup_settled_is_told_in_the_background(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert rt.schedules is not None
    monkeypatch.setattr(rt.spine_store, "get_pending", get_pending)
    rt.provider = None
    rt.tell_soon([Settled(id="x", outcome="succeeded")])
    assert not rt._interrupted  # nothing in doubt: no task at all
    rt.tell_soon([Settled(id="pa1", outcome="not_sent")])
    await asyncio.gather(*rt._interrupted)
    (n,) = await rt.schedules.held(OWNER)
    assert n.title == TITLE and n.text is None


async def test_startup_tells_what_is_still_in_doubt_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A notice lost to a quit before it was written: the next start finds the action
    # still in doubt and tells it, and the start after that does not tell it again.
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    asked: list[int] = []

    async def in_doubt(self: Router, since_ms: int) -> list[Settled]:
        asked.append(since_ms)
        return [Settled(id="pa1", outcome="unknown")]

    async def pending(self: SqliteSpineStore, pid: str) -> PendingAction | None:
        return ACTIONS.get(pid)

    monkeypatch.setattr(Router, "in_doubt", in_doubt)
    monkeypatch.setattr(SqliteSpineStore, "get_pending", pending)
    for _ in range(2):
        runtime = await open_runtime(
            share_rules=lambda _p: None,
            granted_roots=(granted,),
            persona=tmp_path,
            secrets=MemoryStore(),
            env={"TZ": "UTC"},
        )
        try:
            assert runtime.door is not None and runtime.schedules is not None
            runtime.door.notify = lambda title, body, *, silent=False: True
            runtime.provider = None
            await asyncio.gather(*runtime._interrupted)
            (n,) = await runtime.schedules.held(OWNER)
            assert n.source_id == "action:pa1"
        finally:
            await runtime.close()
    assert len(asked) == 2
