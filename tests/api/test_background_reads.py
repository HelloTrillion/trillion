"""A slow read runs in the background (the background reads spec): her turn ends with
her acknowledgement, the user keeps talking, and at the next pause she brings the
result in from a host note."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_heard import Halting, asked_by_voice
from tests.api.test_listening import hearing
from tests.api.test_session import Page, idle
from tests.api.test_session_notices import Shown, voiced
from tests.api.test_widget_ask import tool_results
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.conversations import create_conversations_router
from trillion.api.jobs import STARTED
from trillion.api.runtime import OWNER, Runtime, open_runtime
from trillion.api.session import BACKGROUND, Session, _Finished, keep_for_later
from trillion.brain.types import ContextBlock, Msg, TextBlock, ToolResultBlock
from trillion.delivery import door as delivery
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

READ = "mcp_stripe_charges"
REMIND = {"when": "2030-01-07T10:00", "say": "Pay the bill.", "title": "Bill"}
KPIS = {
    "type": "kpis",
    "title": "Revenue",
    "source": "Stripe",
    "metrics": [{"label": "MRR", "value": "$4,200"}, {"label": "New", "value": "12"}],
}


class SlowRead(BaseTool):
    """A connected app's read that waits until the test lets it finish."""

    connector_title = "Stripe"

    def __init__(
        self,
        name: str = READ,
        risk: Risk = Risk.READ,
        timeout_s: float = 5.0,
        category: str = "mcp",
    ) -> None:
        self.definition = ToolDefinition(
            name=name,
            description="Read the charges.",
            input_schema={
                "type": "object",
                "properties": {"period": {"type": "string"}, "n": {"type": "integer"}},
                "additionalProperties": False,
            },
            risk=risk,
            category=category,
            timeout_s=timeout_s,
        )
        self.gate = asyncio.Event()
        self.contexts: list[ToolContext] = []
        self.fail = False

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text="Read the charges")

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.contexts.append(ctx)
        await self.gate.wait()
        if self.fail:
            raise RuntimeError("the server went away")
        return ToolResult(ok=True, content="Revenue $4,200", effect="confirmed")


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
    runtime.door.notify = Shown()  # never the real notification in a test
    yield runtime
    await runtime.close()


def slow(rt: Runtime, **kw: Any) -> SlowRead:
    tool = SlowRead(**kw)
    rt.registry.register(tool, module="tests.api")
    return tool


async def until(cond: Callable[[], bool], what: str = "it") -> None:
    for _ in range(400):
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never saw {what}")


async def started(rt: Runtime, *after: list[Any]) -> tuple[Session, Page, ScriptedProvider]:
    """She starts the read and says her one line; the turn ends with the job running."""
    provider = ScriptedProvider(
        use(READ, {"period": "year"}, "r1", text="Pulling it now."), say("On its way."), *after
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "pull my Stripe numbers"})
    await idle(session)
    return session, page, provider


def notes(history: list[tuple[str, Msg]]) -> list[tuple[str, Msg]]:
    return [
        (k, m)
        for k, m in history
        if any(isinstance(b, ContextBlock) and b.text == BACKGROUND for b in m.blocks)
    ]


def text_of(msg: Msg) -> str:
    return "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))


# ── The call ─────────────────────────────────────────────────────────────


async def test_the_call_is_answered_at_once_and_the_session_is_free(rt: Runtime) -> None:
    tool = slow(rt)
    session, page, provider = await started(rt, say("It is noon."))
    (end,) = page.all("tool.end")
    assert end["summary"] == "background" and end["ok"] is True
    # The tool use is paired at once, with a job id, and is not an error.
    (result,) = [
        b for m in provider.calls[1][1] for b in m.blocks if isinstance(b, ToolResultBlock)
    ]
    body = json.loads(result.content)
    assert body["status"] == "started" and body["detail"] == STARTED and body["job"]
    assert result.is_error is False
    assert page.all("jobs")[-1] == {"type": "jobs", "count": 1}
    # The user keeps talking while it runs.
    await session.handle({"type": "turn.text", "text": "what time is it"})
    await idle(session)
    assert page.all("error") == []
    assert [m["text"] for m in page.all("reply.delta")][-1] == "It is noon."
    # Her context says what is running.
    context = provider.calls[-1][1][-1].blocks[0]
    assert isinstance(context, ContextBlock)
    assert f"background: {READ} (job {body['job']}) running" in context.text
    assert len(tool.contexts) == 1 and tool.contexts[0].turn == 1
    await session.close()


