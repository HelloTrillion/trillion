"""#3: an approved action is seen through to a recorded outcome, and a timeout can stop
waiting on a stuck disk.

Cancelling the task that asked (a barge-in, a closed tab) used to leave the action
in `executing` until the next start, answering every later confirm with
`in_progress`. And a file tool doing blocking I/O on the event loop could not be
timed out at all, because the timeout itself waits on the loop.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.spine.conftest import Spine
from trillion.spine.confirm import Status
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.tools.files import _OffLoop


class Gated(BaseTool):
    """An irreversible action that waits for the test to let it finish."""

    definition = ToolDefinition(
        name="gated",
        description="Wait, then do something irreversible (test).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.IRREVERSIBLE,
        category="test",
        timeout_s=0.5,
    )

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.executions = 0

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text="Do the gated thing")

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.executions += 1
        self.started.set()
        await self.release.wait()
        return ToolResult(ok=True, content="done", effect="confirmed")


class StuckDisk(_OffLoop):
    """A write whose disk call blocks for longer than the tool may take."""

    definition = ToolDefinition(
        name="stuck_disk",
        description="Write to a disk that does not answer (test).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.IRREVERSIBLE,
        category="test",
        timeout_s=0.2,
    )

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text="Write to the stuck disk")

    def _execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        time.sleep(1.0)  # blocking, as a file system call is
        return ToolResult(ok=True, content="written", effect="confirmed")


@pytest.fixture
def gated(spine: Spine) -> Gated:
    tool = Gated()
    spine.registry.register(tool)
    return tool


async def proposed(spine: Spine, name: str) -> str:
    r = await spine.call(name, {})
    assert r.status == "confirmation_required" and r.pending_id
    return r.pending_id


async def status_of(spine: Spine, pid: str) -> Status:
    a = await spine.store.get_pending(pid)
    assert a is not None
    return a.status


async def test_cancelling_after_the_claim_still_records_the_outcome(
    spine: Spine, gated: Gated
) -> None:
    pid = await proposed(spine, "gated")
    asked = asyncio.create_task(spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap")))
    await gated.started.wait()
    asked.cancel()  # the user barged in; the tab closed
    await asyncio.sleep(0.01)
    assert await status_of(spine, pid) is Status.EXECUTING  # still running, not abandoned
    gated.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asked  # the cancellation is honoured, once the outcome is written
    assert await status_of(spine, pid) is Status.SUCCEEDED
    again = await spine.router.resolve(pid, "confirm", spine.ctx(turn=3, via="tap"))
    assert again.status == "succeeded"  # not "in_progress" until the next start
    assert gated.executions == 1


async def test_cancelling_again_and_again_changes_nothing(spine: Spine, gated: Gated) -> None:
    pid = await proposed(spine, "gated")
    asked = asyncio.create_task(spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap")))
    await gated.started.wait()
    for _ in range(5):
        asked.cancel()
        await asyncio.sleep(0)
    gated.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asked
    assert await status_of(spine, pid) is Status.SUCCEEDED and gated.executions == 1


async def test_a_cancelled_action_that_never_finishes_is_recorded_as_unknown(
    spine: Spine, gated: Gated
) -> None:
    pid = await proposed(spine, "gated")
    asked = asyncio.create_task(spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap")))
    await gated.started.wait()
    asked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asked  # its own timeout ends it; nobody released it
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.UNKNOWN and a.effect_status == "unknown"


async def test_a_stuck_disk_is_timed_out_and_the_loop_keeps_running(spine: Spine) -> None:
    spine.registry.register(StuckDisk())
    pid = await proposed(spine, "stuck_disk")
    beats = 0

    async def heartbeat() -> None:
        nonlocal beats
        while True:
            await asyncio.sleep(0.02)
            beats += 1

    pulse = asyncio.create_task(heartbeat())
    start = time.monotonic()
    done = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    took = time.monotonic() - start
    pulse.cancel()
    assert done.status == "unknown" and "timed out" in done.content  # not a guessed success
    assert took < 0.6  # the timeout stopped the waiting; the disk call did not hold it
    assert beats >= 5  # and nothing else stood still meanwhile


async def test_the_real_file_tools_do_their_disk_work_off_the_loop(
    spine: Spine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.tools import beneath

    real = beneath.open_file

    def slow_disk(*args: object, **kw: object) -> int:
        time.sleep(0.3)  # a slow network drive, say
        return real(*args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(beneath, "open_file", slow_disk)
    target = spine.granted / "slow.txt"
    r = await spine.call("file_create", {"path": str(target), "content": "hi"})
    beats = 0

    async def heartbeat() -> None:
        nonlocal beats
        while True:
            await asyncio.sleep(0.02)
            beats += 1

    pulse = asyncio.create_task(heartbeat())
    done = await spine.router.resolve(r.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    pulse.cancel()
    assert done.status == "succeeded" and target.read_text() == "hi"
    assert beats >= 5  # the loop kept running while the disk was slow
