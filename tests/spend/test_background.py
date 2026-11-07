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
from trillion.spend.ledger import Row
from trillion.spend.types import Work
from trillion.storage.db import connect
from trillion.storage.migrate import migrate, names
from trillion.storage.sqlite.attention_store import SqliteAttentionStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore
from trillion.zones import next_month_start

NY = "America/New_York"
DAY = 86_400_000
PAUSED = "Routines and the board are paused: Anthropic is at its $50 cap"
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
    assert await rt.spend.paused("anthropic") == ["routines", "titles", "board", "memories"]

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
    # Two kinds paused, one notice naming what is set up.
    assert await rt.spend.paused("anthropic") == ["routines", "asks", "titles", "board", "memories"]
    assert [n[2] for n in await paused_notices(rt)] == [
        "Routines, timed asks and the board are paused: Anthropic is at its $50 cap"
    ]


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
    assert await rt.spend.paused("anthropic") == ["asks", "titles", "board", "memories"]

    await caps(rt, anthropic=RAISED)
    assert await rt.spend.paused("anthropic") == []
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
    assert await rt.spend.paused("anthropic") == ["asks", "titles", "board", "memories"]
    assert len(await paused_notices(rt)) == 1

    # The 1st: a new month has spent nothing, so it runs.
    first = next_month_start(now, rt.zone() or "")
    at(rt, ms("2026-12-01T09:00"))
    assert rt.clock() >= first
    assert await rt.spend.paused("anthropic") == []
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


async def test_the_runtime_holds_background_work_until_the_cap_is_raised(rt: Runtime) -> None:
    quiet(rt)
    await capped(rt, ScriptedProvider())
    assert await rt.held("watchers")
    assert await rt.spend.paused("anthropic") == ["titles", "board", "memories"]
    await caps(rt, anthropic=RAISED)
    assert not await rt.held("watchers")
    assert await rt.spend.paused("anthropic") == []


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
        assert await rt.spend.paused("anthropic") == ["titles", "board", "memories"]
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
    assert await rt.spend.paused("anthropic") == ["titles", "board", "memories"]


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
    assert await rt.spend.paused("anthropic") == ["titles", "board", "memories"]

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
        assert await migrate(conn) == names(after="017")
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


# ── Review fixes: the usual model, words, began work, paused kinds, 018 ──


async def test_a_switch_never_carries_background_work(rt: Runtime) -> None:
    """A switch is for the conversation (decision 7, BRN-11): at the usual model's cap a
    routine is skipped, and the model switched to is never called for it."""
    from tests.api.test_spend_session import GEMMA
    from trillion.spend.desk import Switch

    assert rt.schedules is not None and rt.scheduler is not None
    quiet(rt)
    now = ms("2026-11-10T09:00")
    at(rt, now)
    await capped(rt, ScriptedProvider())
    rt.spend._switches["anthropic"] = Switch(
        leg="model",
        to="local/gemma",
        label="gemma",
        month=rt.spend.month(),
        until_ms=next_month_start(now, rt.zone() or ""),
        settings=GEMMA.to_json(),
    )
    await rt.use_switches(frozenset({"model"}))
    local = ScriptedProvider(say("never"))
    rt.provider = local
    assert getattr(rt.provider, "provider_id", None) == "local"
    background = rt.background_provider
    assert getattr(background, "provider_id", None) == "anthropic"
    assert background is not rt.provider

    await rt.schedules.create(daily(next_due_ms=now))
    await rt.scheduler.tick()
    assert local.calls == []
    assert await runs(rt) == [("skipped", "capped")]
    assert "routines" in await rt.spend.paused("anthropic")

    # Even unasked, the gate refuses it on the usual model: no call goes to the switch.
    rt.scheduler._held = None
    await rt.schedules.create(daily(id="b2c3d4e5", kind="ask", target=None, next_due_ms=now))
    await rt.scheduler.tick()
    assert local.calls == []
    assert await runs(rt) == [("skipped", "capped"), ("skipped", "capped")]


