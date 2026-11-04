"""Background work at the hard cap (the spend caps spec, decision 7): it pauses and
says so, once a month, and never moves to another model.

A routine or timed ask is skipped with the reason `capped`, never failed or retried; a
watcher keeps its place and reads the same again once the cap lifts; a title, a board
refresh and a channel reply are dropped quietly. All of it resumes when the cap is
raised, or on the 1st."""

# ruff: noqa: F811 - the fixtures are shared with the spend session and refresh tests

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import home, make  # noqa: F401
from tests.api.test_session import Page
from tests.api.test_spend_session import (  # noqa: F401
    ANTHROPIC,
    CAPS,
    caps,
    no_wait,
    no_whisper,
    on_model,
    rt,
    spend,
)
from tests.brain.fakes import ScriptedProvider, say
from tests.scheduler.conftest import FakeClock, ms, schedule
from tests.unit.attention.test_mail import NOW, ON, Gmail, message, verdict
from tests.unit.attention.test_runner import Door
from tests.workspace.test_refresh import Filler, World, world  # noqa: F401
from trillion.api.runtime import OWNER, Runtime
from trillion.api.session import Session
from trillion.attention.mail import UrgentEmail
from trillion.attention.runner import AttentionRunner
from trillion.brain.types import ModelEvent, Msg, TextBlock
from trillion.channels.base import Inbound
from trillion.scheduler.when import Repeat
from trillion.spend.gate import ModelCapped
from trillion.spend.types import Work
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.attention_store import SqliteAttentionStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore
from trillion.zones import next_month_start

NY = "America/New_York"
DAY = 86_400_000
PAUSED = "Routines, watchers and channel replies are paused: Anthropic is at its $50 cap"
RAISED = {"soft": 20, "hard": 100, "warn_pct": 80}


def refused() -> ModelCapped:
    """What the gate raises at the cap: here, as if it was crossed mid-way."""
    return ModelCapped("anthropic", "background", 50.0, "dollars")


def quiet(rt: Runtime) -> None:
    assert rt.door is not None

    def notify(title: str, body: str, *, silent: bool = False) -> bool:
        return True

    rt.door.notify = notify  # never the real notification in a test


def at(rt: Runtime, now: int) -> None:
    rt.clock = lambda: now  # type: ignore[method-assign]


async def capped(rt: Runtime, provider: Any) -> None:
    await on_model(rt, ANTHROPIC, provider)
    await caps(rt, anthropic=CAPS)
    spend(rt, "anthropic", 50.0)


async def runs(rt: Runtime) -> list[tuple[str, str | None]]:
    assert rt.schedules is not None
    async with rt.schedules._conn.execute(
        "SELECT status, detail FROM task_runs ORDER BY id"
    ) as cur:
        return [(r[0], r[1]) for r in await cur.fetchall()]


async def notices(rt: Runtime) -> list[tuple[str, str, str, str | None]]:
    assert rt.schedules is not None
    async with rt.schedules._conn.execute(
        "SELECT source_id, kind, title, text FROM notices ORDER BY created_ms, id"
    ) as cur:
        return [(r[0], r[1], r[2], r[3]) for r in await cur.fetchall()]


async def paused_notices(rt: Runtime) -> list[tuple[str, str, str, str | None]]:
    return [n for n in await notices(rt) if n[0].startswith("spend:paused:")]


def daily(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "kind": "ask",
        "title": "Monday revenue",
        "body": "Show me last week's revenue.",
        "target": "own",
        "zone": NY,
        "at_local": "2026-11-10T09:00",
        "repeat": Repeat(every="day"),
    }
    return schedule(**{**base, **kw})


# ── Routines and timed asks ──────────────────────────────────────────────


async def test_a_routine_at_the_cap_is_skipped_capped_and_waits_for_its_next_time(
    rt: Runtime,
) -> None:
    assert rt.schedules is not None and rt.scheduler is not None
    quiet(rt)
    now = ms("2026-11-10T09:00")
    at(rt, now)
    provider = ScriptedProvider(say("never"))
    await capped(rt, provider)
    await rt.schedules.create(daily(next_due_ms=now))

    await rt.scheduler.tick()
    assert provider.calls == []  # refused before anything was sent
    assert await runs(rt) == [("skipped", "capped")]
    s = await rt.schedules.get("a1b2c3d4")
    assert s is not None
    # Not a failure: nothing counted toward pausing it, and it moved on to tomorrow.
    assert s.failures == 0 and s.paused_ms is None
    assert s.next_due_ms == ms("2026-11-11T09:00")
    # Not retried: the next tick runs nothing.
    await rt.scheduler.tick()
    assert await runs(rt) == [("skipped", "capped")]
    # Said once, by the app, shown and not spoken; no failed or missed notice.
    assert [(n[1], n[2], n[3]) for n in await notices(rt)] == [("heads_up", PAUSED, None)]
    assert rt.spend.paused("anthropic") == ["routines"]

    # Its next time, still capped: skipped again, and nothing more said.
    at(rt, ms("2026-11-11T09:00"))
    await rt.scheduler.tick()
    assert await runs(rt) == [("skipped", "capped"), ("skipped", "capped")]
    assert len(await notices(rt)) == 1


