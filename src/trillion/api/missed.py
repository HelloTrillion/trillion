"""Missed approvals under Needs you (SPN-17, SPN-2b, SUR-7).

A card that expired before the user answered it is an unseen decision. Each from the
last 7 days is sent to the page when it opens, one line each under Needs you, with
Ask again and Dismiss, until the user does one or the other.

These lines are the app's: never spoken, never saved as her message, and never handed
to her as something to raise again (the one-voice rules). Ask again is the user's tap,
not hers: it is the router's ordinary re-present, a fresh card with fresh checks, in
the conversation the card was raised in. A card that can't be asked again (her
browser's, whose task has ended) offers only Dismiss, with the reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trillion.spine import labels
from trillion.spine.confirm import PendingAction, Refusal
from trillion.spine.router import RouterResponse
from trillion.tools.schedule import ASK as ASK_SCHEDULE

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime

OWNER = "local"
# A browsing helper's card lives in a session of its task, which ends with the task.
BROWSER_SESSION = "browse:"

BROWSER_ENDED = "Her browser's task has ended, so this can't be asked again."
CHAT_GONE = "Its conversation was deleted, so this can't be asked again."
TOOL_GONE = "What it used isn't set up any more, so this can't be asked again."
CLEARED = "Its details are no longer kept, so this can't be asked again."
# What the line says when Ask again is tapped on one no longer listed.
GONE = "This was already asked again or dismissed."


async def why_not(rt: Runtime, a: PendingAction) -> str | None:
    """Why this card can't be asked again, in plain words; None when it can."""
    if a.session_id.startswith(BROWSER_SESSION):
        return BROWSER_ENDED
    if a.args_cleared:
        return CLEARED
    if rt.registry.get(a.tool) is None:
        return TOOL_GONE
    if not await rt.conversations.exists(a.session_id, OWNER):
        return CHAT_GONE
    return None


async def item(rt: Runtime, a: PendingAction) -> dict[str, Any]:
    """One line under Needs you, from the host's words only (SPN-2a)."""
    # Here, not at the top: the session imports this module.
    from trillion.api.session import ROUTINE_LABEL

    reg = rt.registry
    label = labels.tool_label(a.tool, labels.connected(reg.definition(a.tool), reg.get(a.tool)))
    if a.tool == ASK_SCHEDULE and a.args.get("routine") in ("day", "own"):
        label = ROUTINE_LABEL
    why = await why_not(rt, a)
    shown = await rt.spine_store.get_delivery(a.id)
    return {
        "pending_id": a.id,
        "tool_label": label,
        "summary": a.summary,
        "expired_at": a.expires_at,
        # Whether its card was ever on a screen (SPN-17's `delivered_at`).
        "shown": shown is not None,
        # Where Ask again raises the fresh card; null when it can't be asked again.
        "conversation_id": a.session_id if why is None else None,
        "why_not": why,
    }


async def items(rt: Runtime) -> list[dict[str, Any]]:
    """The owner's unseen decisions, newest first, as the page lists them."""
    return [await item(rt, a) for a in await rt.router.missed(OWNER)]


async def listed(rt: Runtime, pending_id: str) -> PendingAction | None:
    """The card, if it is still an unseen decision: not asked again or dismissed."""
    for a in await rt.router.missed(OWNER):
        if a.id == pending_id:
            return a
    return None


# Why Ask again raised no card, in the user's words. The router's own detail is for her.
_REFUSED: dict[Refusal, str] = {
    Refusal.WRONG_SESSION: "Open the conversation it was asked in, then ask again.",
    Refusal.DENIED_BY_FLOOR: "It reaches somewhere she can't any more, so it can't be asked again.",
    Refusal.NOT_FOR_HELPERS: "A helper's chat can't ask for this.",
    Refusal.UNKNOWN_TOOL: TOOL_GONE,
}
COULD_NOT = "It couldn't be asked again."


def refused_words(r: RouterResponse) -> str:
    """Why Ask again raised no fresh card, plainly. Still listed: it was not asked."""
    if r.reason is Refusal.PRECONDITION_FAILED and r.detail:
        return f"It can't be done as it was: {r.detail}"
    return _REFUSED.get(r.reason, COULD_NOT) if r.reason is not None else COULD_NOT