async def test_a_watcher_wording_refused_at_the_cap_is_not_told(tmp_path: Path) -> None:
    from tests.unit.attention.test_runner import Watcher
    from trillion.attention import words
    from trillion.attention.runner import Find
    from trillion.delivery import interrupted

    # Words refused at the cap raise, never come back as "nothing to say".
    with pytest.raises(ModelCapped):
        await words.line(ScriptedProvider(refused()), "meetings", [], "")
    # A safety notice still has its title: no line, nothing raised.
    action = type("A", (), {"tool": "t", "summary": "s", "id": "a1"})()
    assert await interrupted.line(ScriptedProvider(refused()), "unknown", action) == (  # type: ignore[arg-type]
        None,
        interrupted.Usage(),
    )

    a, b = await connect(tmp_path / "t.db"), await connect(tmp_path / "t.db")
    try:
        await migrate(a)
        store = SqliteAttentionStore(a)
        door = Door()
        clock = FakeClock(NOW)
        provider: list[Any] = [ScriptedProvider(refused(), say("Planning starts at ten."))]
        runner = AttentionRunner(
            store,
            SqliteScheduleStore(b),
            door,
            clock=clock,
            owner="local",
            provider=lambda: provider[0],
            settings=lambda: ON,
        )
        w = Watcher(finds=[Find(key="meeting:1", title="Planning", facts={"title": "x"})])
        runner.add(w)
        await runner.tick()
        assert door.notices == []  # not told while "watchers are paused"
        assert await store.recent("local", clock()) == []
        clock.advance(int(w.interval_s * 1000))
        await runner.tick()
        (n,) = door.notices
        assert n.text == "Planning starts at ten."
    finally:
        await a.close()
        await b.close()


async def test_a_run_that_had_begun_when_refused_is_failed_with_the_reason(
    rt: Runtime,
) -> None:
    from tests.brain.fakes import use
    from trillion.scheduler.runner import CAPPED_MIDWAY

    assert rt.schedules is not None and rt.scheduler is not None
    quiet(rt)
    now = ms("2026-11-10T09:00")
    at(rt, now)
    # A timed ask that called a tool, then was refused on its next round.
    await on_model(
        rt, ANTHROPIC, ScriptedProvider(use("describe_self", {}, "c1", text="Looking."), refused())
    )
    await rt.schedules.create(daily(kind="ask", target=None, next_due_ms=now))
    await rt.scheduler.tick()
    assert await runs(rt) == [("failed", CAPPED_MIDWAY)]
    assert [n[1] for n in await notices(rt)] == ["failed"]
    # A routine likewise, counted as a failure in a row.
    rt.provider = ScriptedProvider(use("describe_self", {}, "c2", text="Looking."), refused())
    await rt.schedules.create(daily(id="b2c3d4e5", next_due_ms=now))
    await rt.scheduler.tick()
    assert (await runs(rt))[-1] == ("failed", CAPPED_MIDWAY)
    s = await rt.schedules.get("b2c3d4e5")
    assert s is not None and s.failures == 1


async def test_paused_lists_the_work_set_up_and_holds_across_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.api.test_spend_session import _open, _secrets

    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    first = await _open(tmp_path, _secrets())
    try:
        assert first.schedules is not None
        await on_model(first, ANTHROPIC, ScriptedProvider())
        await caps(first, anthropic=CAPS)
        await first.schedules.create(daily(next_due_ms=None))
        await first.schedules.create(daily(id="b2c3d4e5", kind="ask", target=None))
        await first.conversations._conn.execute(
            "INSERT INTO usage (owner_id, conversation_id, provider, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, created_at,"
            " provider_id, caller, units)"
            " VALUES ('local', NULL, 'anthropic', 'm', 1, 1, 0, 0, 50.0, ?, 'anthropic',"
            " 'background', 'tokens')",
            (first.clock(),),
        )
        await first.conversations._conn.commit()
    finally:
        await first.close()
    again = await _open(tmp_path, _secrets())
    try:
        # Nothing has run since the restart: what is set up is listed, in order.
        assert await again.spend.paused("anthropic") == [
            "routines",
            "asks",
            "titles",
            "board",
            "memories",
        ]
        # Turned off, a kind is not listed; another provider pauses nothing.
        again.widgets_fresh = False
        assert await again.spend.paused("anthropic") == ["routines", "asks", "titles", "memories"]
        await caps(again, anthropic=CAPS, openai=CAPS)
        again.ledger.add(Row("openai", "conversation", "tokens", 0, 50.0, again.clock()))
        assert again.gate.state("openai") == "capped"
        assert await again.spend.paused("openai") == []
    finally:
        await again.close()


