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

from tests.api.test_listening import hearing
from tests.api.test_session import Page, idle
from tests.api.test_session_notices import Shown, voiced
from tests.api.test_widget_ask import tool_results
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.conversations import create_conversations_router
from trillion.api.jobs import STARTED
from trillion.api.runtime import OWNER, Runtime, open_runtime
from trillion.api.session import BACKGROUND, Session
from trillion.brain.types import ContextBlock, Msg, TextBlock, ToolResultBlock
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
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={"TZ": "UTC"}
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
