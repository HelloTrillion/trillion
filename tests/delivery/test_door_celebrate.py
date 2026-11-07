"""GAL-9: a celebration rides on its notice through the one door (SUR-9), as the
attention engine chose it, and so never arrives during a reply (VOI-17)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re

import pytest

from tests.delivery.test_door import DUE, FakeNotify, FakePage, add
from tests.delivery.test_door_quiet import FakeQuiet
from tests.delivery.test_structure import SRC, file_imports
from tests.scheduler.conftest import FakeClock
from trillion.attention.celebrate import LONG_RUN_MS, Celebrations
from trillion.delivery import door as door_module
from trillion.delivery.door import Door, Quiet, Tier
from trillion.scheduler.store import Notice
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore


class FakeCelebrate:
    """Gives each notice whatever tier it is told to, and keeps what it was asked."""

    def __init__(self, tiers: dict[str, Tier] | None = None, default: Tier | None = None) -> None:
        self.tiers = tiers or {}
        self.default = default
        self.asked: list[str] = []

    async def tier(self, notice: Notice, now_ms: int) -> Tier | None:
        self.asked.append(notice.id)
        return self.tiers.get(notice.id, self.default)


def door(
    store: SqliteScheduleStore,
    pages: list[FakePage],
    celebrate: object | None = None,
    quiet: Quiet | None = None,
) -> Door:
    d = Door(store, pages, clock=FakeClock(DUE), owner="local", notify=FakeNotify(), quiet=quiet)
    if celebrate is not None:
        d.celebrate = celebrate  # type: ignore[assignment]
    return d


async def test_without_the_attention_engine_nothing_celebrates(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    await door(store, [page]).added(await add(store, kind="ask", text="Done."))
    assert [x.celebrate for x in page.got] == [None]


async def test_the_engines_tier_rides_on_its_notice(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    n = await add(store, kind="ask", text="Done.")
    await door(store, [page], FakeCelebrate(default="medium")).added(n)
    (got,) = page.got
    assert (got.id, got.celebrate) == (n.id, "medium")


async def test_one_per_hand_over_the_largest(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    page.state = dataclasses.replace(page.state, turn_running=True)
    a = await add(store, kind="ask", text="One.")
    b = await add(store, kind="ask", text="Two.")
    c = await add(store, kind="ask", text="Three.")
    d = door(store, [page], FakeCelebrate({a.id: "small", b.id: "large", c.id: "large"}))
    await d.added(a)
    assert page.got == []  # a reply is under way: nothing, and no celebration (VOI-17)
    page.state = dataclasses.replace(page.state, turn_running=False)
    assert await d.offer(page) == 3
    assert [(x.id, x.celebrate) for x in page.got] == [(a.id, None), (b.id, "large"), (c.id, None)]


async def test_never_while_her_voice_is_held_for_a_quiet_reason(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    engine = FakeCelebrate(default="large")
    d = door(store, [page], engine, FakeQuiet("Focus is on"))
    await d.added(await add(store, kind="ask", text="Done."))
    await d.drain()
    assert [(x.quiet, x.celebrate) for x in page.got] == [("Focus is on", None)]
    assert engine.asked == []


class Broken:
    async def tier(self, notice: Notice, now_ms: int) -> Tier | None:
        raise OSError("no records")


class Slow:
    async def tier(self, notice: Notice, now_ms: int) -> Tier | None:
        await asyncio.sleep(60)
        return "large"


class Made:
    async def tier(self, notice: Notice, now_ms: int) -> Tier | None:
        return "enormous"  # type: ignore[return-value]


@pytest.mark.parametrize("engine", [Broken(), Slow(), Made()])
async def test_an_engine_that_cant_tell_celebrates_nothing(
    store: SqliteScheduleStore,
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(door_module, "CELEBRATE_TIMEOUT_S", 0.05)
    page = FakePage("c1")
    with caplog.at_level(logging.WARNING):
        await door(store, [page], engine).added(await add(store, kind="ask", text="Done."))
    # The notice still arrives, as it would have.
    assert [(x.text, x.celebrate) for x in page.got] == [("Done.", None)]


async def test_the_real_engine_through_the_door(store: SqliteScheduleStore) -> None:
    """A timed ask that worked for 20 minutes: the attention engine reads its run and
    the door carries a small one."""
    page = FakePage("c1")
    n = await add(store, kind="ask", text="Done.")
    # Make its run a long one, as a real one that worked that long would be.
    async with store._tx() as conn:
        await conn.execute(
            "UPDATE task_runs SET started_ms = ? WHERE result_notice = ?",
            (DUE - LONG_RUN_MS - 60_000, n.id),
        )
    await door(store, [page], Celebrations(store, owner="local")).added(n)
    assert [x.celebrate for x in page.got] == ["small"]


# ── By construction: nothing but the attention engine can ask for one ──────────

# The only files in the core that may so much as name a celebration: the attention
# engine decides, the door carries it, the session sends what the door gave it, and
# the runtime puts the engine in the door.
ALLOWED = {
    "attention/celebrate.py",
    "delivery/door.py",
    "api/session.py",
    "api/runtime.py",
}
WORDS = re.compile(r"celebrat|supernova", re.IGNORECASE)


def test_only_the_attention_engine_and_the_door_name_a_celebration() -> None:
    """No tool, plugin, connector, skill, MCP server or model path can emit one: none
    of them has a name for it, an argument that carries it, or a route to the field."""
    naming = sorted(
        py.relative_to(SRC).as_posix()
        for py in SRC.rglob("*.py")
        if WORDS.search(py.read_text(encoding="utf-8"))
    )
    assert set(naming) == ALLOWED, naming
    # Nothing in the core but the runtime imports the engine.
    users = sorted(
        py.relative_to(SRC).as_posix()
        for py in SRC.rglob("*.py")
        if any(m.startswith("trillion.attention.celebrate") for m in file_imports(py))
    )
    assert users == ["api/runtime.py"]


def test_the_session_only_passes_on_what_the_door_gave_it() -> None:
    source = (SRC / "api" / "session.py").read_text(encoding="utf-8")
    code = [ln.strip() for ln in source.splitlines() if not ln.strip().startswith("#")]
    used = [ln for ln in code if WORDS.search(ln)]
    assert used == ["celebrate=n.celebrate,"]


def test_the_runtime_only_puts_the_engine_in_the_door() -> None:
    source = (SRC / "api" / "runtime.py").read_text(encoding="utf-8")
    code = [ln.strip() for ln in source.splitlines() if not ln.strip().startswith("#")]
    used = sorted(ln for ln in code if WORDS.search(ln))
    assert used == [
        "from trillion.attention.celebrate import Celebrations",
        "rt.door.celebrate = Celebrations(schedules, owner=OWNER)",
    ]


def test_tools_and_plugins_cannot_import_the_door() -> None:
    """The field is set in the door alone; a tool or plugin can't even reach the door."""
    # Connectors include the MCP servers'.
    for pkg in ("tools", "skills", "connectors", "channels", "helpers"):
        root = SRC / pkg
        found = sorted(root.rglob("*.py"))
        assert found, pkg  # the package is where this test looks
        for py in found:
            bad = [m for m in file_imports(py) if m.startswith("trillion.delivery")]
            assert bad == [], f"{py.relative_to(SRC)} reaches {bad}"


def test_the_check_sees_a_tool_that_names_one() -> None:
    assert WORDS.search('result = {"supernova": "large"}')
    assert WORDS.search("def celebrate(): ...")
    assert WORDS.search("CELEBRATION = True")