async def test_018_keeps_every_find_its_rowid_and_the_index(tmp_path: Path) -> None:
    from importlib.resources import files

    before = tmp_path / "before"
    before.mkdir()
    for entry in (files("trillion.storage") / "migrations").iterdir():
        if entry.name.endswith(".sql") and entry.name < "018":
            (before / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    conn = await connect(tmp_path / "old.db")
    try:
        await migrate(conn, before)
        for i, status in enumerate(("claimed", "told", "silent", "dismissed")):
            await conn.execute(
                "INSERT INTO attention_finds (rowid, owner_id, key, watcher, first_seen_ms,"
                " notice_id, status, dismissed_ms, detail) VALUES (?, 'local', ?, 'email', ?,"
                " ?, ?, ?, ?)",
                (
                    10 + 3 * i,
                    f"k{i}",
                    NOW + i,
                    f"n{i}" if status == "told" else None,
                    status,
                    NOW if status == "dismissed" else None,
                    f"detail {i}",
                ),
            )
        await conn.commit()
        sql = "SELECT rowid, * FROM attention_finds ORDER BY rowid"
        async with conn.execute(sql) as cur:
            rows = [tuple(r) for r in await cur.fetchall()]
        assert await migrate(conn) == names(after="017")
        async with conn.execute(sql) as cur:
            assert [tuple(r) for r in await cur.fetchall()] == rows
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'attention_finds'"
            " AND sql IS NOT NULL"
        ) as cur:
            assert [r[0] for r in await cur.fetchall()] == ["attention_finds_recent"]
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'attention_finds_before%'"
        ) as cur:
            assert await cur.fetchall() == []
        await conn.execute("UPDATE attention_finds SET status = 'deferred' WHERE key = 'k0'")
        async with conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
    finally:
        await conn.close()


def test_the_paused_notice_names_only_what_is_set_up() -> None:
    from trillion.spend.desk import paused_title

    assert paused_title("Anthropic", "$50", {"routines", "helpers", "titles"}) == (
        "Routines and helpers are paused: Anthropic is at its $50 cap"
    )

    assert paused_title("Anthropic", "$50", {"watchers", "channels", "titles"}) == (
        "Watchers and channel replies are paused: Anthropic is at its $50 cap"
    )
    assert paused_title("OpenAI", "$50", {"board", "titles"}) == (
        "The board is paused: OpenAI is at its $50 cap"
    )
    assert paused_title("OpenAI", "$50", {"titles"}) == (
        "Background work is paused: OpenAI is at its $50 cap"
    )


async def test_a_helpers_work_pauses_at_the_cap_like_routines(rt: Runtime) -> None:
    """A helper's asks and routines are background work of their own kind: "helpers"."""
    assert rt.schedules is not None and rt.scheduler is not None and rt.helpers is not None
    quiet(rt)
    now = ms("2026-11-10T09:00")
    at(rt, now)
    made = await rt.helpers.create(OWNER, "Ledger", "Watches the books.", now)
    assert not isinstance(made, str)
    provider = ScriptedProvider(say("never"))
    await capped(rt, provider)
    await rt.schedules.create(daily(id="d4e5f6a7", next_due_ms=now, helper_id=made.id))
    assert await rt.spend.paused("anthropic") == ["helpers", "titles", "board", "memories"]

    await rt.scheduler.tick()
    assert provider.calls == []
    assert await runs(rt) == [("skipped", "capped")]
    assert [n[2] for n in await paused_notices(rt)] == [
        "Helpers and the board are paused: Anthropic is at its $50 cap"
    ]
