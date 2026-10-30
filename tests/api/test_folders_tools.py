"""Her folder tools (the shared folders spec, D2, D3, D5, D7): she browses only when the
user asked, sees folder names and nothing else, proposes a share on a card only a tap
approves, checked again when it is approved, and narrows a share at once."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from tests.scheduler.conftest import schedule
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.files import browse
from trillion.files.shares import routine_scope
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import ToolCall, ToolContext, Via
from trillion.tools.folders import Look, View, near_home

ROUTINE_ID = "a1b2c3d4"


def _judge(p: Path) -> str | None:
    # Stands in for D4 on a temporary folder: a hidden folder is never shared.
    if any(part.startswith(".") for part in p.parts):
        return "hidden folders, and folders inside them, are never shared"
    return None


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A fake home: folders, a file, a hidden folder and a `.ssh`."""
    h = tmp_path.resolve() / "Users" / "sam"
    for name in ("Desktop", "Documents/Clients/Website", "Documents/Taxes", ".hidden", ".ssh"):
        (h / name).mkdir(parents=True)
    (h / "Documents" / "notes.txt").write_text("not a folder")
    return h


@pytest.fixture
async def rt(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "data"))
    # Her card spells a folder from `~`, as the user would say it.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    runtime = await open_runtime(
        share_rules=_judge, persona=tmp_path, secrets=MemoryStore(), env={}
    )

    def fence(shared: Sequence[Path]) -> browse.Fence:
        return browse.Fence(
            home=home, data=home / ".trillion", shared=tuple(shared), hidden=lambda _p: False
        )

    def finder(h: Path) -> browse.Found:
        return [browse.Place("Documents", h / "Documents", "folder")], []

    runtime.folder_look = Look(fence=fence, home=home, finder=finder)
    yield runtime
    await runtime.close()


class Panel:
    """A page's folder browser, as the tools see it."""

    def __init__(self) -> None:
        self.folder_place: Path | None = None
        self.views: list[View] = []

    async def show_folders(self, view: View) -> None:
        self.folder_place = view.place
        self.views.append(view)


def ctx(via: Via = "text", *, turn: int = 1, read_outside: bool = False) -> ToolContext:
    return ToolContext(
        owner_id="local",
        session_id="s1",
        turn=turn,
        call_id=f"c{turn}",
        via=via,
        read_outside=read_outside,
    )


@pytest.fixture
def panel(rt: Runtime) -> Panel:
    p = Panel()
    rt.by_conversation["s1"] = p
    return p


async def call(rt: Runtime, name: str, args: dict[str, Any], c: ToolContext | None = None) -> Any:
    return await rt.router.dispatch(ToolCall(name=name, args=args), c or ctx())


async def started(rt: Runtime, *replies: list[Any]) -> tuple[Session, Page, ScriptedProvider]:
    provider = ScriptedProvider(*replies)
    rt.provider = provider
    page = Page(rt)
    return await Session.start(rt, page.send, None), page, provider


async def ask(session: Session, text: str) -> None:
    await session.handle({"type": "turn.text", "text": text})
    await idle(session)


# --- folders_browse (D5, D7) --------------------------------------------------


@pytest.mark.parametrize("via", ["schedule", "routine", "ask", "refresh", "channel"])
async def test_browsing_is_refused_unless_the_user_spoke_or_typed(
    rt: Runtime, panel: Panel, home: Path, via: Via
) -> None:
    r = await call(rt, "folders_browse", {"place": str(home)}, ctx(via))
    assert r.status == "refused"
    assert panel.views == [] and panel.folder_place is None


