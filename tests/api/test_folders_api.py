"""Settings → Shared folders and the folder browser's clicks (the shared folders spec):
/api/folders lists, adds, changes and removes shares as the user's own act, D4 holds
on the route, and /api/folders/browse keeps the browser's fence and shows names only."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import App, home, make  # noqa: F401
from tests.scheduler.conftest import schedule
from trillion.files import browse
from trillion.files.shares import Shares, routine_scope
from trillion.tools.folders import Look

PATH = "/api/folders"
BROWSE = "/api/folders/browse"
ROUTINE_ID = "a1b2c3d4"


def _judge(p: Path) -> str | None:
    # Stands in for D4 on a temporary folder: a hidden folder is never shared.
    if any(part.startswith(".") for part in p.parts):
        return "hidden folders, and folders inside them, are never shared"
    return None


@pytest.fixture
def app(make: Any, home: Path) -> App:
    built: App = make()
    user = home.resolve() / "Users" / "sam"
    for name in ("Desktop", "Documents/Clients", "Documents/Taxes", ".ssh"):
        (user / name).mkdir(parents=True)
    (user / "Documents" / "notes.txt").write_text("not a folder")
    rt = built.rt
    # The same store the process has, judged by a rule a temporary folder can pass.
    rt.shares = Shares(rt.shares.path, rules=_judge)

    def fence(shared: Sequence[Path]) -> browse.Fence:
        return browse.Fence(
            home=user, data=user / ".trillion", shared=tuple(shared), hidden=lambda _p: False
        )

    rt.folder_look = Look(
        fence=fence,
        home=user,
        finder=lambda h: ([browse.Place("Documents", h / "Documents", "folder")], []),
    )
    built.user = user  # type: ignore[attr-defined]
    return built


def user(app: App) -> Path:
    return app.user  # type: ignore[attr-defined,no-any-return]


def get(app: App) -> dict[str, Any]:
    r = app.client.get(PATH, headers=app.auth)
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def post(app: App, url: str, body: dict[str, Any]) -> Any:
    return app.client.post(url, json=body, headers=app.auth)


def delete(app: App, url: str) -> Any:
    # A write is JSON, as every other route's (SPN-11).
    return app.client.request("DELETE", url, headers=app.auth, json={})


def test_every_route_needs_the_session_token(app: App) -> None:
    assert app.client.get(PATH).status_code == 401
    assert app.client.post(PATH, json={"path": "/x"}).status_code == 401
    assert app.client.request("DELETE", f"{PATH}/f_abc", json={}).status_code == 401
    assert app.client.post(BROWSE, json={"place": "places"}).status_code == 401


def test_a_folder_is_added_changed_and_removed(app: App) -> None:
    taxes = user(app) / "Documents" / "Taxes"
    body = post(app, PATH, {"path": str(taxes), "name": "Taxes"}).json()
    (row,) = body["shares"]
    assert row == {
        "id": row["id"],
        "name": "Taxes",
        "path": str(taxes),
        "access": "read",
        "scope": "everyone",
        "routine": None,
        "state": None,
        "run_only": False,
    }
    assert body["problem"] is None
    # The user's own act: widening needs no card.
    body = post(app, PATH, {"id": row["id"], "access": "change"}).json()
    assert body["shares"][0]["access"] == "change"
    body = delete(app, f"{PATH}/{row['id']}").json()
    assert [s for s in body["shares"] if not s["run_only"]] == []


def test_d4_holds_on_the_route(app: App) -> None:
    r = post(app, PATH, {"path": str(user(app) / ".ssh")})
    assert r.status_code == 400
    assert r.json() == {
        "error": "refused",
        "detail": "hidden folders, and folders inside them, are never shared",
    }
    r = post(app, PATH, {"path": "Documents"})
    assert r.json()["detail"] == "a folder must be given as a full path"


def test_a_share_for_a_routine_shows_its_title_and_an_unknown_one_is_refused(app: App) -> None:
    rt = app.rt
    assert rt.schedules is not None and app.client.portal is not None
    app.client.portal.call(
        rt.schedules.create, schedule(kind="ask", title="Monday revenue", target="own")
    )
    desk = user(app) / "Desktop"
    body = post(app, PATH, {"path": str(desk), "scope": routine_scope(ROUTINE_ID)}).json()
    row = next(s for s in body["shares"] if s["path"] == str(desk))
    assert row["routine"] == {"id": ROUTINE_ID, "title": "Monday revenue"}
    r = post(app, PATH, {"id": row["id"], "scope": "routine:ffff0000"})
    assert r.status_code == 400 and r.json()["detail"] == "that routine was not found"


def test_deleting_a_routine_removes_its_shares(app: App) -> None:
    rt = app.rt
    assert rt.schedules is not None and app.client.portal is not None
    app.client.portal.call(
        rt.schedules.create, schedule(kind="ask", title="Monday revenue", target="own")
    )
    desk = user(app) / "Desktop"
    post(app, PATH, {"path": str(desk), "scope": routine_scope(ROUTINE_ID)})
    assert delete(app, f"/api/routines/{ROUTINE_ID}").status_code == 200
    assert [s for s in get(app)["shares"] if s["path"] == str(desk)] == []


def test_a_routines_share_can_be_removed_by_itself(app: App) -> None:
    rt = app.rt
    assert rt.schedules is not None and app.client.portal is not None
    app.client.portal.call(
        rt.schedules.create, schedule(kind="ask", title="Monday revenue", target="own")
    )
    desk = user(app) / "Desktop"
    body = post(app, PATH, {"path": str(desk), "scope": routine_scope(ROUTINE_ID)}).json()
    row = next(s for s in body["shares"] if s["path"] == str(desk))
    body = delete(app, f"{PATH}/{row['id']}").json()
    assert all(s["id"] != row["id"] for s in body["shares"])


def test_a_missing_folder_is_shown_not_found(app: App) -> None:
    taxes = user(app) / "Documents" / "Taxes"
    post(app, PATH, {"path": str(taxes)})
    taxes.rmdir()
    row = next(s for s in get(app)["shares"] if s["path"] == str(taxes))
    assert row["state"] == "not found"


def test_a_damaged_file_is_shown_and_never_overwritten(app: App) -> None:
    store = app.rt.shares.path
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json", encoding="utf-8")
    body = get(app)
    assert body["problem"] == (
        f"Your shared folders file is damaged, so no folders are shared. It is at {store}."
    )
    r = post(app, PATH, {"path": str(user(app) / "Desktop")})
    assert r.status_code == 400 and "damaged" in r.json()["detail"]
    assert store.read_text(encoding="utf-8") == "{not json"


def test_a_grant_share_is_shown_this_run_only_and_removable_for_the_run(app: App) -> None:
    grant = app.rt.shares.add_run_only(user(app) / "Desktop")
    row = next(s for s in get(app)["shares"] if s["id"] == grant.id)
    assert row["run_only"] is True and row["access"] == "change"
    body = delete(app, f"{PATH}/{grant.id}").json()
    assert all(s["id"] != grant.id for s in body["shares"])
    assert not app.rt.shares.path.exists()  # never written


def test_removing_a_share_no_one_has_is_not_found(app: App) -> None:
    r = delete(app, f"{PATH}/f_nothing")
    assert r.status_code == 404 and r.json() == {"error": "not_found"}


def test_add_or_change_says_which(app: App) -> None:
    r = post(app, PATH, {"name": "x"})
    assert r.status_code == 400


# --- the browse route ----------------------------------------------------------


def test_browsing_shows_folder_names_only(app: App) -> None:
    docs = user(app) / "Documents"
    r = post(app, BROWSE, {"path": str(docs)})
    assert r.status_code == 200
    frame = r.json()
    assert frame["type"] == "folders" and frame["folders"] == ["Clients", "Taxes"]
    assert frame["places"] is None and frame["path"] == str(docs)
    assert "notes.txt" not in r.text


def test_browsing_the_places_and_by_name_from_there(app: App) -> None:
    frame = post(app, BROWSE, {"place": "places"}).json()
    assert frame["path"] is None
    assert [p["label"] for p in frame["places"]] == ["Home", "Documents"]
    assert post(app, BROWSE, {"place": "Documents"}).json()["path"] == str(user(app) / "Documents")


def test_the_browse_route_keeps_the_fence(app: App, home: Path) -> None:
    r = post(app, BROWSE, {"path": str(home)})
    assert r.status_code == 400 and r.json()["detail"] == browse.OUTSIDE
    r = post(app, BROWSE, {"path": str(user(app) / ".ssh")})
    assert r.status_code == 400
    assert post(app, BROWSE, {}).status_code == 400


def test_a_click_moves_the_pages_browser_and_tells_her(app: App) -> None:
    clients = user(app) / "Documents" / "Clients"
    with app.connect() as chat:
        conversation = chat.session["id"]
        r = post(app, BROWSE, {"path": str(clients.parent), "conversation": conversation})
        assert r.status_code == 200
        # Then a name, from where that page's browser now is.
        r = post(app, BROWSE, {"place": "Clients", "conversation": conversation})
        assert r.json()["path"] == str(clients)
        session = app.rt.by_conversation[conversation]
        assert session.folder_place == clients
        assert "folder browser now shows" in (session._folder_note or "")
        frames = chat.until("folders")
        assert frames[-1]["type"] == "folders"
