import dataclasses

import pytest

from trillion.spine.confirm import (
    CANCEL_TOOL,
    CONFIRM_TOOL,
    EXPIRY_MS,
    PendingAction,
    Refusal,
    Rejection,
    Status,
    decide,
)
from trillion.spine.contract import Risk, ToolContext

NOW = 1_000_000


def action(**kw: object) -> PendingAction:
    base = PendingAction(
        id="p1",
        owner_id="local",
        tool="file_create",
        args={"path": "/x"},
        args_hash="h",
        summary="Create x",
        target="/x",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id="s1",
        created_turn=1,
        status=Status.PROPOSED,
        created_at=NOW,
        expires_at=NOW + EXPIRY_MS,
        represented_from=None,
    )
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]


def ctx(**kw: object) -> ToolContext:
    base = ToolContext(owner_id="local", session_id="s1", turn=2, call_id="c", via="text")
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]


def test_confirm_on_a_later_turn_from_the_raising_session_approves():
    assert decide(action(), "confirm", ctx(), NOW) is Status.APPROVED


@pytest.mark.parametrize(
    ("a", "c", "reason"),
    [
        (action(), ctx(turn=1), Refusal.SAME_TURN),
        (action(), ctx(session_id="s2"), Refusal.WRONG_SESSION),
        (action(), ctx(owner_id="someone"), Refusal.NOT_FOUND),
        (action(), ctx(via="channel"), Refusal.CHANNEL),
        (action(risk=Risk.SPEND), ctx(via="voice"), Refusal.TAP_REQUIRED),
        (action(recoverable=False), ctx(via="text"), Refusal.TAP_REQUIRED),
        (action(status=Status.SUPERSEDED), ctx(), Refusal.SUPERSEDED),
        (action(status=Status.EXPIRED), ctx(), Refusal.EXPIRED),
        (action(status=Status.INVALIDATED), ctx(), Refusal.PRECONDITION_CHANGED),
        (action(status=Status.SUCCEEDED), ctx(), Refusal.ALREADY_RESOLVED),
        (action(status=Status.EXECUTING), ctx(), Refusal.ALREADY_RESOLVED),
    ],
)
def test_refusals(a: PendingAction, c: ToolContext, reason: Refusal) -> None:
    result = decide(a, "confirm", c, NOW)
    assert isinstance(result, Rejection)
    assert result.reason is reason


def test_a_tap_satisfies_spend_and_unrecoverable():
    assert decide(action(risk=Risk.SPEND), "confirm", ctx(via="tap"), NOW) is Status.APPROVED
    assert decide(action(recoverable=False), "confirm", ctx(via="tap"), NOW) is Status.APPROVED


def test_expiry_is_by_wall_clock_not_turns():
    assert decide(action(), "confirm", ctx(turn=50), NOW + EXPIRY_MS - 1) is Status.APPROVED
    late = decide(action(), "confirm", ctx(), NOW + EXPIRY_MS)
    assert isinstance(late, Rejection) and late.reason is Refusal.EXPIRED


def test_cancel_is_allowed_on_the_same_turn_but_only_from_the_session():
    assert decide(action(), "cancel", ctx(turn=1), NOW) is Status.REJECTED
    other = decide(action(), "cancel", ctx(session_id="s2"), NOW)
    assert isinstance(other, Rejection) and other.reason is Refusal.WRONG_SESSION


def test_confirm_tool_has_no_argument_fields_but_the_id():
    for d in (CONFIRM_TOOL, CANCEL_TOOL):
        schema = d.input_schema
        assert set(schema["properties"]) == {"pending_id"}
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["pending_id"]


def test_a_tap_on_a_widget_ask_can_never_approve():
    # A page she read could make her write an ask that says "Yes, go ahead" (D5).
    refused = decide(action(), "confirm", ctx(via="ask"), NOW)
    assert refused == Rejection(Refusal.ASK, "a tap on a widget can't approve; use the card")


def test_a_tap_on_a_widget_ask_can_still_cancel():
    assert decide(action(), "cancel", ctx(via="ask"), NOW) is Status.REJECTED
