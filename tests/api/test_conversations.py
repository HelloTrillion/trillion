"""The thread list's API: list, search, rename, delete and undo (SUR-1)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from tests.api.test_server import ORIGIN, App, home, make  # noqa: F401
from tests.brain.fakes import say
from trillion import paths
from trillion.api.runtime import OWNER
from trillion.brain.types import Msg, TextBlock

PATH = "/api/conversations"


def seed(app: App, cid: str, at: int, text: str, owner: str = OWNER) -> None:
    portal = app.client.portal
    assert portal is not None
    store = app.rt.conversations
    portal.call(store.create, cid, owner, at)
    portal.call(
        store.append, cid, owner, "user", Msg(role="user", blocks=(TextBlock(text=text),)), at
    )


def write(app: App, method: str, path: str, **kw: Any) -> Any:
    # A write needs our origin and a JSON body (SPN-11), even an empty one.
    return app.client.request(method, path, headers=app.auth | ORIGIN, **({"json": {}} | kw))


def test_lists_newest_first(make) -> None:
    app = make(model=False)
    seed(app, "a", 1000, "first")
    seed(app, "b", 2000, "second")
    r = app.client.get(PATH, headers=app.auth)
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body["conversations"]] == ["b", "a"]
    assert body["conversations"][0] == {
        "id": "b",
        "title": "second",
        "last_message_at": 2000,
        "kind": "chat",
        "day": None,
    }
    assert body["more"] is False


def test_search_and_paging(make) -> None:
    app = make(model=False)
    for n in range(3):
        seed(app, f"c{n}", 1000 + n, f"trip {n}")
    r = app.client.get(PATH, params={"q": "trip", "limit": 2}, headers=app.auth).json()
    assert [c["id"] for c in r["conversations"]] == ["c2", "c1"] and r["more"]
    page = {"before": 1001, "before_id": "c1", "limit": 2}
    r = app.client.get(PATH, params=page, headers=app.auth).json()
    assert [c["id"] for c in r["conversations"]] == ["c0"]


def test_needs_the_token(make) -> None:
    app = make(model=False)
    assert app.client.get(PATH).status_code == 401
    assert (
        app.client.delete(
            f"{PATH}/a", headers=ORIGIN | {"content-type": "application/json"}
        ).status_code
        == 401
    )


def test_a_write_needs_json(make) -> None:
    app = make(model=False)
    seed(app, "a", 1000, "first")
    r = app.client.delete(f"{PATH}/a", headers=app.auth | ORIGIN)
    assert r.status_code == 415
    assert len(app.client.get(PATH, headers=app.auth).json()["conversations"]) == 1


def test_rename(make) -> None:
    app = make(model=False)
    seed(app, "a", 1000, "first")
    r = write(app, "PATCH", f"{PATH}/a", json={"title": "  The   trip "})
    assert r.status_code == 200 and r.json() == {"id": "a", "title": "The trip"}
    listed = app.client.get(PATH, headers=app.auth).json()["conversations"]
    assert listed[0]["title"] == "The trip"
    assert write(app, "PATCH", f"{PATH}/a", json={"title": "  "}).status_code == 400
    assert write(app, "PATCH", f"{PATH}/nope", json={"title": "x"}).json() == {"error": "not_found"}


def test_a_scheduled_conversation_is_not_there(make) -> None:
    app = make(model=False)
    portal = app.client.portal
    assert portal is not None
    portal.call(app.rt.conversations.create, "s", OWNER, 1000, "scheduled")
    assert write(app, "PATCH", f"{PATH}/s", json={"title": "x"}).status_code == 404
    assert write(app, "DELETE", f"{PATH}/s").status_code == 404
    assert write(app, "POST", f"{PATH}/s/restore").status_code == 404


def test_delete_and_restore(make) -> None:
    app = make(model=False)
    seed(app, "a", 1000, "first")
    assert write(app, "DELETE", f"{PATH}/a").status_code == 200
    assert app.client.get(PATH, headers=app.auth).json()["conversations"] == []
    assert write(app, "DELETE", f"{PATH}/a").status_code == 404
    assert write(app, "POST", f"{PATH}/a/restore").status_code == 200
    assert len(app.client.get(PATH, headers=app.auth).json()["conversations"]) == 1


def test_deleting_an_open_conversation_tells_its_page(make) -> None:
    app = make(say("Hi."), say("Greetings"))
    with app.connect() as chat:
        cid = chat.session["id"]
        chat.say("hello")
        assert write(app, "DELETE", f"{PATH}/{cid}").status_code == 200
        assert chat.until("error")[-1]["code"] == "deleted"
        # Her session is gone: it stays out of the live ones and takes no more input.
        assert cid not in app.rt.by_conversation
        before = app.rows("SELECT count(*) FROM messages")
        chat.ws.send_json({"type": "turn.text", "text": "anyone there?"})
        chat.ws.send_json({"type": "turn.text", "text": "still?"})
        assert app.rows("SELECT count(*) FROM messages") == before
    with app.connect(resume=cid) as chat:
        assert chat.session["id"] != cid


def test_renaming_an_open_conversation_tells_its_page(make) -> None:
    app = make(model=False)
    with app.connect() as chat:
        cid = chat.session["id"]
        write(app, "PATCH", f"{PATH}/{cid}", json={"title": "Mine"})
        assert chat.until("conversation.title")[-1] == {
            "type": "conversation.title",
            "id": cid,
            "title": "Mine",
        }


def test_another_owners_conversation_is_not_there(make) -> None:
    app = make(model=False)
    with closing(sqlite3.connect(paths.db_path())) as db, db:
        db.execute("INSERT INTO owners (id, created_at) VALUES ('someone-else', 0)")
    seed(app, "x", 1000, "theirs", owner="someone-else")
    assert app.client.get(PATH, headers=app.auth).json()["conversations"] == []
    assert write(app, "PATCH", f"{PATH}/x", json={"title": "mine now"}).status_code == 404
    assert write(app, "DELETE", f"{PATH}/x").status_code == 404
    assert write(app, "POST", f"{PATH}/x/restore").status_code == 404


def test_the_query_is_bounded(make) -> None:
    app = make(model=False)
    for params in (
        {"limit": 0},
        {"limit": 101},
        {"before": -1, "before_id": "a"},
        {"before": 2**63, "before_id": "a"},
        {"q": "x" * 201},
    ):
        r = app.client.get(PATH, params=params, headers=app.auth)
        assert r.status_code == 422, params
        assert r.json()["error"] == "bad_request"


def test_a_cursor_needs_its_id(make) -> None:
    app = make(model=False)
    r = app.client.get(PATH, params={"before": 1000}, headers=app.auth)
    assert r.status_code == 400 and r.json() == {"error": "bad_request"}


def test_a_title_must_be_a_bounded_string(make) -> None:
    app = make(model=False)
    seed(app, "a", 1000, "first")
    for title in (7, None, ["x"], "y" * 501):
        r = write(app, "PATCH", f"{PATH}/a", json={"title": title})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"


def test_a_deleted_conversation_cannot_be_renamed_and_a_live_one_is_not_restored(make) -> None:
    app = make(model=False)
    seed(app, "a", 1000, "first")
    assert write(app, "POST", f"{PATH}/a/restore").status_code == 404
    assert write(app, "DELETE", f"{PATH}/a").status_code == 200
    assert write(app, "PATCH", f"{PATH}/a", json={"title": "x"}).status_code == 404


# ── Your day (SUR-1) ─────────────────────────────────────────────────────


def test_today_is_made_once_then_the_same(make) -> None:
    from trillion.persona import day

    app = make(model=False)
    at = 1_793_000_000_000  # 26 Oct 2026, 07:33 UTC
    app.rt.clock = lambda: at
    first = app.client.get(f"{PATH}/day", headers=app.auth)
    assert first.status_code == 200 and first.json()["created"] is True
    cid = first.json()["id"]
    again = app.client.get(f"{PATH}/day", headers=app.auth).json()
    assert again == {"id": cid, "created": False}
    portal = app.client.portal
    assert portal is not None
    date = day.today(at, app.rt.zone())
    title = portal.call(app.rt.conversations.title_of, cid, OWNER)
    assert title == (day.title(date), "user") and title[0].startswith("Your day · ")
    # Listed once something is said there, with its kind and date.
    reply = Msg(role="assistant", blocks=(TextBlock(text="Your day is clear."),))
    portal.call(app.rt.conversations.append, cid, OWNER, "assistant", reply, at)
    (row,) = app.client.get(PATH, headers=app.auth).json()["conversations"]
    assert row["kind"] == "day" and row["day"] == date.isoformat() and row["id"] == cid
    # The next day is another conversation.
    app.rt.clock = lambda: at + 86_400_000
    tomorrow = app.client.get(f"{PATH}/day", headers=app.auth).json()
    assert tomorrow["created"] is True and tomorrow["id"] != cid


def test_today_needs_the_token(make) -> None:
    app = make(model=False)
    assert app.client.get(f"{PATH}/day").status_code == 401
