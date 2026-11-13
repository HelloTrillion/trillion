"""A tap on a card whose action belongs to another router (the her browser spec).

Her conversation shows a browsing helper's card like any other, but the press it
approves runs in the helper's own quarantined router, never hers. A handler is asked
first: it settles the tap and returns the response when the card is its own, and
None otherwise. With none registered, every tap goes to her router as before.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from trillion.spine.router import RouterResponse

Decision = Literal["confirm", "cancel"]
Handler = Callable[[Any, str, Decision], Awaitable[RouterResponse | None]]

HANDLERS: list[Handler] = []


def register(handler: Handler) -> None:
    if handler not in HANDLERS:
        HANDLERS.append(handler)


async def route(rt: Any, pending_id: str, decision: Decision) -> RouterResponse | None:
    for handler in HANDLERS:
        found = await handler(rt, pending_id, decision)
        if found is not None:
            return found
    return None
