"""Who is making a paid call: a live conversation, or background work (BRN-7).

Set once at the top of each caller with `as_caller`, and read by the spend gate and
the usage rows. It is a context variable, so it holds across awaits in the task that
set it, and a task started inside the block inherits it. A path nobody labelled
counts as `background`, so it is capped the stricter way.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple

from trillion.spend.types import Caller


class Call(NamedTuple):
    caller: Caller
    # The conversation the call is for, when there is one.
    conversation_id: str | None = None


UNSET = Call("background", None)

_current: ContextVar[Call | None] = ContextVar("trillion_spend_caller", default=None)


@contextmanager
def as_caller(caller: Caller, conversation_id: str | None = None) -> Iterator[Call]:
    """Label every paid call made inside the block. Blocks nest; leaving one puts back
    the caller from outside it."""
    call = Call(caller, conversation_id)
    token = _current.set(call)
    try:
        yield call
    finally:
        _current.reset(token)


def current() -> Call:
    return _current.get() or UNSET
