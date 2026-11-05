"""Who is calling: a live conversation, or work nobody is waiting on (BRN-7)."""

from __future__ import annotations

import asyncio

from trillion.spend.caller import Call, as_caller, current


def test_an_unset_caller_is_background() -> None:
    assert current() == Call("background", None)


def test_as_caller_sets_the_caller_and_resets_it() -> None:
    with as_caller("conversation", "c1") as call:
        assert call == Call("conversation", "c1")
        assert current() == Call("conversation", "c1")
    assert current() == Call("background", None)


def test_as_caller_nests_and_each_level_resets_to_the_one_outside() -> None:
    with as_caller("conversation", "c1"):
        with as_caller("background"):
            assert current() == Call("background", None)
        assert current() == Call("conversation", "c1")
    assert current() == Call("background", None)


def test_the_caller_resets_even_when_the_block_raises() -> None:
    try:
        with as_caller("conversation", "c1"):
            raise ValueError
    except ValueError:
        pass
    assert current() == Call("background", None)


async def test_the_caller_holds_across_awaits_and_tasks_inherit_it() -> None:
    async def who() -> Call:
        await asyncio.sleep(0)
        return current()

    with as_caller("conversation", "c1"):
        await asyncio.sleep(0)
        assert current() == Call("conversation", "c1")
        assert await asyncio.create_task(who()) == Call("conversation", "c1")
    # A task started outside sees no caller, whatever another task set.
    assert await asyncio.create_task(who()) == Call("background", None)


async def test_a_caller_set_inside_a_task_does_not_leak_out() -> None:
    async def label() -> None:
        with as_caller("conversation", "c2"):
            await asyncio.sleep(0)

    with as_caller("background"):
        await asyncio.gather(label(), label())
        assert current() == Call("background", None)
