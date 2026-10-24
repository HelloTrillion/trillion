"""Settings → Reminders reads and cancels through /api/schedules (AGT-4, AGT-9)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import time
from functools import partial
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from tests.scheduler.conftest import ms, schedule
from trillion.scheduler.when import Repeat

PATH = "/api/schedules"


def put(app: App, **kw: Any) -> None:
    assert app.rt.schedules is not None and app.client.portal is not None
    app.client.portal.call(app.rt.schedules.create, schedule(**kw))


def checked(app: App) -> dict[str, Any]:
    """The state, once the scheduler's first check (run as the server starts) is done."""
    deadline = time.monotonic() + 10
    while True:
        body: dict[str, Any] = app.client.get(PATH, headers=app.auth).json()
        if body["checked_ms"] is not None or time.monotonic() > deadline:
            return body
        time.sleep(0.02)


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401
    assert app.client.request("DELETE", f"{PATH}/a1b2c3d4", json={}).status_code == 401


def test_it_lists_what_is_coming_up_and_when_it_last_checked(make: Any) -> None:
    app: App = make()
    put(
        app,
        next_due_ms=ms("2099-10-26T09:00"),
        at_local="2099-10-26T09:00",
        repeat=Repeat(every="weekday"),
        kind="ask",
        title="Urgent email",
    )
    body = checked(app)
    (row,) = body["active"]
    assert row["title"] == "Urgent email" and row["kind"] == "ask"
    assert row["when"] == "Every weekday at 9:00 am" and row["repeat"] == "every weekday"
    assert row["next"] == "Mon 26 Oct, 9:00 am" and row["last"] is None
    assert body["recent"] == [] and body["tick_s"] == 15.0
    assert body["checked_ms"] is not None and body["now_ms"] >= body["checked_ms"]


def test_recent_results_carry_their_status_and_due_time(make: Any) -> None:
    app: App = make()
    s = app.rt.schedules
    assert s is not None and app.client.portal is not None
    portal = app.client.portal
    # Its own scheduler stopped, so this test is the only one moving the run along.
    portal.call(app.rt.stop_background)
    due = ms("2026-10-24T16:00")
    put(app, next_due_ms=due)
    assert portal.call(s.enqueue, "a1b2c3d4", due, None, 10.0, due)
    run = portal.call(s.claim, "w", due, due)
    assert run is not None
    portal.call(partial(s.finish, run.id, "missed", due + 1, detail="not running"))
    body = app.client.get(PATH, headers=app.auth).json()
    assert body["active"] == []
    (r,) = body["recent"]
    assert r["status"] == "missed" and r["due_ms"] == due and r["title"] == "Call the dentist"
    assert r["detail"] == "not running" and r["kind"] == "reminder"


def test_cancel_removes_it_and_an_unknown_one_is_not_found(make: Any) -> None:
    app: App = make()
    put(app, next_due_ms=ms("2099-10-26T09:00"), at_local="2099-10-26T09:00")
    gone = app.client.request("DELETE", f"{PATH}/a1b2c3d4", headers=app.auth, json={})
    assert gone.status_code == 200 and gone.json()["active"] == []
    again = app.client.request("DELETE", f"{PATH}/a1b2c3d4", headers=app.auth, json={})
    assert again.status_code == 404
    bad = app.client.request("DELETE", f"{PATH}/..%2Fx", headers=app.auth, json={})
    assert bad.status_code in (404, 422)


def test_cancel_without_a_json_body_is_refused(make: Any) -> None:
    app: App = make()
    put(app, next_due_ms=ms("2099-10-26T09:00"), at_local="2099-10-26T09:00")
    assert app.client.request("DELETE", f"{PATH}/a1b2c3d4", headers=app.auth).status_code == 415
    assert app.client.get(PATH, headers=app.auth).json()["active"] != []