async def test_a_timed_ask_at_the_cap_is_skipped_and_leaves_no_conversation(
    rt: Runtime,
) -> None:
    assert rt.schedules is not None and rt.scheduler is not None
    quiet(rt)
    now = ms("2026-11-10T09:00")
    at(rt, now)
    provider = ScriptedProvider(say("never"))
    await capped(rt, provider)
    # A routine and a timed ask, both due.
    await rt.schedules.create(daily(id="b2c3d4e5", next_due_ms=now))
    await rt.schedules.create(daily(id="c3d4e5f6", kind="ask", target=None, next_due_ms=now))

    await rt.scheduler.tick()
    assert provider.calls == []
    assert sorted(await runs(rt)) == [("skipped", "capped"), ("skipped", "capped")]
    async with rt.conversations._conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE kind = 'scheduled'"
    ) as cur:
        assert (await cur.fetchone())[0] == 0  # type: ignore[index]
    # Two kinds paused, one notice.
    assert rt.spend.paused("anthropic") == ["routines", "asks"]
    assert [n[2] for n in await paused_notices(rt)] == [PAUSED]


async def test_one_refused_mid_way_is_skipped_too_never_failed(rt: Runtime) -> None:
    assert rt.schedules is not None and rt.scheduler is not None
    quiet(rt)
    now = ms("2026-11-10T09:00")
    at(rt, now)
    # Under the cap when it starts; the gate refuses its call (the cap crossed meanwhile).
    await on_model(rt, ANTHROPIC, ScriptedProvider(refused()))
    await rt.schedules.create(daily(kind="ask", target=None, next_due_ms=now))
    await rt.scheduler.tick()
    assert await runs(rt) == [("skipped", "capped")]
    assert await notices(rt) == []
    await rt.schedules.create(daily(id="b2c3d4e5", next_due_ms=now))
    rt.provider = ScriptedProvider(refused())
    await rt.scheduler.tick()
    assert await runs(rt) == [("skipped", "capped"), ("skipped", "capped")]
    s = await rt.schedules.get("b2c3d4e5")
    assert s is not None and s.failures == 0


async def test_routines_resume_when_the_cap_is_raised(rt: Runtime) -> None:
    assert rt.schedules is not None and rt.scheduler is not None
    quiet(rt)
    now = ms("2026-11-10T09:00")
    at(rt, now)
    provider = ScriptedProvider(say("Revenue is up."))
    await capped(rt, provider)
    await rt.schedules.create(daily(kind="ask", target=None, next_due_ms=now))
    await rt.scheduler.tick()
    assert rt.spend.paused("anthropic") == ["asks"]

    await caps(rt, anthropic=RAISED)
    assert rt.spend.paused("anthropic") == []
    at(rt, ms("2026-11-11T09:00"))
    await rt.scheduler.tick()
    assert len(provider.calls) == 1
    assert [r[0] for r in await runs(rt)] == ["skipped", "delivered"]


async def test_everything_resumes_on_the_1st_and_a_new_month_says_so_again(
    rt: Runtime,
) -> None:
    assert rt.schedules is not None and rt.scheduler is not None
    quiet(rt)
    now = ms("2026-11-30T09:00")
    at(rt, now)
    provider = ScriptedProvider(say("Revenue is up."), say("never"))
    await capped(rt, provider)
    await rt.schedules.create(daily(kind="ask", target=None, next_due_ms=now))
    await rt.scheduler.tick()
    assert rt.spend.paused("anthropic") == ["asks"]
    assert len(await paused_notices(rt)) == 1

    # The 1st: a new month has spent nothing, so it runs.
    first = next_month_start(now, rt.zone() or "")
    at(rt, ms("2026-12-01T09:00"))
    assert rt.clock() >= first
    assert rt.spend.paused("anthropic") == []
    await rt.scheduler.tick()
    assert len(provider.calls) == 1
    assert [r[0] for r in await runs(rt)] == ["skipped", "delivered"]

    # Capped again this month: one more notice, for this month.
    spend(rt, "anthropic", 50.0)
    at(rt, ms("2026-12-02T09:00"))
    await rt.scheduler.tick()
    assert len(provider.calls) == 1
    keys = [n[0] for n in await paused_notices(rt)]
    assert keys == ["spend:paused:anthropic:2026-11", "spend:paused:anthropic:2026-12"]