async def test_a_duplicate_is_refused_and_three_is_the_most(rt: Runtime) -> None:
    tool = slow(rt)
    provider = ScriptedProvider(
        use(READ, {"n": 1}, "r1"),
        use(READ, {"n": 1}, "r2"),
        use(READ, {"n": 2}, "r3"),
        use(READ, {"n": 3}, "r4"),
        use(READ, {"n": 4}, "r5"),
        say("Pulling them."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "pull them all"})
    # The fourth runs in the turn, which waits for it.
    await until(lambda: len(tool.contexts) == 4, "the fourth read in the turn")
    ends = page.all("tool.end")
    assert [e["summary"] for e in ends] == ["background", "refused", "background", "background"]
    answers = [
        b.content for m in provider.calls[-1][1] for b in m.blocks if isinstance(b, ToolResultBlock)
    ]
    refused = json.loads(answers[1])
    first = json.loads(answers[0])["job"]
    assert refused["detail"] == f"already running as job {first}"
    assert session._task is not None  # the fourth is inline
    tool.gate.set()
    await session.close()


@pytest.mark.parametrize(("risk", "category"), [(Risk.IRREVERSIBLE, "mcp"), (Risk.READ, "memory")])
async def test_a_gated_read_or_her_own_tool_stays_in_the_turn(
    rt: Runtime, risk: Risk, category: str
) -> None:
    tool = slow(rt, risk=risk, category=category)
    tool.gate.set()
    provider = ScriptedProvider(use(READ, {}, "r1"), say("Done."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "read it"})
    await idle(session)
    assert page.all("tool.end")[0]["summary"] != "background"
    assert page.all("jobs") == []
    await session.close()


# ── The result and the follow-up ─────────────────────────────────────────


async def test_the_result_is_a_host_note_and_she_brings_it_in(rt: Runtime) -> None:
    tool = slow(rt)
    session, page, provider = await started(rt, say("Your Stripe numbers are in."))
    tool.gate.set()
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    history = await rt.conversations.history(session.id, OWNER)
    ((kind, note),) = notes(history)
    assert kind == "tap"  # never the user's words
    said = text_of(note)
    assert said.startswith("[host] Stripe results are in. The background read you started")
    assert f"({READ}, job " in said and "Revenue $4,200" in said
    assert "<untrusted_tool_result" in said and "at most two sentences" in said
    assert history[-1][0] == "assistant"
    assert page.all("jobs")[-1]["count"] == 0
    # On reload: a small host line, and nothing runs again.
    again_page = Page(rt)
    await session.close()
    again = await Session.start(rt, again_page.send, session.id)
    shown = again_page.all("history")[-1]["messages"]
    assert {"role": "host", "text": "Stripe results are in"} in shown
    assert not any("[host]" in m["text"] for m in shown)
    await idle(again)
    assert len(provider.calls) == 3
    await again.close()


