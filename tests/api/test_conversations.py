"""The thread list's API: list, search, rename, delete and undo (SUR-1)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from typing import Any

from tests.api.test_server import ORIGIN, App, home, make  # noqa: F401
from tests.brain.fakes import say
from trillion.api.runtime import OWNER
from trillion.brain.types import Msg, TextBlock

PATH = "/api/conversations"


def seed(app: App, cid: str, at: int, text: str) -> None:
    portal = app.client.portal
    assert portal is not None
    store = app.rt.conversations
    portal.call(store.create, cid, OWNER, at)
    portal.call(
        store.append, cid, OWNER, "user", Msg(role="user", blocks=(TextBlock(text=text),)), at
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
    assert body["conversations"][0] == {"id": "b", "title": "second", "last_message_at": 2000}
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
