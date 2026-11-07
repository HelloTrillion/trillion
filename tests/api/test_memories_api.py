"""Settings → Memories: read and edit through /api/memories, forget only on a card (MEM-6)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from trillion import paths
from trillion.memory.types import Memory

PATH = "/api/memories"


def put(app: App, text: str, *, source: str = "agent_inference", kind: str = "user") -> Memory:
    portal = app.client.portal
    assert portal is not None

    async def write() -> Memory:
        done = await app.rt.memory.write(
            type=kind,  # type: ignore[arg-type]
            text=text,
            source=source,  # type: ignore[arg-type]
            moves=False,
            now_ms=app.rt.clock(),
        )
        return done.memory

    return portal.call(write)


def listed(app: App) -> list[dict[str, Any]]:
    body = app.client.get(PATH, headers=app.auth).json()
    rows: list[dict[str, Any]] = body["memories"]
    return rows


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401
    assert app.client.put(f"{PATH}/mem_0123456789ab", json={}).status_code == 401


def test_it_lists_what_is_current_newest_first_with_where_it_came_from(make: Any) -> None:
    app: App = make()
    older = put(app, "I take my coffee black.", source="user_statement")
    newer = put(app, "The project files live in the shared drive.", kind="reference")
    gone = put(app, "I live in a flat.")
    app.client.portal.call(app.rt.memory.forget, gone.id, app.rt.clock())  # type: ignore[union-attr]
    body = app.client.get(PATH, headers=app.auth).json()
    assert [r["id"] for r in body["memories"]] == [newer.id, older.id]
    first = body["memories"][1]
    assert first["type"] == "user" and first["source"] == "user_statement"
    assert first["text"] == "I take my coffee black." and first["version"] == 1
    assert first["created_ms"] == older.created_at and first["supersedes"] is None
    assert body["max_text"] == 1200 and body["diverged"] is False


def test_an_edit_is_the_users_own_word_and_keeps_the_old_version(make: Any) -> None:
    app: App = make()
    old = put(app, "I take my coffee black.")
    milk = {"text": "I take my coffee with milk.", "version": 1}
    r = app.client.put(f"{PATH}/{old.id}", headers=app.auth, json=milk)
    assert r.status_code == 200
    body = r.json()
    (now,) = body["memories"]
    assert now["id"] == body["id"] != old.id
    assert now["text"] == "I take my coffee with milk." and now["supersedes"] == old.id
    # Whoever said it first, what the user typed here is what the user said (MEM-6a).
    assert now["source"] == "user_statement"
    # The original is kept, marked as replaced, file and all (MEM-7, SPN-9).
    kept = app.rt.memory.peek(old.id)
    assert kept is not None and kept.status == "superseded" and kept.superseded_by == now["id"]
    assert kept.text == "I take my coffee black."
    on_disk = (paths.memory_dir() / "user" / f"{old.id}.md").read_text(encoding="utf-8")
    assert "status: superseded" in on_disk and "I take my coffee black." in on_disk


def test_bad_edits_are_refused_plainly(make: Any) -> None:
    app: App = make()
    m = put(app, "I take my coffee black.")
    edit = lambda memory_id, **body: app.client.put(  # noqa: E731
        f"{PATH}/{memory_id}", headers=app.auth, json={"version": 1} | body
    )
    assert edit(m.id, text="  I take my coffee black.  ").json() == {"error": "unchanged"}
    assert edit(m.id, text="   ").json() == {"error": "empty"}
    assert edit(m.id, text="x" * 1300).json() == {"error": "too_long"}
    assert edit(m.id, text="tea", version=2).status_code == 409
    assert edit("mem_000000000000", text="tea").status_code == 404
    assert edit("not-an-id", text="tea").status_code == 422
    assert [r["id"] for r in listed(app)] == [m.id]


def test_there_is_no_route_that_forgets(make: Any) -> None:
    app: App = make()
    m = put(app, "I take my coffee black.")
    for method in ("DELETE", "POST"):
        r = app.client.request(method, f"{PATH}/{m.id}", headers=app.auth, json={})
        assert r.status_code == 405
    assert app.rt.memory.count() == 1


def test_forget_from_the_page_raises_her_card_and_runs_only_on_a_tap(make: Any) -> None:
    app: App = make()
    m = put(app, "I take my coffee black.")
    with app.connect() as chat:
        chat.ws.send_json({"type": "memory.forget", "id": m.id})
        seen = chat.until("memory.asked")
        (card,) = [s for s in seen if s["type"] == "confirm.request"]
        asked = seen[-1]
        # The same host-composed card her own memory_forget raises (MEM-3).
        assert card["tool"] == "memory_forget"
        assert card["summary"] == "Forget: “I take my coffee black.”"
        assert asked["status"] == "confirmation_required"
        assert asked["pending_id"] == card["pending_id"]
        assert app.rt.memory.count() == 1  # nothing has happened yet

        resolved = chat.tap("confirm", card["pending_id"])
        assert resolved["status"] == "succeeded"
    # Soft underneath: the file stays, marked (SPN-9).
    kept = app.rt.memory.peek(m.id)
    assert kept is not None and kept.status == "retracted"
    assert (paths.memory_dir() / "user" / f"{m.id}.md").is_file()
    assert listed(app) == []
    # She was not asked to say anything about it: the page raised it, not her.
    assert app.rt.provider.calls == []  # type: ignore[union-attr]
    # The decision record has it, from proposal to the tap (SPN-17).
    rows = app.rows("SELECT pending_id, event, via FROM decisions ORDER BY id")
    events = [(event, via) for pid, event, via in rows if pid == card["pending_id"]]
    assert ("proposed", "tap") in events and any(e == "approved" for e, _ in events)


def test_declining_the_card_forgets_nothing(make: Any) -> None:
    app: App = make()
    m = put(app, "I take my coffee black.")
    with app.connect() as chat:
        chat.ws.send_json({"type": "memory.forget", "id": m.id})
        pid = chat.until("memory.asked")[-1]["pending_id"]
        assert chat.tap("cancel", pid)["status"] == "cancelled"
    assert app.rt.memory.count() == 1 and [r["id"] for r in listed(app)] == [m.id]


def test_forgetting_what_is_not_there_raises_no_card(make: Any) -> None:
    app: App = make()
    m = put(app, "I take my coffee black.")
    app.client.portal.call(app.rt.memory.forget, m.id, app.rt.clock())  # type: ignore[union-attr]
    with app.connect() as chat:
        chat.ws.send_json({"type": "memory.forget", "id": m.id})
        seen = chat.until("memory.asked")
        assert [s["type"] for s in seen].count("confirm.request") == 0
        assert seen[-1]["status"] == "refused" and seen[-1]["pending_id"] is None
        chat.ws.send_json({"type": "memory.forget", "id": "../user/x"})
        assert chat.until("error")[-1]["code"] == "bad_message"