async def test_the_follow_up_counts_as_read_and_can_approve_nothing(rt: Runtime) -> None:
    tool = slow(rt)
    path = str(rt.granted_roots[0] / "a.txt")
    provider = ScriptedProvider(
        use("file_create", {"path": path, "content": "x"}, "c1"),
        say("Shall I?"),
        use(READ, {}, "r1"),
        say("Pulling it."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a"})
    await idle(session)
    card = page.all("confirm.request")[0]["pending_id"]
    await session.handle({"type": "turn.text", "text": "and pull my numbers"})
    await idle(session)
    provider._replies += [
        use("confirm_pending_action", {"pending_id": card}, "c2"),
        use("reminder_set", REMIND, "c3"),
        say("In."),
    ]
    tool.gate.set()
    await until(lambda: len(provider.calls) == 7, "the follow-up")
    await idle(session)
    confirm = page.all("tool.end")[-2]
    assert confirm["summary"] == "ask"  # never a yes
    assert "from_reading" in tool_results(provider)[-1]
    assert not (rt.granted_roots[0] / "a.txt").exists()
    assert rt.schedules is not None and not await rt.schedules.active(OWNER)
    await session.close()


async def test_it_waits_while_listening(rt: Runtime) -> None:
    tool = slow(rt)
    session, _, provider = await started(rt, say("In."))
    hearing(rt)
    rt.provider = provider
    await session.handle({"type": "listen.start"})
    assert session._listener is not None
    tool.gate.set()
    await until(lambda: rt.jobs.running(session.id) == [], "the job's end")
    await asyncio.sleep(0.02)
    assert len(provider.calls) == 2 and session._task is None
    # Stopping the microphone is a pause: now it comes.
    await session.handle({"type": "interrupt"})
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    await session.close()


async def test_it_waits_while_her_reply_is_still_heard(rt: Runtime) -> None:
    voiced(rt)
    tool = slow(rt)
    session, _, provider = await started(rt, say("In."))
    tool.gate.set()
    await until(lambda: rt.jobs.running(session.id) == [], "the job's end")
    await asyncio.sleep(0.02)
    assert len(provider.calls) == 2  # turn 1 is still playing
    await session.handle({"type": "playback.ended", "turn_id": 1})
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    await session.close()


async def test_a_stop_or_new_message_never_cancels_the_read(rt: Runtime) -> None:
    tool = slow(rt)
    session, _, provider = await started(rt, say("Sure."), say("In."))
    await session.handle({"type": "interrupt"})
    await session.handle({"type": "turn.text", "text": "something else"})
    await idle(session)
    assert len(rt.jobs.running(session.id)) == 1
    tool.gate.set()
    await until(lambda: len(provider.calls) == 4, "the follow-up")
    await idle(session)
    await session.close()


async def test_a_failure_comes_back_as_a_note(rt: Runtime) -> None:
    tool = slow(rt)
    tool.fail = True
    session, _, provider = await started(rt, say("It did not come back."))
    tool.gate.set()
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    ((_, note),) = notes(await rt.conversations.history(session.id, OWNER))
    said = text_of(note)
    assert "did not come back" in said and "offer to try again" in said
    assert "RuntimeError" in said
    await session.close()


async def test_a_timeout_comes_back_as_a_note(rt: Runtime) -> None:
    slow(rt, timeout_s=0.05)  # never let go
    session, _, provider = await started(rt, say("It did not come back."))
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    ((_, note),) = notes(await rt.conversations.history(session.id, OWNER))
    said = text_of(note)
    assert "did not come back" in said and "timeout" in said
    await session.close()


async def test_a_widget_built_in_the_follow_up_is_live(rt: Runtime) -> None:
    tool = slow(rt)
    session, _, provider = await started(
        rt, use("workspace_show", {"title": "Money", "widgets": [KPIS]}, "w1"), say("In.")
    )
    tool.gate.set()
    await until(lambda: len(provider.calls) == 4, "the follow-up")
    await idle(session)
    board = await rt.board_store.get(session.id, OWNER)
    assert board is not None
    assert board["widgets"][0]["live"]["reads"] == [{"tool": READ, "args": {"period": "year"}}]
    await session.close()


# ── No page on that conversation ─────────────────────────────────────────


async def test_with_no_page_a_ready_notice_and_the_follow_up_on_open(rt: Runtime) -> None:
    tool = slow(rt)
    first, _, provider = await started(rt, say("Your Stripe numbers are in."))
    cid = first.id
    await first.close()
    other_page = Page(rt)
    other = await Session.start(rt, other_page.send, None)
    tool.gate.set()
    await other_page.until("notice")
    (ready,) = other_page.all("notice")
    assert ready["kind"] == "ready" and ready["conversation_id"] == cid
    assert ready["title"] == "Stripe results are in"
    assert len(provider.calls) == 2  # nothing runs over there
    assert notes(await rt.conversations.history(cid, OWNER))
    await other.close()
    # Opening it runs the follow-up there.
    page = Page(rt)
    back = await Session.start(rt, page.send, cid)
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(back)
    assert (await rt.conversations.history(cid, OWNER))[-1][0] == "assistant"
    await back.close()


async def test_with_no_page_at_all_the_computer_is_told(rt: Runtime) -> None:
    tool = slow(rt)
    first, _, _ = await started(rt)
    await first.close()
    tool.gate.set()
    assert rt.door is not None and isinstance(rt.door.notify, Shown)
    shown = rt.door.notify
    await until(lambda: bool(shown.calls), "a notification")
    assert shown.calls[0][0] == "Stripe results are in"


# ── Ending ───────────────────────────────────────────────────────────────


async def test_deleting_the_conversation_cancels_its_reads(rt: Runtime) -> None:
    slow(rt)
    session, _, _ = await started(rt)
    (job,) = rt.jobs.running(session.id)
    router = create_conversations_router(rt)
    delete = next(r for r in router.routes if getattr(r, "methods", None) == {"DELETE"})
    await delete.endpoint(session.id)  # type: ignore[attr-defined]
    assert rt.jobs.running(session.id) == []
    assert job.task is not None and job.task.cancelled()


async def test_closing_the_runtime_cancels_every_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    runtime = await open_runtime(persona=tmp_path, secrets=MemoryStore(), env={"TZ": "UTC"})
    slow(runtime)
    session, _, _ = await started(runtime)
    (job,) = runtime.jobs.running(session.id)
    await session.close()
    await runtime.close()
    assert job.task is not None and job.task.cancelled()
    assert runtime.jobs.running(session.id) == []


# ── Tracked to the end, and no note lost ─────────────────────────────────


async def test_a_delete_while_its_note_is_written_writes_nothing(rt: Runtime) -> None:
    tool = slow(rt)
    first, _, provider = await started(rt)
    cid = first.id
    await first.close()
    writing, never = asyncio.Event(), asyncio.Event()
    real = rt.conversations.exists

    async def exists(conversation_id: str, owner: str) -> bool:
        if conversation_id == cid:
            writing.set()
            await never.wait()
        return await real(conversation_id, owner)

    rt.conversations.exists = exists  # type: ignore[method-assign]
    (job,) = rt.jobs.running(cid)
    tool.gate.set()
    await writing.wait()
    assert rt.jobs.running(cid) == []  # off the list, still delivering
    router = create_conversations_router(rt)
    delete = next(r for r in router.routes if getattr(r, "methods", None) == {"DELETE"})
    await delete.endpoint(cid)  # type: ignore[attr-defined]
    assert job.task is not None and job.task.cancelled()  # its delivery too
    never.set()
    await asyncio.sleep(0.02)
    assert notes(await rt.conversations.history(cid, OWNER)) == []
    assert rt.schedules is not None and await rt.schedules.held(OWNER) == []
    assert cid not in rt.routine_holds
    assert len(provider.calls) == 2


async def test_a_page_that_opens_while_the_note_is_written_brings_it_in(rt: Runtime) -> None:
    tool = slow(rt)
    first, _, provider = await started(rt, say("Your Stripe numbers are in."))
    cid = first.id
    await first.close()
    page = Page(rt)
    opened: list[Session] = []
    real = rt.conversations.append

    async def append(conversation_id: str, owner: str, kind: Any, msg: Msg, at: int) -> int:
        if conversation_id == cid and notes([(kind, msg)]) and not opened:
            # The page reads the history just before the note lands.
            opened.append(await Session.start(rt, page.send, cid))
        return await real(conversation_id, owner, kind, msg, at)

    rt.conversations.append = append  # type: ignore[method-assign]
    tool.gate.set()
    await until(lambda: len(provider.calls) == 3, "the follow-up on the page that opened")
    await idle(opened[0])
    assert page.all("notice") == []  # it shows the note; no ready line as well
    assert rt.schedules is not None and await rt.schedules.held(OWNER) == []
    assert rt.door is not None and isinstance(rt.door.notify, Shown)
    assert rt.door.notify.calls == []
    await opened[0].close()


async def test_a_routine_cannot_start_while_the_note_is_written(rt: Runtime) -> None:
    tool = slow(rt)
    first, _, _ = await started(rt)
    cid = first.id
    await first.close()
    held: list[bool] = []
    real = rt.conversations.append

    async def append(conversation_id: str, owner: str, kind: Any, msg: Msg, at: int) -> int:
        if conversation_id == cid:
            # What a routine's build checks before it takes the conversation.
            held.append(cid in rt.routine_holds)
        return await real(conversation_id, owner, kind, msg, at)

    rt.conversations.append = append  # type: ignore[method-assign]
    tool.gate.set()
    await until(lambda: bool(held), "the note")
    await until(lambda: cid not in rt.routine_holds, "the hold let go")
    assert held == [True]


async def test_a_stop_before_the_note_is_saved_loses_nothing(rt: Runtime) -> None:
    tool = slow(rt)
    session, _, provider = await started(rt, say("In."))
    real = session._build_input
    building, never = asyncio.Event(), asyncio.Event()
    calls = 0

    async def build(*a: Any, **kw: Any) -> Any:
        nonlocal calls
        calls += 1
        if kw.get("marker") == BACKGROUND and calls == 1:
            building.set()
            await never.wait()
        return await real(*a, **kw)

    session._build_input = build  # type: ignore[method-assign]
    tool.gate.set()
    await building.wait()
    await session.handle({"type": "interrupt"})
    # They asked for quiet: nothing starts at the rest that follows the Stop.
    await asyncio.sleep(0.05)
    assert len(provider.calls) == 2 and session._task is None
    # At the next pause it comes, and the note is saved once.
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    assert len(notes(await rt.conversations.history(session.id, OWNER))) == 1
    await session.close()


async def test_the_users_own_message_answers_a_saved_note(rt: Runtime) -> None:
    tool = slow(rt)
    first, _, provider = await started(rt, say("Here they are, and good morning."))
    cid = first.id
    await first.close()
    tool.gate.set()
    await until(lambda: rt.jobs.running(cid) == [], "the job's end")
    await until(lambda: bool(rt.door and rt.door.notify.calls), "the note kept")  # type: ignore[attr-defined]
    rt.provider = None  # no model yet: nothing runs on open
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    rt.provider = provider
    await session.handle({"type": "turn.text", "text": "morning, anything new?"})
    await idle(session)
    await asyncio.sleep(0.02)
    # She answered it in the user's turn: no empty turn of its own after it.
    assert len(provider.calls) == 3 and session._task is None
    await session.close()


# ── Voice ────────────────────────────────────────────────────────────────


async def spoken_read(rt: Runtime, provider: ScriptedProvider) -> tuple[Session, Page, SlowRead]:
    """A read asked for out loud; it ends while she is still heard, and the microphone
    that opened after her is closed again, which is the pause."""
    hearing(rt)
    voiced(rt)
    rt.think_early = False  # no draft takes a scripted reply
    tool = slow(rt)
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await until(lambda: len(provider.calls) == 2 and session._task is None, "the spoken turn")
    tool.gate.set()
    await until(lambda: rt.jobs.running(session.id) == [], "the job's end")
    first = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": first})
    assert session._listener is not None  # her spoken turn's follow-up window
    await session.handle({"type": "interrupt"})
    return session, page, tool


async def test_after_a_spoken_turn_the_microphone_comes_back_after_the_follow_up(
    rt: Runtime,
) -> None:
    provider = ScriptedProvider(
        use(READ, {}, "r1"), say("Pulling it."), say("Your Stripe numbers are in.")
    )
    session, page, _ = await spoken_read(rt, provider)
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    opened = len(page.all("mic.start"))
    turn = page.all("reply.sentence")[-1]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})
    assert len(page.all("mic.start")) == opened + 1
    await session.close()


