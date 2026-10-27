"""The background reads registry (the background reads spec): what goes to the
background, at most three a conversation, no duplicate, and every job cancellable."""

from __future__ import annotations

import asyncio

import pytest

from trillion.api.jobs import LIMIT, Full, Jobs, goes_to_background
from trillion.spine.contract import Risk, ToolDefinition
from trillion.spine.router import RouterResponse


def definition(name: str, risk: Risk = Risk.READ, category: str = "mcp") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="A tool.",
        input_schema={"type": "object"},
        risk=risk,
        category=category,
        timeout_s=5.0,
    )


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("mcp_stripe_list_charges", "mcp"),
        ("stripe_revenue", "mcp"),
        ("gmail_search", "google"),
        ("calendar_events", "google"),
        ("slack_read", "slack"),
        ("web_fetch", "web"),
    ],
)
def test_a_read_from_outside_goes_to_the_background(name: str, category: str) -> None:
    assert goes_to_background(definition(name, category=category))


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("memory_recall", "memory"),
        ("workspace_show", "workspace"),
        ("schedule_list", "schedule"),
        ("rule_list", "rules"),
        ("day_open", "day"),
        ("routine_nothing", "routine"),
        ("file_list", "files"),
        # A file is on this computer and quick: read in the turn.
        ("file_read", "files"),
    ],
)
def test_her_own_quick_tools_stay_in_the_turn(name: str, category: str) -> None:
    assert not goes_to_background(definition(name, category=category))


@pytest.mark.parametrize("risk", [Risk.WRITE, Risk.IRREVERSIBLE, Risk.SPEND])
def test_a_write_or_a_gated_read_stays_in_the_turn(risk: Risk) -> None:
    # A connected read left asking first is registered irreversible: it has a card.
    assert not goes_to_background(definition("mcp_notes_read", risk=risk))
    assert not goes_to_background(definition("gmail_send", risk=risk, category="google"))


def test_nothing_unknown_goes() -> None:
    assert not goes_to_background(None)


async def held(gate: asyncio.Event) -> RouterResponse:
    await gate.wait()
    return RouterResponse(status="succeeded", content="ok")


async def test_three_at_most_per_conversation_and_a_duplicate_is_found() -> None:
    told: list[tuple[str, int]] = []
    ended: list[str] = []

    async def changed(cid: str, count: int) -> None:
        told.append((cid, count))

    async def then(job: object, r: RouterResponse) -> None:
        ended.append(r.status)

    jobs = Jobs(clock=lambda: 1000, changed=changed)
    gate = asyncio.Event()
    started = [
        await jobs.start("c1", "mcp_a", {"n": n}, lambda: held(gate), then) for n in range(LIMIT)
    ]
    assert len({j.id for j in started}) == LIMIT
    with pytest.raises(Full):
        await jobs.start("c1", "mcp_a", {"n": 9}, lambda: held(gate), then)
    # Another conversation has its own three.
    other = await jobs.start("c2", "mcp_a", {"n": 0}, lambda: held(gate), then)
    assert jobs.same("c1", "mcp_a", {"n": 1}) is started[1]
    assert jobs.same("c1", "mcp_a", {"n": 7}) is None
    assert jobs.same("c1", "mcp_b", {"n": 1}) is None
    assert jobs.same("c2", "mcp_a", {"n": 0}) is other
    assert [c for cid, c in told if cid == "c1"] == [1, 2, 3]

    gate.set()
    for _ in range(50):
        await asyncio.sleep(0)
    assert ended == ["succeeded"] * 4
    assert jobs.running("c1") == [] and jobs.running("c2") == []
    assert told[-1][1] == 0


async def test_cancelling_a_conversation_leaves_the_others() -> None:
    ended: list[str] = []

    async def then(job: object, r: RouterResponse) -> None:
        ended.append(r.status)

    jobs = Jobs(clock=lambda: 0)
    gate = asyncio.Event()
    a = await jobs.start("c1", "mcp_a", {}, lambda: held(gate), then)
    await jobs.start("c2", "mcp_a", {}, lambda: held(gate), then)
    await jobs.cancel("c1")
    assert a.task is not None and a.task.cancelled()
    assert jobs.running("c1") == [] and len(jobs.running("c2")) == 1
    await jobs.stop()
    assert jobs.running("c2") == []
    gate.set()
    await asyncio.sleep(0)
    assert ended == []  # nothing cancelled ever reports


async def test_a_failing_then_never_breaks_the_registry() -> None:
    async def then(job: object, r: RouterResponse) -> None:
        raise RuntimeError("boom")

    async def fine() -> RouterResponse:
        return RouterResponse(status="succeeded")

    jobs = Jobs(clock=lambda: 0)
    job = await jobs.start("c1", "mcp_a", {}, fine, then)
    assert job.task is not None
    await job.task
    assert jobs.running("c1") == []


def test_what_a_job_left_for_a_conversation_is_taken_once() -> None:
    jobs = Jobs(clock=lambda: 0)
    jobs.keep_read("c1", "mcp_a", {"x": 1})
    jobs.keep_notice("c1", "n1")
    assert jobs.take("c1") == ([("mcp_a", {"x": 1})], ["n1"])
    assert jobs.take("c1") == ([], [])


async def test_a_delivery_still_running_is_cancelled_with_its_conversation() -> None:
    delivering = asyncio.Event()
    finished: list[str] = []

    async def then(job: object, r: RouterResponse) -> None:
        delivering.set()
        await asyncio.Event().wait()  # writing its note, for ever
        finished.append("written")

    async def fine() -> RouterResponse:
        return RouterResponse(status="succeeded")

    jobs = Jobs(clock=lambda: 0)
    job = await jobs.start("c1", "mcp_a", {}, fine, then)
    await delivering.wait()
    assert jobs.running("c1") == []  # off the list, but not done
    await jobs.cancel("c1")
    assert job.task is not None and job.task.cancelled() and finished == []


async def test_stopping_cancels_tracked_work_too() -> None:
    async def forever() -> None:
        await asyncio.Event().wait()

    jobs = Jobs(clock=lambda: 0)
    task = jobs.track("c1", forever())
    await jobs.stop()
    assert task.cancelled()
