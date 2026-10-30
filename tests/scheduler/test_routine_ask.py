"""A routine's run with nobody there (the routines spec): in its own conversation or
today's day, its board shown there and kept fresh, its request saved as the host's,
and nothing kept when it has nothing to say."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.brain.fakes import say, use
from tests.scheduler.conftest import schedule
from tests.scheduler.test_ask import Recording, count
from trillion.api.runtime import Runtime, open_runtime
from trillion.brain.types import ContextBlock, ProviderError, TextBlock, ToolResultBlock
from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.tool import McpTool, Offered, Outcome
from trillion.files.shares import EVERYONE, routine_scope
from trillion.persona import day
from trillion.persona.prompt import system_blocks
from trillion.scheduler.ask import ROUTINE_RUN, SHOW_OR_NOTHING, Asker, RoutineDeps
from trillion.scheduler.runner import AskFailed, Busy
from trillion.scheduler.store import Schedule
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import JSON, Risk
from trillion.workspace.home import Layout, clean

NY = "America/New_York"
KPIS = {
    "type": "kpis",
    "title": "Revenue",
    "source": "Stripe",
    "metrics": [{"label": "MRR", "value": "$4,200"}, {"label": "New", "value": "12"}],
}
NOTE = {"type": "note", "text": "Quiet week."}


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


def asker(rt: Runtime, provider: Recording | None, **kw: Any) -> Asker:
    return Asker(
        provider=lambda: provider,
        router=rt.router,
        registry=rt.registry,
        conversations=rt.conversations,
        system=lambda scope: system_blocks(rt.identity, rt.soul, rt.shared(scope)),
        identity=lambda: rt.identity,
        shares=rt.reach,
        zone=lambda: NY,
        clock=rt.clock,
        owner="local",
        routines=RoutineDeps(
            **{
                "boards": lambda: rt.boards,
                "reads": rt.reads,
                "day_setup": rt.day_setup,
                **kw,
            }
        ),
    )


def routine(**kw: Any) -> Schedule:
    base: dict[str, Any] = {
        "kind": "ask",
        "title": "Monday revenue",
        "body": "Show me last week's revenue.",
        "target": "own",
    }
    return schedule(**{**base, **kw})


def show(*widgets: dict[str, Any], call_id: str = "s1") -> list[Any]:
    return use("workspace_show", {"widgets": list(widgets), "mode": "replace"}, call_id)


async def test_its_own_conversation_is_made_once_and_its_board_replaced(rt: Runtime) -> None:
    a = asker(rt, Recording(show(KPIS), say("Revenue is up."), show(NOTE), say("Quiet.")))
    s = routine()
    first = await a.place(s)
    assert first is not None
    assert await rt.conversations.kind_of(first, "local") == ("routine", None)
    assert await rt.conversations.title_of(first, "local") == ("Monday revenue", "user")
    built = await a.build(s, first)
    assert (built.shown, built.nothing, built.said, built.outcome) == (
        True,
        False,
        "Revenue is up.",
        "ready",
    )
    board = await rt.boards.current(first)  # type: ignore[union-attr]
    assert board is not None and [w["type"] for w in board["widgets"]] == ["kpis"]
    s = routine(conversation_id=first)
    assert await a.place(s) == first  # the same one, every run
    assert (await a.build(s, first)).outcome == "ready"
    board = await rt.boards.current(first)  # type: ignore[union-attr]
    assert board is not None and [w["type"] for w in board["widgets"]] == ["note"]
    assert await count(rt, "SELECT COUNT(*) FROM conversations WHERE kind = 'routine'") == 1


async def test_a_deleted_conversation_gives_a_fresh_one(rt: Runtime) -> None:
    a = asker(rt, None)
    first = await a.place(routine())
    assert first is not None
    await rt.conversations.delete(first, "local", rt.clock())
    again = await a.place(routine(conversation_id=first))
    assert again is not None and again != first


async def test_the_request_is_the_hosts_and_the_reply_hers(rt: Runtime) -> None:
    provider = Recording(show(KPIS), say("Revenue is up."))
    a = asker(rt, provider)
    s = routine(notes="Leave out refunds.")
    target = await a.place(s)
    assert target is not None
    await a.build(s, target)
    history = await rt.conversations.history(target, "local")
    kinds = [k for k, _ in history]
    assert kinds == ["tap", "assistant", "tool_results", "assistant"]
    asked = history[0][1]
    assert ContextBlock(text=ROUTINE_RUN) in asked.blocks
    (text,) = [b.text for b in asked.blocks if isinstance(b, TextBlock)]
    # The routine's id rides with its name, so she knows which routine she is in.
    assert text.startswith("Routine: Monday revenue (id a1b2c3d4)\n\nShow me last week's revenue.")
    assert "Follow these notes from the user: Leave out refunds." in text
    assert text.endswith(SHOW_OR_NOTHING)
    reply = history[-1][1]
    assert [b.text for b in reply.blocks if isinstance(b, TextBlock)] == ["Revenue is up."]
    # It never counts as the user's: no user message, and the title is the routine's.
    assert "user" not in kinds
    assert await rt.conversations.title_of(target, "local") == ("Monday revenue", "user")
    # Its cost is the conversation's.
    async with rt.conversations._conn.execute(
        "SELECT COUNT(*) FROM usage WHERE conversation_id = ?", (target,)
    ) as cur:
        assert (await cur.fetchone())[0] == 1  # type: ignore[index]


async def test_it_gets_the_board_and_nothing_else_a_timed_ask_does_not(rt: Runtime) -> None:
    provider = Recording(say("Nothing new."))
    a = asker(rt, provider)
    target = await a.place(routine())
    assert target is not None
    await a.build(routine(), target)
    (offered,) = provider.offered
    assert {"workspace_show", "workspace_update", "workspace_remove", "routine_nothing"} <= set(
        offered
    )
    assert not set(offered) & {
        "day_open",
        "workspace_close",
        "ask_schedule",
        "reminder_set",
        "rule_keep",
        "file_create",
        "confirm_pending_action",
    }


async def test_nothing_to_say_keeps_nothing_but_its_cost(rt: Runtime) -> None:
    a = asker(rt, Recording(use("routine_nothing", {}), say("")))
    target = await a.place(routine())
    assert target is not None
    built = await a.build(routine(), target)
    assert built.nothing and built.outcome == "nothing"
    assert await rt.conversations.history(target, "local") == []
    assert await rt.boards.current(target) is None  # type: ignore[union-attr]
    async with rt.conversations._conn.execute(
        "SELECT COUNT(*) FROM usage WHERE conversation_id = ?", (target,)
    ) as cur:
        assert (await cur.fetchone())[0] == 1  # type: ignore[index]


async def test_no_board_and_no_words_is_nothing_too(rt: Runtime) -> None:
    a = asker(rt, Recording(say("")))
    target = await a.place(routine())
    assert target is not None
    assert (await a.build(routine(), target)).outcome == "nothing"
    assert await rt.conversations.history(target, "local") == []


async def test_a_day_routine_fills_todays_day_conversation(rt: Runtime) -> None:
    provider = Recording(show(NOTE), say("A quiet day."))
    a = asker(rt, provider)
    s = routine(target="day", title="Build my day", body="build my day", notes="No Slack.")
    target = await a.place(s)
    assert target is not None
    today = day.today(rt.clock(), NY).isoformat()
    assert await rt.conversations.kind_of(target, "local") == ("day", today)
    assert (await a.build(s, target)).outcome == "ready"
    history = await rt.conversations.history(target, "local")
    (text,) = [b.text for b in history[0][1].blocks if isinstance(b, TextBlock)]
    assert text.startswith("Routine: Build my day (id a1b2c3d4)\n\n[host] It's time for ")
    assert "day routine. Build today's board" in text
    assert "Build today's board" in text and "do not call day_open" in text
    assert text.endswith("Follow these notes from the user: No Slack.")
    # Built: the next run finds a board there, and leaves it alone.
    assert await a.place(s) is None


async def test_a_day_built_by_hand_is_left_alone(rt: Runtime) -> None:
    today = day.today(rt.clock(), NY)
    mine, _ = await rt.conversations.day_conversation(
        "local", today.isoformat(), day.title(today), rt.clock()
    )
    assert rt.boards is not None
    await rt.boards.show(mine, "Mine", [NOTE])
    assert await asker(rt, None).place(routine(target="day")) is None


async def test_what_it_reads_counts_as_outside(rt: Runtime) -> None:
    note = rt.granted_roots[0] / "note.txt"
    note.write_text("The user takes their coffee black.", encoding="utf-8")
    remember = {"kind": "personal", "text": "Coffee black.", "moves": False, "said_by": "user"}
    a = asker(
        rt,
        Recording(
            use("file_read", {"path": str(note)}, "c1"),
            use("memory_write", remember, "c2"),
            show(NOTE, call_id="c3"),
            say("Noted."),
        ),
    )
    target = await a.place(routine())
    assert target is not None
    await a.build(routine(), target)
    history = await rt.conversations.history(target, "local")
    results = [
        json.loads(b.content)
        for _, m in history
        for b in m.blocks
        if isinstance(b, ToolResultBlock)
    ]
    assert "something you read this turn" in results[1]["content"]


async def test_its_widgets_remember_the_reads_that_made_them(rt: Runtime) -> None:
    async def call(name: str, args: JSON, timeout: float) -> Outcome:
        return Outcome(ok=True, text="MRR $4,200")

    server = ServerConfig(
        name="stripe", transport="stdio", command="x", title="Stripe", risks={"charges": Risk.READ}
    )
    offered = Offered(
        name="charges",
        description="charges",
        input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
    )
    rt.registry.register(
        McpTool(server, offered, "mcp_stripe_charges", call), module="trillion.mcp"
    )
    a = asker(
        rt,
        Recording(
            use("mcp_stripe_charges", {"limit": 10}, "c1"), show(KPIS, call_id="c2"), say("Up.")
        ),
    )
    target = await a.place(routine())
    assert target is not None
    await a.build(routine(), target)
    board = await rt.boards.current(target)  # type: ignore[union-attr]
    assert board is not None
    (kpis,) = board["widgets"]
    assert kpis["live"]["reads"] == [{"tool": "mcp_stripe_charges", "args": {"limit": 10}}]
    # Dropped once the run is over.
    assert rt.reads.of(target, -1) == []


async def test_the_running_scheduler_builds_it_and_says_it_is_ready(rt: Runtime) -> None:
    assert rt.schedules is not None and rt.scheduler is not None and rt.door is not None
    shown: list[tuple[str, str]] = []

    def notify(title: str, body: str, *, silent: bool = False) -> bool:
        shown.append((title, body))
        return True

    rt.door.notify = notify  # never the real notification in a test
    now = rt.clock()
    rt.clock = lambda: now  # type: ignore[method-assign]
    rt.provider = Recording(show(KPIS), say("Revenue is up."))
    await rt.schedules.create(routine(next_due_ms=now, zone=NY))
    await rt.scheduler.tick()
    (held,) = await rt.schedules.held("local")
    assert (held.kind, held.title, held.text) == (
        "ready",
        "Your Monday revenue is ready",
        "Your Monday revenue is ready.",
    )
    s = await rt.schedules.get("a1b2c3d4")
    assert s is not None and held.conversation_id == s.conversation_id
    assert held.conversation_id is not None
    board = await rt.boards.current(held.conversation_id)  # type: ignore[union-attr]
    assert board is not None and board["widgets"]
    # Nobody's page was open: one quiet line on the computer.
    assert [t for t, _ in shown] == ["Your Monday revenue is ready"]


async def test_a_board_she_then_judged_not_worth_telling_is_kept(rt: Runtime) -> None:
    a = asker(rt, Recording(show(KPIS), use("routine_nothing", {}, "n1"), say("")))
    target = await a.place(routine())
    assert target is not None
    built = await a.build(routine(), target)
    assert built.shown and built.outcome == "nothing"
    assert await rt.boards.current(target) is not None  # type: ignore[union-attr]
    kinds = [k for k, _ in await rt.conversations.history(target, "local")]
    assert kinds == ["tap", "assistant", "tool_results", "assistant", "tool_results", "assistant"]


async def test_a_turn_running_there_makes_it_wait(rt: Runtime) -> None:
    turning: set[str] = set()
    a = asker(rt, None, busy=lambda c: c in turning)
    first = await a.place(routine())
    assert first is not None
    turning.add(first)
    with pytest.raises(Busy):
        await a.place(routine(conversation_id=first))
    today = day.today(rt.clock(), NY)
    mine, _ = await rt.conversations.day_conversation(
        "local", today.isoformat(), day.title(today), rt.clock()
    )
    turning.add(mine)
    with pytest.raises(Busy):
        await a.place(routine(target="day"))


async def test_a_page_open_there_is_told_after_the_run(rt: Runtime) -> None:
    told: list[str] = []

    async def ran(conversation_id: str) -> None:
        told.append(conversation_id)

    a = asker(rt, Recording(show(KPIS), say("Up."), use("routine_nothing", {}), say("")), ran=ran)
    target = await a.place(routine())
    assert target is not None
    await a.build(routine(), target)
    assert told == [target]
    await a.build(routine(), target)  # nothing kept: nobody to tell
    assert told == [target]


async def test_a_board_a_failed_run_left_is_the_routines_to_replace(rt: Runtime) -> None:
    told: list[str] = []

    async def ran(conversation_id: str) -> None:
        told.append(conversation_id)

    a = asker(rt, Recording(show(NOTE), ProviderError("rate_limited")), ran=ran)
    s = routine(target="day", title="Build my day")
    target = await a.place(s)
    assert target is not None
    with pytest.raises(AskFailed):
        await a.build(s, target)
    assert await rt.boards.current(target) is not None  # type: ignore[union-attr]
    assert told == [target]  # a page open there is shown the board it left
    assert await a.place(s) == target  # not taken for a day built by hand
    # Once the user changes it, it is theirs.
    await rt.boards.show(target, "Mine", [KPIS])  # type: ignore[union-attr]
    assert await a.place(s) is None


async def test_a_day_routine_may_read_the_users_reminders(rt: Runtime) -> None:
    # The day's recipe asks for schedule_list: a routine's run is let read it.
    provider = Recording(use("schedule_list", {}, "l1"), show(NOTE), say("Your day is set."))
    a = asker(rt, provider)
    s = routine(target="day", title="Build my day", body="build my day")
    target = await a.place(s)
    assert target is not None
    assert (await a.build(s, target)).outcome == "ready"
    history = await rt.conversations.history(target, "local")
    results = [b for _, m in history for b in m.blocks if isinstance(b, ToolResultBlock)]
    listed = next(r for r in results if r.tool_use_id == "l1")
    assert not listed.is_error and "nothing is set" in json.dumps(listed.content)


async def test_a_run_is_told_its_board_and_where_its_reply_goes(rt: Runtime) -> None:
    provider = Recording(show(KPIS), say("Revenue is up."), say("Still up."))
    a = asker(rt, provider)
    first = await a.place(routine())
    assert first is not None
    await a.build(routine(), first)
    s = routine(conversation_id=first)
    await a.build(s, first)
    asked = provider.calls[-1][1][-1]
    context = "".join(b.text for b in asked.blocks if isinstance(b, ContextBlock))
    # The board as it stands, so workspace_update and remove can name its widgets.
    assert "Board (" in context and "Revenue" in context
    (text,) = [b.text for b in asked.blocks if isinstance(b, TextBlock)]
    assert "the user gets a short ready line" in text
    assert "your reply stays in this routine's conversation" in text


async def test_a_run_reaches_everyones_shares_and_its_own_only(rt: Runtime, tmp_path: Path) -> None:
    folders = {}
    for name, scope in (
        ("Mine", routine_scope("a1b2c3d4")),
        ("Theirs", routine_scope("ffff0000")),
        ("Everyone", EVERYONE),
    ):
        folder = tmp_path / name.lower()
        folder.mkdir()
        (folder / "note.txt").write_text(f"in {name}", encoding="utf-8")
        rt.shares.put(folder, name=name, access="read", scope=scope)
        folders[name] = folder
    provider = Recording(
        *(
            use("file_read", {"path": str(folders[n] / "note.txt")}, f"c{i}")
            for i, n in enumerate(folders)
        ),
        show(NOTE, call_id="s9"),
        say("Read."),
    )
    a = asker(rt, provider)
    target = await a.place(routine())
    assert target is not None
    await a.build(routine(), target)
    history = await rt.conversations.history(target, "local")
    results = [b.content for _, m in history for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert "in Mine" in results[0]
    assert "in Theirs" not in results[1] and "outside the folders" in results[1]
    assert "in Everyone" in results[2]
    # Her prompt lists what the run reaches, by name, and nothing else.
    system = provider.calls[0][0][0].text
    assert '- "Mine": ' in system and '- "Everyone": ' in system and "Theirs" not in system


async def test_a_timed_ask_reaches_everyones_shares_only(rt: Runtime, tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "note.txt").write_text("in Mine", encoding="utf-8")
    rt.shares.put(mine, name="Mine", scope=routine_scope("a1b2c3d4"))
    provider = Recording(use("file_read", {"path": str(mine / "note.txt")}, "c1"), say("No."))
    await asker(rt, provider)(schedule(kind="ask", body="Read my note."))
    (_, sent) = provider.calls[-1]
    results = [b.content for m in sent for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert "outside the folders" in results[0] and "in Mine" not in results[0]


async def test_a_day_routine_follows_homes_layout(rt: Runtime) -> None:
    provider = Recording(show(NOTE), say("A quiet day."))
    gone = clean({"removed": ["tasks", "kpis"]})

    async def layout() -> Layout:
        return gone

    a = asker(rt, provider, home_layout=layout)
    s = routine(target="day", title="Build my day", body="build my day")
    target = await a.place(s)
    assert target is not None
    await a.build(s, target)
    history = await rt.conversations.history(target, "local")
    (text,) = [b.text for b in history[0][1].blocks if isinstance(b, TextBlock)]
    assert "Leave off: Reminders (tasks), Revenue (kpis)." in text
    assert "schedule_list" not in text