async def test_a_barge_over_a_spoken_follow_up_keeps_what_was_heard(rt: Runtime) -> None:
    provider = Halting(use(READ, {}, "r1"), say("Pulling it."))
    session, page, _ = await spoken_read(rt, provider)
    before = len(page.all("reply.sentence"))
    await page.until("reply.sentence", before + 2)
    turn = page.all("reply.sentence")[-1]["turn"]
    await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": 1})
    await session.handle({"type": "barge"})
    (stopped,) = page.all("interrupted")
    assert stopped["kept"] == "First thing.…"
    await session.close()


async def test_a_page_that_never_says_it_played_still_gets_the_follow_up(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(delivery, "PLAYBACK_GRACE_MS", 300)
    voiced(rt)
    tool = slow(rt)
    session, _, provider = await started(rt, say("In."))
    tool.gate.set()
    await until(lambda: rt.jobs.running(session.id) == [], "the job's end")
    await asyncio.sleep(0.02)
    assert len(provider.calls) == 2  # still being heard, as far as anyone knows
    # No playback.ended ever comes: once the grace is over, it comes anyway.
    await until(lambda: len(provider.calls) == 3, "the follow-up after the grace")
    await idle(session)
    await session.close()


# ── Closing with a note waiting ──────────────────────────────────────────


async def test_a_page_closed_before_the_pause_keeps_the_note_and_tells(rt: Runtime) -> None:
    voiced(rt)  # her reply is still heard: the note waits
    tool = slow(rt)
    session, _, provider = await started(rt)
    tool.gate.set()
    await until(lambda: bool(session._finished), "the note waiting")
    await session.close()
    assert notes(await rt.conversations.history(session.id, OWNER))
    assert rt.door is not None and isinstance(rt.door.notify, Shown)
    assert rt.door.notify.calls[0][0] == "Stripe results are in"
    assert len(provider.calls) == 2


async def test_a_page_replaced_before_the_pause_hands_the_note_on(rt: Runtime) -> None:
    voiced(rt)
    tool = slow(rt)
    session, _, provider = await started(rt, say("Your Stripe numbers are in."))
    tool.gate.set()
    await until(lambda: bool(session._finished), "the note waiting")
    page = Page(rt)
    again = await Session.start(rt, page.send, session.id)
    await until(lambda: len(provider.calls) == 3, "the follow-up on the new page")
    await idle(again)
    assert rt.door is not None and isinstance(rt.door.notify, Shown)
    assert rt.door.notify.calls == [] and page.all("notice") == []
    assert len(notes(await rt.conversations.history(session.id, OWNER))) == 1
    await again.close()


async def test_it_waits_while_a_routine_holds_the_conversation(rt: Runtime) -> None:
    tool = slow(rt)
    session, _, provider = await started(rt, say("In."))
    rt.routine_holds.add(session.id)
    tool.gate.set()
    await until(lambda: bool(session._finished), "the note waiting")
    await asyncio.sleep(0.02)
    assert len(provider.calls) == 2
    rt.routine_holds.discard(session.id)
    await rt.routine_ran(session.id)  # what a routine does when it is done
    await until(lambda: len(provider.calls) == 3, "the follow-up")
    await idle(session)
    await session.close()


# ── A close that always finishes, and saves that never repeat ──────────────


async def two_waiting(rt: Runtime, *after: list[Any]) -> tuple[Session, Page, ScriptedProvider]:
    """Two reads end together while her reply is still heard: both notes wait."""
    voiced(rt)
    tool = slow(rt)
    provider = ScriptedProvider(
        use(READ, {"n": 1}, "r1"), use(READ, {"n": 2}, "r2"), say("Pulling both."), *after
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "pull both"})
    await idle(session)
    tool.gate.set()
    await until(lambda: len(session._finished) == 2, "both notes waiting")
    return session, page, provider