async def test_browsing_is_refused_after_the_turn_read_something_from_outside(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    r = await call(rt, "folders_browse", {"place": str(home)}, ctx(read_outside=True))
    assert r.status == "refused"
    assert "read" in r.detail
    assert panel.views == []


async def test_browsing_shows_folder_names_only(rt: Runtime, panel: Panel, home: Path) -> None:
    r = await call(rt, "folders_browse", {"place": str(home / "Documents")})
    assert r.status == "succeeded"
    assert "untrusted_tool_result" in r.content  # names are the disk's, not the user's
    assert "Clients" in r.content and "Taxes" in r.content
    assert "notes.txt" not in r.content
    (view,) = panel.views
    assert view.frame == {
        "path": str(home / "Documents"),
        "crumbs": [
            {"label": "Home", "path": str(home)},
            {"label": "Documents", "path": str(home / "Documents")},
        ],
        "folders": ["Clients", "Taxes"],
        "more": 0,
        "shared": None,
        "places": None,
    }
    assert panel.folder_place == home / "Documents"


async def test_browsing_moves_by_name_and_up_and_to_the_places(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    await call(rt, "folders_browse", {"place": "places"})
    (places,) = panel.views
    assert places.frame["path"] is None and places.frame["folders"] == []
    assert [p["label"] for p in places.frame["places"]] == ["Home", "Documents"]
    await call(rt, "folders_browse", {"place": "Documents"})
    await call(rt, "folders_browse", {"place": "clients"})
    assert panel.folder_place == home / "Documents" / "Clients"
    await call(rt, "folders_browse", {"place": "up"})
    assert panel.folder_place == home / "Documents"


async def test_a_folder_past_the_fence_is_refused_in_words(
    rt: Runtime, panel: Panel, home: Path, tmp_path: Path
) -> None:
    r = await call(rt, "folders_browse", {"place": str(tmp_path)})
    assert r.status in ("refused", "failed")
    assert browse.OUTSIDE in r.content + r.detail
    assert panel.views == []


async def test_a_shared_folder_is_tagged_in_the_frame(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    website = home / "Documents" / "Clients" / "Website"
    share = rt.shares.put(website, name="Website project", access="change")
    await call(rt, "folders_browse", {"place": str(website)})
    assert panel.views[-1].frame["shared"] == share.id


async def test_the_page_gets_the_folders_frame_from_her_turn(rt: Runtime, home: Path) -> None:
    session, page, _ = await started(
        rt, use("folders_browse", {"place": str(home / "Documents")}), say("Here they are.")
    )
    await ask(session, "show me my Documents")
    (frame,) = page.all("folders")
    assert frame["type"] == "folders"
    assert frame["folders"] == ["Clients", "Taxes"]
    assert session.folder_place == home / "Documents"
    await session.close()


# --- folder_share (D2, D4) ----------------------------------------------------


async def card(rt: Runtime, panel: Panel, args: dict[str, Any]) -> str:
    r = await call(rt, "folder_share", args)
    assert r.status == "confirmation_required", r
    assert r.pending_id is not None
    await rt.router.mark_delivered(r.pending_id, "local", "web")
    return str(r.pending_id)


async def test_a_share_is_a_card_with_the_exact_path(rt: Runtime, panel: Panel, home: Path) -> None:
    website = home / "Documents" / "Clients" / "Website"
    r = await call(
        rt, "folder_share", {"path": str(website), "name": "Website project", "access": "change"}
    )
    assert r.status == "confirmation_required"
    assert near_home(website) == f"~{os.sep}{Path('Documents', 'Clients', 'Website')}"
    assert r.summary == (
        f"Share {near_home(website)} with Trillion as Website project, read and change, "
        "for everyone"
    )
    assert rt.shares.all() == []


async def test_the_card_defaults_to_read_for_everyone_named_after_the_folder(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    taxes = home / "Documents" / "Taxes"
    r = await call(rt, "folder_share", {"path": str(taxes)})
    assert r.summary == f"Share {near_home(taxes)} with Trillion as Taxes, read only, for everyone"


async def test_a_card_too_long_to_show_whole_is_refused(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    deep = home.joinpath(*["a-long-folder-name"] * 12)
    deep.mkdir(parents=True)
    r = await call(rt, "folder_share", {"path": str(deep)})
    assert r.status == "refused"
    assert "too long" in r.detail


async def test_a_share_needs_a_tap_and_a_spoken_yes_is_refused(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    website = home / "Documents" / "Clients" / "Website"
    pid = await card(rt, panel, {"path": str(website), "access": "change"})
    spoken = await rt.router.resolve(pid, "confirm", ctx("voice", turn=2))
    assert spoken.status == "refused" and spoken.reason is not None
    assert spoken.reason.value == "tap_required"
    assert rt.shares.all() == []
    tapped = await rt.router.resolve(pid, "confirm", ctx("tap", turn=2))
    assert tapped.status == "succeeded", tapped
    (share,) = rt.shares.all()
    assert share.path == website and share.access == "change" and share.scope == "everyone"


async def test_a_hidden_folder_is_refused_when_she_asks(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    r = await call(rt, "folder_share", {"path": str(home / ".hidden")})
    assert r.status == "refused"
    assert "hidden folders" in r.detail


async def test_a_folder_swapped_for_a_link_to_ssh_before_the_tap_is_refused(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    website = home / "Documents" / "Clients" / "Website"
    pid = await card(rt, panel, {"path": str(website), "access": "change"})
    website.rmdir()
    try:
        website.symlink_to(home / ".ssh", target_is_directory=True)
    except OSError:
        pytest.skip("this system can't make a link here")
    r = await rt.router.resolve(pid, "confirm", ctx("tap", turn=2))
    assert r.status == "refused"
    assert rt.shares.all() == []


async def test_a_card_approved_after_its_folder_was_removed_is_refused(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    taxes = home / "Documents" / "Taxes"
    pid = await card(rt, panel, {"path": str(taxes)})
    shutil.rmtree(taxes)
    r = await rt.router.resolve(pid, "confirm", ctx("tap", turn=2))
    assert r.status == "refused"
    assert "not found" in r.detail
    assert rt.shares.all() == []


async def test_a_share_for_one_routine_names_it_by_its_title(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    assert rt.schedules is not None
    await rt.schedules.create(schedule(kind="ask", title="Monday revenue", target="own"))
    taxes = home / "Documents" / "Taxes"
    pid = await card(rt, panel, {"path": str(taxes), "scope": "monday revenue"})
    a = await rt.spine_store.get_pending(pid)
    assert a is not None and a.summary.endswith("for the routine Monday revenue")
    r = await rt.router.resolve(pid, "confirm", ctx("tap", turn=2))
    assert r.status == "succeeded", r
    (share,) = rt.shares.all()
    assert share.scope == routine_scope(ROUTINE_ID)


async def test_a_routine_no_one_has_is_refused_in_words(
    rt: Runtime, panel: Panel, home: Path
) -> None:
    r = await call(rt, "folder_share", {"path": str(home / "Desktop"), "scope": "Daily news"})
    assert r.status == "refused"
    assert "no routine called Daily news" in r.detail


async def test_this_is_the_folder_the_browser_shows(rt: Runtime, panel: Panel, home: Path) -> None:
    r = await call(rt, "folder_share", {"path": "this"})
    assert r.status == "refused"
    assert "isn't showing a folder" in r.detail
    clients = home / "Documents" / "Clients"
    await call(rt, "folders_browse", {"place": str(clients)})
    r = await call(rt, "folder_share", {"path": "this"})
    # The card must name the folder itself, so she is told which one to name.
    assert r.status == "refused"
    assert near_home(clients) in r.detail and "folder_share" in r.detail


# --- folder_unshare (D3) ------------------------------------------------------


async def test_unsharing_never_asks(rt: Runtime, panel: Panel, home: Path) -> None:
    rt.shares.put(home / "Desktop", name="Desk", access="change")
    r = await call(rt, "folder_unshare", {"name": "desk", "access": "read"})
    assert r.status == "succeeded", r
    assert r.pending_id is None
    (share,) = rt.shares.all()
    assert share.access == "read"
    r = await call(rt, "folder_unshare", {"name": "Desk"}, ctx(read_outside=True))
    assert r.status == "succeeded", r
    assert rt.shares.all() == []


async def test_unsharing_narrows_to_one_routine(rt: Runtime, panel: Panel, home: Path) -> None:
    assert rt.schedules is not None
    await rt.schedules.create(schedule(kind="ask", title="Monday revenue", target="own"))
    rt.shares.put(home / "Desktop", name="Desk", access="change")
    r = await call(rt, "folder_unshare", {"name": "Desk", "scope": "Monday revenue"})
    assert r.status == "succeeded", r
    (share,) = rt.shares.all()
    assert share.scope == routine_scope(ROUTINE_ID) and share.access == "change"


async def test_unsharing_refuses_to_widen(rt: Runtime, panel: Panel, home: Path) -> None:
    rt.shares.put(home / "Desktop", name="Desk", access="read", scope=routine_scope(ROUTINE_ID))
    for args in ({"name": "Desk", "access": "change"}, {"name": "Desk", "scope": "everyone"}):
        r = await call(rt, "folder_unshare", args)
        assert r.status == "refused"
        assert "folder_share" in r.detail
    (share,) = rt.shares.all()
    assert share.access == "read" and share.scope == routine_scope(ROUTINE_ID)


async def test_unsharing_a_name_no_share_has_is_refused(rt: Runtime, panel: Panel) -> None:
    r = await call(rt, "folder_unshare", {"name": "Nothing"})
    assert r.status == "refused"
    assert "no shared folder is called Nothing" in r.detail


# --- the panel's click (the host note) -----------------------------------------


async def test_a_click_in_the_panel_tells_her_where_the_browser_is(rt: Runtime, home: Path) -> None:
    session, page, provider = await started(rt, say("That one?"), say("Still there."))
    clients = home / "Documents" / "Clients"
    view = await session.browsed(str(clients))
    assert view.problem is None
    (frame,) = page.all("folders")
    assert frame["path"] == str(clients) and frame["folders"] == ["Website"]
    assert session.folder_place == clients
    await ask(session, "share this one")
    sent = json.dumps([str(m) for m in provider.calls[0][1]])
    assert "The folder browser shows" in sent and "Clients" in sent
    await ask(session, "and now?")
    newest = str(provider.calls[1][1][-1])
    assert "The folder browser shows" not in newest  # said once, on the next turn only
    await session.close()


async def test_a_click_past_the_fence_moves_nothing(
    rt: Runtime, home: Path, tmp_path: Path
) -> None:
    session, page, _ = await started(rt)
    view = await session.browsed(str(tmp_path))
    assert view.problem == browse.OUTSIDE
    assert page.all("folders") == [] and session.folder_place is None
    await session.close()
