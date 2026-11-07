"""Settings → Confirmations reads the decision record through /api/confirmations (SUR-7).

Every line is read from the record (SPN-17, SPN-17a), the route only reads, and the
arguments it sends are the frozen slim copy (SPN-17c).
"""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from typing import Any

from tests.api.test_server import App, home, make, only, propose  # noqa: F401
from tests.brain.fakes import say
from trillion.api.confirmations import DAY_MS, create_confirmations_router
from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.store import DecisionIn
from trillion.tools.record import NOT_SENT

PATH = "/api/confirmations"
CONV = "c0ffee00-0000-4000-8000-000000000001"


def by(event: str, via: str = "tap", who: str = CONV) -> DecisionIn:
    return DecisionIn(event=event, decided_by=who, via=via)


def seed(
    app: App,
    pid: str,
    *,
    steps: tuple[tuple[Status, Status, DecisionIn], ...] = (),
    shown: bool = True,
    ago_ms: int = 60_000,
    expires_in_ms: int = 600_000,
    tool: str = "file_create",
    summary: str = "Create a note in Projects",
    args: dict[str, Any] | None = None,
    detail: str | None = None,
) -> None:
    """An action in the record, made up and generic, moved through `steps`."""
    portal, store = app.client.portal, app.rt.spine_store
    assert portal is not None
    now = app.rt.clock()
    at = now - ago_ms
    a = PendingAction(
        id=pid,
        owner_id="local",
        tool=tool,
        args=args or {"path": "/projects/note.txt", "content": "hello"},
        args_hash="h-" + pid,
        summary=summary,
        target="/projects/note.txt",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id=CONV,
        created_turn=1,
        status=Status.PROPOSED,
        created_at=at,
        expires_at=at + expires_in_ms,
        represented_from=None,
    )
    portal.call(store.insert_pending, a, by("proposed", "text"))
    if shown:
        portal.call(store.mark_delivered, pid, "local", "web", at + 1_000)
    for i, (old, new, d) in enumerate(steps):
        last = i == len(steps) - 1
        moved = portal.call(
            lambda o=old, n=new, d=d, i=i, last=last: store.transition(
                pid,
                o,
                n,
                now_ms=at + 2_000 + i * 1_000,
                decision=d,
                outcome_detail=detail if last else None,
            )
        )
        assert moved


def get(app: App, **params: Any) -> dict[str, Any]:
    r = app.client.get(PATH, params=params, headers=app.auth)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["id"]: a for a in body["actions"]}


APPROVED = (Status.PROPOSED, Status.APPROVED, by("approved"))
EXECUTING = (Status.APPROVED, Status.EXECUTING, by("executing"))


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401
    wrong = {"authorization": "Bearer nope"}
    assert app.client.get(PATH, headers=wrong).status_code == 401


def test_a_card_approved_by_tap_reads_shown_answered_and_done(make: Any) -> None:
    app: App = make()
    assert app.provider is not None
    app.provider._replies += [propose(app), say("Shall I?"), say("Done.")]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        chat.until("turn.complete")
        conversation = chat.session["id"]
    (row,) = get(app)["actions"]
    assert row["id"] == card["pending_id"]
    assert row["tool"] == "file_create" and row["tool_label"] == "Create a file"
    # The host's frozen summary, the same words the card showed (SPN-2a).
    assert row["summary"] == card["summary"]
    assert row["status"] == "succeeded" and row["decision"] == "approved"
    assert row["delivered_at"] is not None and row["surface"] == "web"
    assert row["decided_at"] >= row["delivered_at"] >= row["created_at"]
    assert row["channel"] == "tap" and row["decided_by"] == conversation
    assert row["decided_by_kind"] == "asking_conversation"
    assert row["finished_at"] >= row["decided_at"]
    assert [h["event"] for h in row["history"]] == [
        "proposed",
        "approved",
        "executing",
        "succeeded",
    ]
    # Settled: there is no card to go to.
    assert row["conversation_id"] is None


def test_a_card_still_waiting_points_at_its_conversation(make: Any) -> None:
    app: App = make()
    assert app.provider is not None
    app.provider._replies += [propose(app), say("Shall I?")]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        conversation = chat.session["id"]
    (row,) = get(app, status="pending")["actions"]
    assert row["id"] == card["pending_id"] and row["status"] == "pending"
    assert row["decided_at"] is None and row["channel"] is None
    assert row["conversation_id"] == conversation