async def test_a_close_whose_save_fails_still_finishes(rt: Runtime) -> None:
    session, _, _ = await two_waiting(rt)

    async def broken(*a: Any, **kw: Any) -> int:
        raise RuntimeError("disk full")

    rt.conversations.append = broken  # type: ignore[method-assign]
    dropped: list[bool] = []

    async def drop() -> None:
        dropped.append(True)

    session._drop_draft = drop  # type: ignore[method-assign]
    naming = asyncio.ensure_future(asyncio.Event().wait())
    session._naming = naming  # type: ignore[assignment]
    await session.close()  # never raises
    assert dropped and naming.cancelled()
    assert session.id not in rt.by_conversation and rt.routine_holds == set()


async def test_a_save_that_fails_part_way_saves_nothing_twice(rt: Runtime) -> None:
    session, page, provider = await two_waiting(rt, say("Both are in."))
    real = session._save
    failed: list[bool] = []
    seen = 0

    async def save(kind: Any, msg: Msg) -> None:
        nonlocal seen
        seen += bool(notes([(kind, msg)]))
        if seen == 2 and not failed:
            # The first note is saved; the second is not.
            failed.append(True)
            raise RuntimeError("disk hiccup")
        await real(kind, msg)

    session._save = save  # type: ignore[method-assign]
    await session.handle({"type": "playback.ended", "turn_id": 1})
    await until(lambda: bool(failed), "the failed save")
    # It still comes to rest, with nothing of the follow-up left set.
    await until(lambda: session._task is None and page.all("state")[-1]["orb"] == "idle", "rest")
    assert len(session._finished) == 1 and not session._listen_after and not session._aloud
    # At the next pause the one left is saved, once, and she brings both in.
    await until(lambda: len(provider.calls) == 4, "the follow-up")
    await idle(session)
    saved = notes(await rt.conversations.history(session.id, OWNER))
    assert len(saved) == 2 and text_of(saved[0][1]) != text_of(saved[1][1])
    await session.close()


