"""Phase 0 item 14 end to end: approve, clear the conversation, ask, and the record answers."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from typing import Any

from tests.api.test_server import home, make, only, propose  # noqa: F401
from tests.brain.fakes import say, use
from trillion.brain.types import ToolResultBlock


def test_a_new_conversation_learns_what_happened_from_the_record(make: Any) -> None:
    app = make()
    app.provider._replies += [
        propose(app),
        say("Shall I?"),
        say("Done."),
        use("decision_record", {"about": "note.txt"}),
        say("Yes: you approved it with a tap, and the note was written."),
    ]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        chat.ws.send_json({"type": "confirm.shown", "pending_id": card["pending_id"]})
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        chat.until("turn.complete")
    # A brand-new conversation: nothing from the first one is in what the model is sent.
    with app.connect() as chat:
        chat.say("did that note I asked for ever get approved?")
    first_call_of_new = len(app.provider.calls) - 2
    assert all(
        "write a note" not in str(getattr(b, "text", ""))
        for m in app.provider.calls[first_call_of_new][1]
        for b in m.blocks
    )
    results = [
        b for m in app.provider.calls[-1][1] for b in m.blocks if isinstance(b, ToolResultBlock)
    ]
    said = results[-1].content
    assert card["pending_id"] in said
    assert "shown: on screen" in said
    assert "answer: approved (a tap on the card)" in said
    assert "outcome: done" in said
