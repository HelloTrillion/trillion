"""Who is making a paid call: a live conversation, or background work (BRN-7).

Set once at the top of each caller with `as_caller`, and read by the spend gate and
the usage rows. It is a context variable, so it holds across awaits in the task that
set it, and a task started inside the block inherits it. A path nobody labelled
counts as `background`, so it is capped the stricter way.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple

from trillion.spend.types import Caller, Work


class Call(NamedTuple):
    caller: Caller
    # The conversation the call is for, when there is one.
    conversation_id: str | None = None
    # Awaited before a usage row for the call is written: a caller whose conversation
    # is only saved once something happens in it (a timed ask) makes it exist here, so
    # the row can name it.
    ready: Callable[[], Awaitable[None]] | None = None
    # The background work it is, so a refusal at a hard cap can say what paused.
    work: Work | None = None


UNSET = Call("background", None)

_current: ContextVar[Call | None] = ContextVar("trillion_spend_caller", default=None)


@contextmanager
def as_caller(
    caller: Caller,
    conversation_id: str | None = None,
    *,
    ready: Callable[[], Awaitable[None]] | None = None,
    work: Work | None = None,
) -> Iterator[Call]:
    """Label every paid call made inside the block. Blocks nest; leaving one puts back
    the caller from outside it."""
    call = Call(caller, conversation_id, ready, work)
    token = _current.set(call)
    try:
        yield call
    finally:
        _current.reset(token)


def current() -> Call:
    return _current.get() or UNSET