# ── Watchers ─────────────────────────────────────────────────────────────

MIN = 60_000


@dataclass
class Held:
    on: bool = False
    asked: list[Work] = field(default_factory=list)

    async def __call__(self, work: Work) -> bool:
        self.asked.append(work)
        return self.on


@dataclass
class Watching:
    runner: AttentionRunner
    store: SqliteAttentionStore
    gmail: Gmail
    clock: FakeClock
    door: Door
    held: Held
    interval: int
    # The model the runner is handed, set by `model`.
    provider: list[Any]

    async def place(self) -> tuple[str | None, int | None, str | None]:
        s = await self.store.state("local", "email")
        return s.cursor, s.last_check_ms, s.last_error

    def later(self) -> None:
        self.clock.advance(self.interval)


@pytest.fixture
async def watching(tmp_path: Path) -> AsyncIterator[Watching]:
    a, b = await connect(tmp_path / "t.db"), await connect(tmp_path / "t.db")
    await migrate(a)
    store = SqliteAttentionStore(a)
    clock = FakeClock(NOW)
    gmail = Gmail({"m1": message("m1")})
    door, held = Door(), Held()
    provider: list[Any] = [None]
    runner = AttentionRunner(
        store,
        SqliteScheduleStore(b),
        door,
        clock=clock,
        owner="local",
        provider=lambda: provider[0],
        settings=lambda: ON,
        held=held,
    )
    w = UrgentEmail(gmail)
    runner.add(w)
    rig = Watching(runner, store, gmail, clock, door, held, int(w.interval_s * 1000), provider)
    try:
        # Turned on: the first check starts from now.
        await runner.tick()
        rig.later()
        for m in gmail.found.values():
            m["internalDate"] = str(clock() - MIN)
        yield rig
    finally:
        await a.close()
        await b.close()


def model(rig: Watching, *replies: list[ModelEvent] | Exception) -> ScriptedProvider:
    p = ScriptedProvider(*replies)
    rig.provider[0] = p
    return p


async def test_a_watcher_at_the_cap_keeps_its_place_and_reads_again_after(
    watching: Watching,
) -> None:
    rig = watching
    before = await rig.place()
    provider = model(rig, say(verdict({"1": True})))
    rig.held.on = True
    await rig.runner.tick()
    assert rig.held.asked[-1] == "watchers"
    # Not begun: nothing read, nothing claimed, its place where it was, no error.
    assert rig.gmail.listed == [] and provider.calls == []
    assert await rig.place() == before
    assert await rig.store.recent("local", rig.clock()) == []

    # The cap lifts: the same mail is read, from the same place, and told.
    rig.held.on = False
    rig.later()
    await rig.runner.tick()
    (n,) = rig.door.notices
    assert n.kind == "heads_up" and n.title == "Important email"
    assert len(rig.gmail.listed) == 1


async def test_a_judge_refused_mid_way_gives_its_finds_back(watching: Watching) -> None:
    rig = watching
    before = await rig.place()
    model(rig, refused(), say(verdict({"1": True})))
    await rig.runner.tick()
    # Read, then refused before a word: nothing told, nothing judged silent, nothing
    # claimed, its place put back, and no failure shown.
    assert rig.door.notices == []
    assert await rig.store.recent("local", rig.clock()) == []
    assert await rig.place() == before

    rig.later()
    await rig.runner.tick()
    (n,) = rig.door.notices
    assert n.title == "Important email"
    (found,) = await rig.store.recent("local", rig.clock())
    assert found.status == "told"


async def test_the_runtime_watchers_ask_before_they_read(rt: Runtime) -> None:
    quiet(rt)
    await capped(rt, ScriptedProvider())
    assert await rt.held("watchers")
    assert rt.spend.paused("anthropic") == ["watchers"]
    await caps(rt, anthropic=RAISED)
    assert not await rt.held("watchers")
    assert rt.spend.paused("anthropic") == []


# ── Titles, board refreshes and channel replies: dropped quietly ─────────


async def test_a_title_at_the_cap_is_dropped_quietly(rt: Runtime) -> None:
    quiet(rt)
    provider = ScriptedProvider(say("Coffee"))
    await capped(rt, provider)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    try:
        said = Msg(role="user", blocks=(TextBlock(text="how do I take my coffee?"),))
        reply = Msg(role="assistant", blocks=(TextBlock(text="Black."),))
        await session._name(said, reply)
        assert provider.calls == []
        assert page.all("conversation.title") == [] and page.all("error") == []
        assert rt.spend.paused("anthropic") == ["titles"]
    finally:
        await session.close()


def inbound(text: str = "hi there") -> Inbound:
    return Inbound(
        platform="slack",
        sender="U1",
        name="Sam",
        chat="D1",
        text=text,
        direct=True,
        mentioned=True,
    )


