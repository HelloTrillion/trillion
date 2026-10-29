"""Settings → Reminders → Routines: list, pause, resume and delete through /api/routines."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from functools import partial
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from tests.scheduler.conftest import FakeClock, ms, schedule
from trillion.files.shares import routine_scope
from trillion.scheduler.when import Repeat

PATH = "/api/routines"
RID = "b1b2c3d4"


def put(app: App, **kw: Any) -> None:
    assert app.rt.schedules is not None and app.client.portal is not None
    app.client.portal.call(app.rt.schedules.create, schedule(**kw))


def routine(app: App, **kw: Any) -> None:
    put(
        app,
        **{
            "id": RID,
            "kind": "ask",
            "title": "Monday revenue",
            "body": "Show me last week's revenue.",
            "at_local": "2099-10-26T08:00",
            "repeat": Repeat(every="weekday"),
            "next_due_ms": ms("2099-10-26T08:00"),
            "target": "own",
            "conversation_id": "conv-1",
            "notes": "Leave out refunds.",
            **kw,
        },
    )


def stopped(app: App, now: int) -> None:
    """Its own scheduler stopped, and the clock set: only this test moves anything."""
    assert app.client.portal is not None
    app.client.portal.call(app.rt.stop_background)
    app.rt.clock = FakeClock(now)


def tap(app: App, method: str, path: str) -> Any:
    # A body, so the request is JSON like every other change (SPN-11).
    return app.client.request(method, path, headers=app.auth, json={})


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401
    assert app.client.request("POST", f"{PATH}/{RID}/pause", json={}).status_code == 401
    assert app.client.request("DELETE", f"{PATH}/{RID}", json={}).status_code == 401


def test_it_lists_each_routine_in_words_and_never_a_plain_reminder(make: Any) -> None:
    app: App = make()
    stopped(app, ms("2099-10-24T12:00"))
    routine(app)
    put(app, next_due_ms=ms("2099-10-26T09:00"), at_local="2099-10-26T09:00")
    body = app.client.get(PATH, headers=app.auth).json()
    (row,) = body["routines"]
    assert row == {
        "id": RID,
        "name": "Monday revenue",
        "when": "Every weekday at 8:00 am",
        "next_ms": ms("2099-10-26T08:00"),
        "next": "Mon 26 Oct, 8:00 am",
        "target": "own",
        "conversation_id": "conv-1",
        "paused": False,
        "notes": "Leave out refunds.",
    }


def test_a_routine_is_not_also_listed_among_reminders(make: Any) -> None:
    app: App = make()
    stopped(app, ms("2099-10-24T12:00"))
    routine(app)
    put(app, next_due_ms=ms("2099-10-26T09:00"), at_local="2099-10-26T09:00")
    active = app.client.get("/api/schedules", headers=app.auth).json()["active"]
    assert [a["id"] for a in active] == ["a1b2c3d4"]


def test_pause_stops_its_runs_and_resume_goes_on_from_the_next_time_after_now(
    make: Any,
) -> None:
    app: App = make()
    s, sched, portal = app.rt.schedules, app.rt.scheduler, app.client.portal
    assert s is not None and sched is not None and portal is not None
    stopped(app, ms("2099-10-24T12:00"))
    routine(app)
    paused = tap(app, "POST", f"{PATH}/{RID}/pause")
    assert paused.status_code == 200 and paused.json()["routines"][0]["paused"] is True
    # Paused through Monday and Tuesday morning: nothing is due.
    app.rt.clock = FakeClock(ms("2099-10-27T12:00"))
    assert portal.call(s.due, app.rt.clock()) == []
    resumed = tap(app, "POST", f"{PATH}/{RID}/resume")
    assert resumed.status_code == 200
    (row,) = resumed.json()["routines"]
    # Never the times it was paused through: the next one after now.
    assert row["paused"] is False and row["next_ms"] == ms("2099-10-28T08:00")


def test_pausing_twice_or_resuming_what_runs_changes_nothing(make: Any) -> None:
    app: App = make()
    stopped(app, ms("2099-10-24T12:00"))
    routine(app)
    assert tap(app, "POST", f"{PATH}/{RID}/resume").json()["routines"][0]["paused"] is False
    assert tap(app, "POST", f"{PATH}/{RID}/pause").status_code == 200
    again = tap(app, "POST", f"{PATH}/{RID}/pause")
    assert again.status_code == 200 and again.json()["routines"][0]["paused"] is True


def test_delete_cancels_it_and_a_second_delete_is_harmless(make: Any) -> None:
    app: App = make()
    s, portal = app.rt.schedules, app.client.portal
    assert s is not None and portal is not None
    stopped(app, ms("2099-10-24T12:00"))
    routine(app)
    gone = tap(app, "DELETE", f"{PATH}/{RID}")
    assert gone.status_code == 200 and gone.json()["routines"] == []
    kept = portal.call(s.get, RID)
    assert kept is not None and kept.status == "cancelled" and kept.next_due_ms is None
    again = tap(app, "DELETE", f"{PATH}/{RID}")
    assert again.status_code == 200 and again.json()["routines"] == []
    # Cancelled stays cancelled: it can't be paused or resumed back to life.
    assert tap(app, "POST", f"{PATH}/{RID}/resume").status_code == 404


def test_what_is_not_a_routine_is_refused(make: Any) -> None:
    app: App = make()
    stopped(app, ms("2099-10-24T12:00"))
    put(app, next_due_ms=ms("2099-10-26T09:00"), at_local="2099-10-26T09:00")
    for method, path in (
        ("POST", f"{PATH}/a1b2c3d4/pause"),
        ("POST", f"{PATH}/a1b2c3d4/resume"),
        ("DELETE", f"{PATH}/a1b2c3d4"),
        ("POST", f"{PATH}/ffffffff/pause"),
        ("DELETE", f"{PATH}/ffffffff"),
    ):
        assert tap(app, method, path).status_code == 404, path
    assert tap(app, "DELETE", f"{PATH}/..%2Fx").status_code in (404, 422)
    # The plain reminder is untouched.
    assert app.client.get("/api/schedules", headers=app.auth).json()["active"] != []


def test_a_routine_deleted_while_it_runs_says_nothing_when_it_ends(make: Any) -> None:
    app: App = make()
    s, portal = app.rt.schedules, app.client.portal
    assert s is not None and portal is not None
    due = ms("2099-10-26T08:00")
    stopped(app, due)
    routine(app)
    assert portal.call(s.enqueue, RID, due, due + 86_400_000, 300.0, due)
    run = portal.call(s.claim, "w", due, due)
    assert run is not None
    assert tap(app, "DELETE", f"{PATH}/{RID}").status_code == 200
    n = portal.call(
        partial(
            s.finish_with_notice,
            run.id,
            "delivered",
            due + 1000,
            owner_id="local",
            source_id=RID,
            kind="ready",
            title="Your Monday revenue is ready",
            text="Your Monday revenue is ready.",
            due_ms=due,
            late=False,
            conversation_id="conv-1",
        )
    )
    assert n is None and portal.call(s.held, "local") == []


def test_deleting_a_routine_removes_its_shared_folders_and_no_others(make: Any) -> None:
    app: App = make()
    stopped(app, ms("2099-10-24T12:00"))
    routine(app)
    mine, everyone = app.granted.parent / "mine", app.granted.parent / "everyone"
    mine.mkdir()
    everyone.mkdir()
    app.rt.shares.put(mine, name="Reports", scope=routine_scope(RID))
    app.rt.shares.put(everyone, name="Website")
    assert tap(app, "DELETE", f"{PATH}/{RID}").status_code == 200
    assert [s.name for s in app.rt.shares.all() if not s.run_only] == ["Website"]