async def test_a_conversation_deleted_part_way_gets_nothing_more(rt: Runtime) -> None:
    session, _, provider = await two_waiting(rt, say("unreached"))
    real = session._save

    async def save(kind: Any, msg: Msg) -> None:
        await real(kind, msg)
        if notes([(kind, msg)]):
            # Deleted elsewhere just after the first note landed.
            await rt.conversations.delete(session.id, OWNER, rt.clock())

    session._save = save  # type: ignore[method-assign]
    await session.handle({"type": "playback.ended", "turn_id": 1})
    await until(lambda: not session._finished, "the notes dropped")
    await asyncio.sleep(0.05)
    assert len(notes(await rt.conversations.history(session.id, OWNER))) == 1
    assert len(provider.calls) == 3  # no turn on a deleted conversation
    await session.close()


async def test_keeping_for_later_stops_when_the_conversation_is_deleted(rt: Runtime) -> None:
    cid = "c-gone"
    await rt.conversations.create(cid, OWNER, rt.clock())
    real = rt.conversations.append

    async def append(conversation_id: str, owner: str, kind: Any, msg: Msg, at: int) -> int:
        n = await real(conversation_id, owner, kind, msg, at)
        # Deleted elsewhere just after the first note landed.
        await rt.conversations.delete(cid, OWNER, rt.clock())
        return n

    rt.conversations.append = append  # type: ignore[method-assign]
    two = [
        _Finished(
            note=f"[host] Stripe results are in. The background read you started ({READ}, "
            f"job j{n}) is in. Result: {{}}",
            read=None,
            title="Stripe results are in",
            job_id=f"j{n}",
        )
        for n in (1, 2)
    ]
    await keep_for_later(rt, cid, two)
    assert len(notes(await rt.conversations.history(cid, OWNER))) == 1
    assert rt.schedules is not None and await rt.schedules.held(OWNER) == []
    assert cid not in rt.routine_holds


async def test_the_turn_bringing_a_read_in_may_not_stay_quiet(rt: Runtime) -> None:
    """A background note's turn is the host's (VOI-7): she answers it, never silence."""
    tool = slow(rt)
    session, page, provider = await started(
        rt, use("stay_quiet", {"kind": "done"}, "q1"), say("Your Stripe numbers are in.")
    )
    tool.gate.set()
    await until(lambda: len(provider.calls) == 4, "her answer after the refusal")
    await idle(session)
    (result,) = [
        b
        for m in provider.calls[-1][1]
        for b in m.blocks
        if isinstance(b, ToolResultBlock) and b.tool_use_id == "q1"
    ]
    assert result.is_error and "note from the app" in result.content
    assert page.all("turn.complete")[-1]["stop"] == "end"
    assert page.all("seen") == []
    await session.close()