def test_every_status_reads_as_the_user_would(make: Any) -> None:
    app: App = make()
    seed(app, "p-waiting", ago_ms=1_000)
    seed(app, "p-unseen", shown=False, ago_ms=2_000)
    seed(app, "p-approved", steps=(APPROVED,), ago_ms=3_000)
    seed(app, "p-running", steps=(APPROVED, EXECUTING), ago_ms=4_000)
    seed(
        app,
        "p-unknown",
        steps=(APPROVED, EXECUTING, (Status.EXECUTING, Status.UNKNOWN, by("unknown"))),
        detail="the provider did not answer",
        ago_ms=5_000,
    )
    seed(
        app,
        "p-failed",
        steps=(APPROVED, EXECUTING, (Status.EXECUTING, Status.FAILED, by("failed"))),
        detail="the folder is read only",
        ago_ms=6_000,
    )
    seed(
        app,
        "p-no",
        steps=((Status.PROPOSED, Status.REJECTED, by("rejected", "voice")),),
        ago_ms=7_000,
    )
    # Past its time with nobody answering, and nothing has swept it yet.
    seed(app, "p-lapsed", shown=False, ago_ms=900_000)
    seed(
        app,
        "p-expired",
        steps=((Status.PROPOSED, Status.EXPIRED, by("expired", "text", "system")),),
        ago_ms=8_000,
    )
    seed(
        app,
        "p-replaced",
        steps=((Status.PROPOSED, Status.SUPERSEDED, by("superseded")),),
        ago_ms=9_000,
    )
    seed(
        app,
        "p-not-sent",
        steps=(
            APPROVED,
            EXECUTING,
            (Status.EXECUTING, Status.INVALIDATED, by(NOT_SENT, "text", "system")),
        ),
        detail="not_sent",
        ago_ms=10_000,
    )
    body = get(app)
    rows = by_id(body)
    assert [a["id"] for a in body["actions"]] == [
        "p-waiting",
        "p-unseen",
        "p-approved",
        "p-running",
        "p-unknown",
        "p-failed",
        "p-no",
        "p-expired",
        "p-replaced",
        "p-not-sent",
        "p-lapsed",
    ]
    status = {k: v["status"] for k, v in rows.items()}
    assert status == {
        "p-waiting": "pending",
        "p-unseen": "pending",
        "p-approved": "approved",
        "p-running": "executing",
        "p-unknown": "unknown",
        "p-failed": "failed",
        "p-no": "rejected",
        "p-lapsed": "expired",
        "p-expired": "expired",
        "p-replaced": "cancelled",
        "p-not-sent": "cancelled",
    }
    # Never shown is said as such: no time is made up for it (SPN-17).
    assert rows["p-unseen"]["delivered_at"] is None and rows["p-waiting"]["delivered_at"]
    assert rows["p-unknown"]["detail"] == "the provider did not answer"
    assert rows["p-unknown"]["finished_at"] is not None
    assert rows["p-failed"]["detail"] == "the folder is read only"
    assert rows["p-no"]["channel"] == "voice" and rows["p-no"]["decision"] == "rejected"
    assert rows["p-lapsed"]["decided_at"] is None
    assert rows["p-expired"]["decided_by_kind"] == "system"
    assert rows["p-replaced"]["cancelled_reason"] == "superseded"
    assert rows["p-not-sent"]["cancelled_reason"] == "not_sent"
    assert rows["p-not-sent"]["decision"] == "approved"
    # Seeded rows name a conversation that does not exist here: nothing to go to.
    assert rows["p-waiting"]["conversation_id"] is None
    assert body["counts"] == {
        "pending": 2,
        "approved": 1,
        "executing": 1,
        "succeeded": 0,
        "failed": 1,
        "unknown": 1,
        "rejected": 1,
        "expired": 2,
        "cancelled": 2,
        "all": 11,
    }


def test_it_filters_by_status_and_by_date_and_stops_at_the_limit(make: Any) -> None:
    app: App = make()
    seed(app, "p-new", ago_ms=1_000)
    seed(
        app,
        "p-unknown",
        steps=(APPROVED, EXECUTING, (Status.EXECUTING, Status.UNKNOWN, by("unknown"))),
        ago_ms=2_000,
    )
    seed(app, "p-old", steps=(APPROVED,), ago_ms=10 * DAY_MS)
    assert [a["id"] for a in get(app, status="unknown")["actions"]] == ["p-unknown"]
    body = get(app, days=7)
    assert [a["id"] for a in body["actions"]] == ["p-new", "p-unknown"]
    # The counts are for the window, whatever the status filter.
    assert body["counts"]["all"] == 2 and body["days"] == 7
    assert get(app, days=7, status="approved")["actions"] == []
    assert [a["id"] for a in get(app, days=30, status="approved")["actions"]] == ["p-old"]
    assert [a["id"] for a in get(app, limit=1)["actions"]] == ["p-new"]


def test_a_bad_filter_is_refused(make: Any) -> None:
    app: App = make()
    for params in (
        {"status": "proposed"},
        {"status": "nope"},
        {"days": 0},
        {"days": 366},
        {"limit": 501},
    ):
        r = app.client.get(PATH, params=params, headers=app.auth)
        assert r.status_code == 422 and r.json()["error"] == "bad_request", params


def test_long_arguments_arrive_slim_and_the_full_ones_are_never_read(make: Any) -> None:
    app: App = make()
    long = "a private paragraph " * 40
    seed(app, "p-long", args={"path": "/projects/long.txt", "content": long})
    r = app.client.get(PATH, headers=app.auth)
    assert "a private paragraph" not in r.text
    (row,) = r.json()["actions"]
    assert row["args"]["path"] == "/projects/long.txt"
    omitted = row["args"]["content"]["$omitted"]
    assert omitted["chars"] == len(long) and len(omitted["sha256"]) == 64


def test_nothing_can_be_approved_from_here(make: Any) -> None:
    app: App = make()
    seed(app, "p-waiting")
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in (PATH, f"{PATH}/p-waiting", f"{PATH}/p-waiting/confirm"):
            for body in ({}, {"pending_id": "p-waiting"}, {"event": "confirm"}):
                r = app.client.request(method, path, json=body, headers=app.auth)
                assert r.status_code in (404, 405), (method, path, r.status_code)
    (row,) = get(app)["actions"]
    assert row["status"] == "pending" and row["decided_at"] is None
    assert [h["event"] for h in row["history"]] == ["proposed"]


def test_the_router_has_one_route_and_it_only_reads(make: Any) -> None:
    app: App = make()
    routes = create_confirmations_router(app.rt).routes
    assert [(getattr(r, "path", ""), getattr(r, "methods", None)) for r in routes] == [
        (PATH, {"GET"})
    ]
