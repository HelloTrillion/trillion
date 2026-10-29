"""Shares apply live (the shared folders spec): a change reaches the very next tool
call, a card approved after its folder was removed or set to read only is refused when
it would run, her prompt lists the shares by name, and `--grant` folders are this run's."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from trillion import paths
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.secrets.store import MemoryStore


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    runtime = await open_runtime(
        share_rules=lambda _p: None, persona=tmp_path, secrets=MemoryStore(), env={}
    )
    yield runtime
    await runtime.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "Website"
    folder.mkdir()
    return folder


def create(folder: Path, call_id: str) -> list[Any]:
    return use("file_create", {"path": str(folder / "a.txt"), "content": "x"}, call_id)


async def started(rt: Runtime, *replies: list[Any]) -> tuple[Session, Page, ScriptedProvider]:
    provider = ScriptedProvider(*replies)
    rt.provider = provider
    page = Page(rt)
    return await Session.start(rt, page.send, None), page, provider


async def ask(session: Session, text: str) -> None:
    await session.handle({"type": "turn.text", "text": text})
    await idle(session)


async def tap(session: Session, page: Page) -> dict[str, Any]:
    (card,) = page.all("confirm.request")
    await session.handle({"type": "confirm", "pending_id": card["pending_id"]})
    await idle(session)
    (resolved,) = page.all("confirm.resolved")
    return resolved


async def test_a_share_added_mid_session_reaches_the_next_call(rt: Runtime, project: Path) -> None:
    session, page, _ = await started(
        rt, create(project, "c1"), say("I can't."), create(project, "c2"), say("Shall I?")
    )
    await ask(session, "make a file")
    assert page.all("confirm.request") == []  # not shared yet: refused at the floor
    rt.shares.put(project, access="change")
    await ask(session, "try again")
    assert len(page.all("confirm.request")) == 1
    await session.close()


async def test_a_card_approved_after_its_share_was_removed_is_refused(
    rt: Runtime, project: Path
) -> None:
    share = rt.shares.put(project, access="change")
    session, page, _ = await started(rt, create(project, "c1"), say("Shall I?"), say("Not done."))
    await ask(session, "make a file")
    rt.shares.remove(share.id)
    resolved = await tap(session, page)
    assert resolved["status"] == "refused"
    assert "outside the folders the user granted" in resolved["detail"]
    assert not (project / "a.txt").exists()
    await session.close()


async def test_a_card_approved_after_its_share_became_read_only_is_refused(
    rt: Runtime, project: Path
) -> None:
    share = rt.shares.put(project, access="change")
    session, page, _ = await started(rt, create(project, "c1"), say("Shall I?"), say("Not done."))
    await ask(session, "make a file")
    rt.shares.change(share.id, access="read")
    resolved = await tap(session, page)
    assert resolved["status"] == "refused"
    assert resolved["detail"] == "that folder is shared read only"
    assert not (project / "a.txt").exists()
    await session.close()


async def test_her_prompt_lists_the_shares_in_scope_by_name_each_turn(
    rt: Runtime, project: Path, tmp_path: Path
) -> None:
    notes = tmp_path / "Notes"
    notes.mkdir()
    rt.shares.put(project, name="Website project", access="change")
    rt.shares.put(notes, name="Notes", access="read", scope="routine:a1b2c3d4")
    session, _, provider = await started(rt, say("Hi."), say("Hi again."))
    await ask(session, "hello")
    first = provider.calls[0][0][0].text
    assert f"- Website project: {project} (read and change)" in first
    assert "Notes" not in first  # one routine's share is not hers
    rt.shares.put(notes, name="Notes", access="read")
    await ask(session, "hello again")
    second = provider.calls[-1][0][0].text
    assert f"- Notes: {notes} (read only)" in second
    await session.close()


def test_the_prompt_spells_a_folder_under_home_from_the_tilde() -> None:
    from trillion.persona.prompt import Identity, SharedFolder, system_blocks

    folder = Path.home() / "Documents" / "Clients" / "Website"
    (block,) = system_blocks(
        Identity(), "", [SharedFolder(name="Website project", path=folder, read_only=False)]
    )
    spelled = str(Path("~") / "Documents" / "Clients" / "Website")
    assert f"- Website project: {spelled} (read and change)" in block.text


async def test_grant_folders_are_shares_for_this_run_and_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    other = tmp_path / "Other"
    other.mkdir()
    rt = await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(project,),
        persona=tmp_path,
        secrets=MemoryStore(),
        env={},
    )
    try:
        (grant,) = rt.shares.all()
        assert (grant.name, grant.access, grant.run_only) == ("Website", "change", True)
        assert rt.granted_roots == (project.resolve(),)
        assert not paths.folders_config_path().exists()
        rt.shares.put(other, name="Other")
        saved = json.loads(paths.folders_config_path().read_text(encoding="utf-8"))
        assert [s["name"] for s in saved["shares"]] == ["Other"]
    finally:
        await rt.close()
