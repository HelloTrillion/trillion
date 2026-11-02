"""Staying quiet in a session (VOI-7): a chosen silence ends the turn without a word
and without the notice an empty reply gets."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from typing import Any

from tests.api.test_server import home, make, only, propose  # noqa: F401
from tests.brain.fakes import USAGE, say, use
from trillion.brain.types import Done, ToolResultBlock
from trillion.tools.quiet import QUIET


def test_a_typed_sign_off_ends_quiet_with_no_notice(make: Any) -> None:
    app = make(use(QUIET, {"kind": "done"}))
    with app.connect() as chat:
        msgs = chat.say("thanks, that's all")
    complete = only(msgs, "turn.complete")
    assert complete["stop"] == "quiet" and complete["quiet"] == "done"
    assert not [m for m in msgs if m["type"] in ("notice", "reply.delta", "error")]
    # One model call: nothing after the silence she chose.
    assert len(app.provider.calls) == 1


def test_an_empty_reply_still_says_so(make: Any) -> None:
    app = make([Done(stop_reason="end", usage=USAGE)])
    with app.connect() as chat:
        msgs = chat.say("hello")
    complete = only(msgs, "turn.complete")
    assert complete["stop"] == "empty" and "quiet" not in complete
    assert only(msgs, "notice") == {"type": "notice", "code": "stopped", "reason": "empty"}


def test_she_answers_a_card_tap_rather_than_staying_quiet(make: Any) -> None:
    app = make()
    app.provider._replies += [
        propose(app),
        # Her own turn, after the card: a silence she may choose.
        use(QUIET, {"kind": "done"}),
        # The turn that answers the tap: refused, so she answers it.
        use(QUIET, {"kind": "done"}),
        say("Done, the note is written."),
    ]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        chat.ws.send_json({"type": "confirm.shown", "pending_id": card["pending_id"]})
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        msgs = chat.until("turn.complete")
    # The user's own turn could end quiet; the turn answering the tap could not.
    complete = only(msgs, "turn.complete")
    assert complete["stop"] == "end" and "quiet" not in complete
    results = [
        b for m in app.provider.calls[-1][1] for b in m.blocks if isinstance(b, ToolResultBlock)
    ]
    assert results[-1].is_error and "note from the app" in results[-1].content
