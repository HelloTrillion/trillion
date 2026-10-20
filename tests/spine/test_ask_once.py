"""#32: she asks once, with the card, and a second confirm after a tap is no second success.

A smaller model read "Asks the user first" in a tool's description as an instruction
to ask in chat before calling it, so the user answered twice. And after a tap it
sometimes confirmed the settled card again, which showed a second "succeeded".
"""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal
from trillion.spine.contract import ASKS_BY_CARD, GATED
from trillion.spine.registry import ToolRegistry


def test_every_tool_that_shows_a_card_says_calling_it_is_the_asking() -> None:
    for d in ToolRegistry.discover().definitions():
        lowered = d.description.lower()
        assert "asks the user first" not in lowered, d.name
        if d.risk in GATED:
            assert ASKS_BY_CARD in d.description, d.name


async def test_confirming_a_card_already_tapped_runs_nothing_and_says_so(spine: Spine) -> None:
    r = await spine.call("slow_irreversible", {})
    pid = r.pending_id or ""
    tapped = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    assert tapped.status == "succeeded" and tapped.reason is None
    # The model, on its next turn, confirms the same card again.
    again = await spine.call("confirm_pending_action", {"pending_id": pid}, turn=3)
    assert again.status == "succeeded"  # the outcome is still the true one...
    assert again.reason is Refusal.ALREADY_RESOLVED  # ...said to be an old one
    assert "nothing ran again" in again.detail
    assert '"reason": "already_resolved"' in again.to_model()
    assert spine.slow.executions == 1
