"""`stay_quiet` (VOI-7): she may say nothing, but only when it is hers to choose."""

from __future__ import annotations

from trillion.spine.contract import ToolContext, Via
from trillion.spine.registry import ToolRegistry, runs_in_routine, runs_unattended
from trillion.tools.quiet import CATEGORY, QUIET, StayQuiet


def ctx(*, via: Via = "voice", host_turn: bool = False) -> ToolContext:
    return ToolContext(
        owner_id="local",
        session_id="s1",
        turn=1,
        call_id="c1",
        via=via,
        host_turn=host_turn,
    )


async def test_done_is_accepted_spoken_and_typed() -> None:
    for via in ("voice", "text"):
        r = await StayQuiet().execute({"kind": "done"}, ctx(via=via))
        assert r.ok and r.effect == "none"


async def test_listening_is_spoken_only() -> None:
    assert (await StayQuiet().execute({"kind": "listening"}, ctx(via="voice"))).ok
    for via in ("text", "ask", "channel"):
        typed = await StayQuiet().execute({"kind": "listening"}, ctx(via=via))
        assert not typed.ok and "answer" in typed.content
        assert typed.error_kind == "refused"


async def test_never_on_a_host_note_turn() -> None:
    for kind in ("done", "listening"):
        r = await StayQuiet().execute({"kind": kind}, ctx(via="voice", host_turn=True))
        assert not r.ok and "answer" in r.content


def test_not_offered_unattended() -> None:
    registry = ToolRegistry()
    registry.register(StayQuiet())
    d = registry.definition(QUIET)
    assert d is not None
    assert not runs_unattended(d) and not runs_in_routine(d)
    assert QUIET not in {d.name for d in registry.unattended().definitions()}
    assert QUIET not in {d.name for d in registry.routine().definitions()}


def test_its_schema_names_the_two_kinds() -> None:
    schema = StayQuiet.definition.input_schema
    assert schema["required"] == ["kind"]
    assert schema["properties"]["kind"]["enum"] == ["listening", "done"]
    # No long dashes in what she reads.
    assert not {chr(0x2013), chr(0x2014)} & set(StayQuiet.definition.description)


def test_the_loop_knows_it_by_the_same_name_and_category() -> None:
    from trillion.brain import loop

    assert loop.QUIET == QUIET == StayQuiet.definition.name
    assert loop.QUIET_CATEGORY == CATEGORY == StayQuiet.definition.category


def test_it_is_installed_with_the_built_in_tools() -> None:
    registry = ToolRegistry.discover()
    assert isinstance(registry.get(QUIET), StayQuiet)


def test_listening_is_only_for_an_unfinished_sentence() -> None:
    """A finished sentence, even a vague one, gets an answer: the live test stayed quiet
    on "I just don't know" and the window never ended."""
    text = StayQuiet.definition.description
    assert "unfinished sentence" in text
    assert "I just don't know" in text