async def test_a_channel_reply_at_the_cap_is_not_made_and_nothing_is_kept(
    rt: Runtime,
) -> None:
    assert rt.channels is not None
    quiet(rt)
    provider = ScriptedProvider(say("never"))
    await capped(rt, provider)
    m = inbound()
    assert await rt.channels._answerer.answer(m) is None
    assert provider.calls == []
    assert not await rt.conversations.exists(m.conversation, OWNER)
    assert rt.spend.paused("anthropic") == ["channels"]


async def test_a_channel_reply_refused_mid_way_is_dropped_too(rt: Runtime) -> None:
    assert rt.channels is not None
    await on_model(rt, ANTHROPIC, ScriptedProvider(refused()))
    m = inbound()
    assert await rt.channels._answerer.answer(m) is None
    assert not await rt.conversations.exists(m.conversation, OWNER)


async def test_a_channel_message_at_the_cap_gets_no_reply(rt: Runtime) -> None:
    assert rt.channels is not None
    quiet(rt)
    await capped(rt, ScriptedProvider(say("never")))
    sent: list[str] = []

    class Adapter:
        async def reply(self, m: Inbound, text: str) -> None:
            sent.append(text)

    rt.channels.adapters["slack"] = Adapter()  # type: ignore[assignment]
    rt.channels.gate.judge = lambda m, now: "answer"  # type: ignore[method-assign]
    await rt.channels.handle(inbound())
    assert sent == []


class Refusing(Filler):
    """Refused at the cap on its first fill-in, as if crossed meanwhile."""

    def __init__(self) -> None:
        super().__init__()
        self.refuse = True

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
        if self.refuse:
            self.refuse = False
            raise refused()
        async for ev in super().stream(**kw):
            yield ev


async def test_a_board_refresh_at_the_cap_leaves_the_board_as_it_was(world: World) -> None:
    rt = world.rt
    rt.provider = rt.meter.model(world.filler, "anthropic")  # type: ignore[arg-type]
    await caps(rt, anthropic=CAPS)
    spend(rt, "anthropic", 50.0)
    before = await world.board()
    world.clock.advance(16)
    await world.s.handle({"type": "board.seen", "on": True})
    assert await world.step() == 0
    # Not begun: no read replayed, no fill-in, no pulse, no failure counted.
    assert world.called == [] and world.filler.n == 0
    assert world.page.all("board.refreshing") == []
    assert await world.board() == before
    assert world.refresher._fails == {}
    assert rt.spend.paused("anthropic") == ["board"]

    # Raised: it refreshes on the next step.
    await caps(rt, anthropic=RAISED)
    assert await world.step() == 1
    assert await world.value() == "$5001"


async def test_a_board_refresh_refused_mid_way_counts_no_failure(world: World) -> None:
    rt = world.rt
    filler = Refusing()
    rt.provider = rt.meter.model(filler, "anthropic")  # type: ignore[arg-type]
    before = await world.board()
    world.clock.advance(16)
    await world.s.handle({"type": "board.seen", "on": True})
    await world.step()
    assert await world.board() == before
    assert world.refresher._fails == {}


async def test_a_deferred_find_is_claimed_again_and_never_deleted(tmp_path: Path) -> None:
    from importlib.resources import files

    # A database from before 018, with a find in it, keeps it.
    before = tmp_path / "before"
    before.mkdir()
    for entry in (files("trillion.storage") / "migrations").iterdir():
        if entry.name.endswith(".sql") and entry.name < "018":
            (before / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    conn = await connect(tmp_path / "old.db")
    try:
        await migrate(conn, before)
        old = SqliteAttentionStore(conn)
        assert await old.claim("local", "email", ["k0"], NOW) == ["k0"]
        await old.told("local", ["k0"], "n0")
        assert await migrate(conn) == ["018_attention_deferred"]
        store = SqliteAttentionStore(conn)
        assert [(f.key, f.status) for f in await store.recent("local", NOW)] == [("k0", "told")]

        assert await store.claim("local", "email", ["k1", "k2"], NOW) == ["k1", "k2"]
        await store.release("local", ["k1", "k0"])  # a told one is never given back
        assert await store.known("local", ["k0", "k1", "k2"]) == {"k0", "k2"}
        assert {f.key for f in await store.recent("local", NOW)} == {"k0", "k2"}
        # The next read claims it afresh, once.
        assert await store.claim("local", "email", ["k1", "k2"], NOW + 1) == ["k1"]
        assert await store.claim("local", "email", ["k1"], NOW + 2) == []
        async with conn.execute("SELECT COUNT(*) FROM attention_finds") as cur:
            assert (await cur.fetchone())[0] == 3  # type: ignore[index]
    finally:
        await conn.close()
