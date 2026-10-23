"""Settings → Skills reads and changes /api/skills (CON-6)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401

PATH = "/api/skills"


def skill(root: Path, name: str, extra: str = "") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Does {name}.\n"
        f"allowed-tools: web_fetch Bash\nlicense: MIT\n---\nSteps.\n",
        encoding="utf-8",
    )
    if extra:
        (folder / extra).write_text("print(1)", encoding="utf-8")
    return folder


def find(body: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in body["skills"] if s["name"] == name)


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401
    assert app.client.patch(PATH, json={"id": "curated:x", "on": True}).status_code == 401


def test_a_fresh_install_has_no_folders_and_nothing_on(make: Any) -> None:
    app: App = make()
    body = app.client.get(PATH, headers=app.auth).json()
    assert body["folders"] == [] and body["unlisted"] == 0
    assert {s["state"] for s in body["skills"]} <= {"off"}
    # What a skill is for, never its instructions.
    assert all("instructions" not in s for s in body["skills"])
    assert app.rt.facts()["skills"] == "none turned on"


def test_add_a_folder_switch_a_skill_and_see_it_in_her_prompt(make: Any, tmp_path: Path) -> None:
    app: App = make()
    mine = tmp_path / "mine"
    folder = skill(mine, "pdf-forms", "fill.py")
    r = app.client.post(f"{PATH}/folders", json={"path": str(mine)}, headers=app.auth)
    assert r.status_code == 200
    assert r.json()["folders"] == [{"path": str(mine.resolve()), "found": True}]
    entry = find(r.json(), "pdf-forms")
    assert entry["has_scripts"] is True and entry["state"] == "off"
    assert entry["license"] == "MIT" and entry["description"] == "Does pdf-forms."
    assert entry["folder"] == str(mine.resolve())
    assert entry["fingerprint"].startswith("sha256:")
    assert entry["tools"] == [
        {"name": "web_fetch", "available": True},
        {"name": "Bash", "available": False},
    ]
    r = app.client.patch(
        PATH,
        json={"id": entry["id"], "on": True, "fingerprint": entry["fingerprint"]},
        headers=app.auth,
    )
    assert r.status_code == 200 and find(r.json(), "pdf-forms")["state"] == "on"
    assert app.rt.skills is not None
    assert [label for label, _ in app.rt.skills.prompt_lines()] == ["pdf-forms"]
    assert "pdf-forms" in app.rt.facts()["skills"]

    # Its files change: off until the user looks again, then on with the new files.
    (folder / "SKILL.md").write_text("---\nname: pdf-forms\ndescription: x\n---\nNew.", "utf-8")
    again = find(app.client.get(PATH, headers=app.auth).json(), "pdf-forms")
    assert again["state"] == "changed" and again["fingerprint"] != entry["fingerprint"]
    assert app.rt.skills.prompt_lines() == []
    r = app.client.patch(
        PATH,
        json={"id": again["id"], "on": True, "fingerprint": again["fingerprint"]},
        headers=app.auth,
    )
    assert r.status_code == 200 and find(r.json(), "pdf-forms")["state"] == "on"

    r = app.client.patch(PATH, json={"id": again["id"], "on": False}, headers=app.auth)
    assert r.status_code == 200 and find(r.json(), "pdf-forms")["state"] == "off"
    r = app.client.request("DELETE", f"{PATH}/folders", json={"path": str(mine)}, headers=app.auth)
    assert r.status_code == 200 and all(s["source"] == "curated" for s in r.json()["skills"])
    assert r.json()["folders"] == []


def test_turning_on_refuses_files_that_changed_since_the_page_showed_them(
    make: Any, tmp_path: Path
) -> None:
    app: App = make()
    mine = tmp_path / "mine"
    folder = skill(mine, "alpha")
    shown = find(
        app.client.post(f"{PATH}/folders", json={"path": str(mine)}, headers=app.auth).json(),
        "alpha",
    )
    (folder / "notes.md").write_text("Added after the card was shown.", encoding="utf-8")
    r = app.client.patch(
        PATH,
        json={"id": shown["id"], "on": True, "fingerprint": shown["fingerprint"]},
        headers=app.auth,
    )
    assert r.status_code == 400
    assert r.json() == {
        "error": "refused",
        "detail": "that skill changed since this page showed it; look again",
    }
    assert find(app.client.get(PATH, headers=app.auth).json(), "alpha")["state"] == "off"


def test_an_invalid_skill_is_a_card_with_its_reason(make: Any, tmp_path: Path) -> None:
    app: App = make()
    broken = tmp_path / "mine" / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("---\nname: broken\n---\nNo description.", "utf-8")
    body = app.client.post(
        f"{PATH}/folders", json={"path": str(broken.parent)}, headers=app.auth
    ).json()
    card = find(body, "broken")
    assert card["state"] == "invalid" and card["reason"] and card["fingerprint"] == ""
    r = app.client.patch(PATH, json={"id": card["id"], "on": True}, headers=app.auth)
    assert r.status_code == 400 and "can't be used" in r.json()["detail"]


def test_a_missing_folder_is_reported_not_dropped(make: Any, tmp_path: Path) -> None:
    app: App = make()
    mine = tmp_path / "mine"
    skill(mine, "alpha")
    app.client.post(f"{PATH}/folders", json={"path": str(mine)}, headers=app.auth)
    (mine / "alpha" / "SKILL.md").unlink()
    (mine / "alpha").rmdir()
    mine.rmdir()
    body = app.client.get(PATH, headers=app.auth).json()
    assert body["folders"] == [{"path": str(mine.resolve()), "found": False}]


def test_refusals_come_back_in_words(make: Any, tmp_path: Path, home: Path) -> None:
    app: App = make()
    r = app.client.post(f"{PATH}/folders", json={"path": str(tmp_path / "x")}, headers=app.auth)
    assert r.status_code == 400 and "not a folder" in r.json()["detail"]
    assert r.json()["error"] == "refused"
    # Trillion's own data folder, or one that holds it.
    r = app.client.post(f"{PATH}/folders", json={"path": str(home)}, headers=app.auth)
    assert r.status_code == 400 and "Trillion's own files" in r.json()["detail"]
    r = app.client.patch(PATH, json={"id": "folder:/nowhere", "on": True}, headers=app.auth)
    assert r.status_code == 400 and r.json()["detail"] == "there is no such skill"
    r = app.client.patch(PATH, json={"id": "", "on": True}, headers=app.auth)
    assert r.status_code == 422


def test_the_page_says_how_many_on_are_past_her_list(make: Any, tmp_path: Path) -> None:
    app: App = make()
    mine = tmp_path / "mine"
    for i in range(8):
        folder = mine / f"long-{i}"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: long-{i}\ndescription: {'d' * 1000}\n---\nSteps.", "utf-8"
        )
    body = app.client.post(f"{PATH}/folders", json={"path": str(mine)}, headers=app.auth).json()
    for s in body["skills"]:
        if s["source"] == "folder":
            app.client.patch(PATH, json={"id": s["id"], "on": True}, headers=app.auth)
    assert app.client.get(PATH, headers=app.auth).json()["unlisted"] == 3
