"""The shares the user made, kept in folders.json (the shared folders spec, D1, D9, D10)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from trillion import paths
from trillion.files import rules
from trillion.files.shares import EVERYONE, Share, ShareError, Shares, routine_scope, widens


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / ".trillion").mkdir(parents=True)
    return h


@pytest.fixture
def store(home: Path) -> Shares:
    def judge(p: Path) -> str | None:
        return rules.why_not(
            p,
            home=home,
            data_dir=home / ".trillion",
            protected_roots=(),
            mounts=(),
            system=(),
            hidden=lambda _p: False,
        )

    return Shares(home / ".trillion" / "folders.json", rules=judge, clock=lambda: 1_000)


def folder(home: Path, *names: str) -> Path:
    p = home.joinpath(*names)
    p.mkdir(parents=True, exist_ok=True)
    return p


def on_disk(store: Shares) -> dict:
    return json.loads(store.path.read_text(encoding="utf-8"))


# ── Add, change, remove ─────────────────────────────────────────────


def test_a_share_is_added_and_written_whole(store, home):
    web = folder(home, "Documents", "Website")
    share = store.put(web, name="Website project", access="change")
    assert share.id.startswith("f_") and len(share.id) > 2
    assert (share.name, share.access, share.scope, share.added_ms) == (
        "Website project",
        "change",
        EVERYONE,
        1_000,
    )
    assert share.path == rules.resolved(web)
    assert on_disk(store) == {
        "version": 1,
        "shares": [
            {
                "id": share.id,
                "name": "Website project",
                "path": str(share.path),
                "access": "change",
                "scope": "everyone",
                "added_ms": 1_000,
            }
        ],
    }
    assert store.all() == [share]


def test_a_new_share_defaults_to_read_for_everyone_named_after_its_folder(store, home):
    share = store.put(folder(home, "Clients"))
    assert (share.name, share.access, share.scope) == ("Clients", "read", "everyone")


def test_a_share_is_changed_and_removed(store, home):
    share = store.put(folder(home, "Clients"))
    changed = store.change(share.id, name="Work", access="change", scope=routine_scope("r1"))
    assert (changed.id, changed.name, changed.access, changed.scope) == (
        share.id,
        "Work",
        "change",
        "routine:r1",
    )
    assert store.get(share.id) == changed
    assert store.get("work") == changed  # by name, any case
    removed = store.remove(share.id)
    assert removed == changed
    assert store.all() == []
    assert on_disk(store)["shares"] == []


def test_removing_or_changing_an_unknown_share_is_refused(store):
    with pytest.raises(ShareError):
        store.remove("f_nope")
    with pytest.raises(ShareError):
        store.change("f_nope", access="read")


def test_the_file_is_read_live(store, home):
    # Another Shares over the same file sees a change with no restart (D1).
    share = store.put(folder(home, "Clients"))
    other = Shares(store.path, rules=lambda _p: None)
    assert other.all() == [share]
    store.remove(share.id)
    assert other.all() == []


def test_a_missing_file_means_no_shares_and_no_problem(store):
    assert store.all() == []
    assert store.problem() is None
    assert store.in_scope(EVERYONE) == ((), ())


# ── Names and paths ─────────────────────────────────────────────────


def test_names_are_unique_whatever_their_case(store, home):
    store.put(folder(home, "A"), name="Website")
    with pytest.raises(ShareError, match="name"):
        store.put(folder(home, "B"), name="WEBSITE")
    other = store.put(folder(home, "C"))
    with pytest.raises(ShareError, match="name"):
        store.change(other.id, name="website")


def test_a_default_name_that_is_taken_gets_a_number(store, home):
    first = store.put(folder(home, "a", "Clients"))
    second = store.put(folder(home, "b", "Clients"))
    assert (first.name, second.name) == ("Clients", "Clients 2")


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "x" * 61, "two\nlines", "a\rb", "tab\x07", "evil\u202etxt", "zero\u200bwidth"],
)
def test_a_bad_name_is_refused(store, home, bad):
    with pytest.raises(ShareError):
        store.put(folder(home, "Clients"), name=bad)


def test_a_name_is_trimmed_and_may_be_sixty_characters(store, home):
    assert store.put(folder(home, "A"), name="  Work  ").name == "Work"
    assert store.put(folder(home, "B"), name="y" * 60).name == "y" * 60


def test_the_same_path_is_one_share_and_the_later_request_changes_it(store, home):
    web = folder(home, "Website")
    first = store.put(web, name="Site", access="read")
    again = store.put(home / "Website" / ".." / "Website", access="change")
    assert again.id == first.id and again.name == "Site" and again.access == "change"
    assert len(store.all()) == 1
    renamed = store.put(web, name="Website project")
    assert renamed.id == first.id and renamed.name == "Website project"
    # Asking again with its own name is not a clash with itself.
    assert store.put(web, name="WEBSITE PROJECT").name == "WEBSITE PROJECT"
    grant = store.add_run_only(folder(home, "Granted"))
    assert store.put(grant.path, name="Granted").id == grant.id


def test_a_path_is_kept_resolved_through_links(store, home, tmp_path):
    web = folder(home, "Website")
    shortcut = tmp_path / "shortcut"
    try:
        shortcut.symlink_to(web, target_is_directory=True)
    except OSError:
        pytest.skip("this account may not create symlinks")
    share = store.put(shortcut)
    assert share.path == rules.resolved(web)
    assert share.name == "Website"
    assert store.put(web).id == share.id


def test_a_relative_path_is_refused(store):
    with pytest.raises(ShareError, match="full path"):
        store.put(Path("Documents"))


def test_a_missing_folder_or_a_file_cannot_be_shared(store, home):
    with pytest.raises(ShareError, match="not found"):
        store.put(home / "nowhere")
    note = home / "note.txt"
    note.write_text("x", encoding="utf-8")
    with pytest.raises(ShareError, match="not a folder"):
        store.put(note)


def test_a_folder_that_can_never_be_shared_is_refused(store, home):
    with pytest.raises(ShareError, match="home"):
        store.put(home)
    with pytest.raises(ShareError):
        store.put(home / ".trillion")
    assert not store.path.exists()


@pytest.mark.parametrize("bad", ["", "all", "routine:", "routine:a b", "agent:x"])
def test_a_bad_scope_is_refused(store, home, bad):
    with pytest.raises(ShareError, match="scope"):
        store.put(folder(home, "Clients"), scope=bad)


def test_a_bad_access_is_refused(store, home):
    with pytest.raises(ShareError, match="access"):
        store.put(folder(home, "Clients"), access="write")  # type: ignore[arg-type]


# ── What a turn reaches ─────────────────────────────────────────────


def test_in_scope_gives_everyone_to_her_turns_and_adds_a_routines_own(store, home):
    shared = store.put(folder(home, "Shared"), access="change")
    notes = store.put(folder(home, "Notes"), access="read")
    mine = store.put(folder(home, "Mine"), access="change", scope=routine_scope("r1"))
    theirs = store.put(folder(home, "Theirs"), scope=routine_scope("r2"))
    roots, read_only = store.in_scope(EVERYONE)
    assert set(roots) == {shared.path, notes.path}
    assert read_only == (notes.path,)
    roots, read_only = store.in_scope(routine_scope("r1"))
    assert set(roots) == {shared.path, notes.path, mine.path}
    assert theirs.path not in roots
    assert read_only == (notes.path,)


def test_a_missing_folder_stays_listed_as_not_found_and_reaches_nothing(store, home):
    gone = folder(home, "Gone")
    share = store.put(gone)
    gone.rmdir()
    assert store.all() == [share]
    assert store.state(share) == "not found"
    assert store.in_scope(EVERYONE) == ((), ())
    # Back again, it reaches again: nothing was removed for being missing.
    gone.mkdir()
    assert store.state(share) is None
    assert store.in_scope(EVERYONE) == ((share.path,), (share.path,))
    assert on_disk(store)["shares"][0]["id"] == share.id


def test_a_hand_edited_share_of_a_refused_folder_reaches_nothing(store, home):
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "shares": [
                    {
                        "id": "f_home",
                        "name": "Everything",
                        "path": str(rules.resolved(home)),
                        "access": "change",
                        "scope": "everyone",
                        "added_ms": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (share,) = store.all()
    reason = store.state(share)
    assert reason is not None and "home" in reason
    assert store.in_scope(EVERYONE) == ((), ())


def test_removing_a_routine_removes_its_shares(store, home):
    keep = store.put(folder(home, "Keep"))
    other = store.put(folder(home, "Other"), scope=routine_scope("r2"))
    store.put(folder(home, "A"), scope=routine_scope("r1"))
    store.put(folder(home, "B"), scope=routine_scope("r1"))
    assert store.remove_routine("r1") == 2
    assert store.all() == [keep, other]
    assert store.remove_routine("r1") == 0


# ── A damaged file ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        '{"version": 2, "shares": []}',
        '{"version": 1}',
        '{"version": 1, "shares": [{"id": "f_1"}]}',
        '{"version": 1, "shares": [{"id": "f_1", "name": "a", "path": "rel", "access": "read",'
        ' "scope": "everyone", "added_ms": 1}]}',
        '{"version": 1, "shares": [{"id": "f_1", "name": "a", "path": "/x", "access": "all",'
        ' "scope": "everyone", "added_ms": 1}]}',
        b'{"version": 1, "shares": [], "x": "\xff\xfe"}',  # not UTF-8
        "[" * 100_000,  # deep enough to overflow the parser
    ],
)
def test_a_damaged_file_is_never_overwritten_and_no_shares_apply(store, home, text):
    raw = text if isinstance(text, bytes) else text.encode("utf-8")
    store.path.write_bytes(raw)
    problem = store.problem()
    assert problem is not None and "damaged" in problem and str(store.path) in problem
    assert store.all() == []
    assert store.in_scope(EVERYONE) == ((), ())
    with pytest.raises(ShareError, match="damaged"):
        store.put(folder(home, "Clients"))
    with pytest.raises(ShareError, match="damaged"):
        store.remove_routine("r1")
    assert store.path.read_bytes() == raw


def test_a_run_only_share_still_applies_while_the_file_is_damaged(store, home):
    store.path.write_text("{not json", encoding="utf-8")
    grant = store.add_run_only(folder(home, "Granted"))
    assert store.all() == [grant]
    assert store.in_scope(EVERYONE)[0] == (grant.path,)
    assert store.change(grant.id, access="read").access == "read"
    store.remove(grant.id)
    assert store.all() == []


def test_two_shares_of_one_path_in_the_file_is_damage(store, home):
    p = str(rules.resolved(folder(home, "A")))
    row = {"name": "a", "path": p, "access": "read", "scope": "everyone", "added_ms": 1}
    store.path.write_text(
        json.dumps(
            {"version": 1, "shares": [{**row, "id": "f_1"}, {**row, "id": "f_2", "name": "b"}]}
        ),
        encoding="utf-8",
    )
    assert store.problem() is not None


def test_a_write_that_does_not_read_back_is_reported(store, home, monkeypatch):
    real = json.loads

    def garbled(text, *a, **k):
        data = real(text, *a, **k)
        if isinstance(data, dict) and data.get("shares"):
            data["shares"] = []
        return data

    monkeypatch.setattr("trillion.files.shares.json.loads", garbled)
    with pytest.raises(ShareError, match="did not save"):
        store.put(folder(home, "Clients"))


def test_a_failed_write_leaves_no_temporary_file(store, home, monkeypatch):
    def boom(*_a: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("trillion.files.shares.os.replace", boom)
    with pytest.raises(OSError):
        store.put(folder(home, "Clients"))
    assert [p.name for p in store.path.parent.iterdir()] == []


# ── Run-only shares from --grant (D10) ──────────────────────────────


def test_a_run_only_share_is_listed_but_never_written(store, home):
    saved = store.put(folder(home, "Saved"))
    grant = store.add_run_only(folder(home, "Granted"))
    assert grant.run_only and grant.access == "change" and grant.scope == EVERYONE
    assert grant.name == "Granted"
    assert store.all() == [saved, grant]
    assert [s["name"] for s in on_disk(store)["shares"]] == ["Saved"]
    assert set(store.in_scope(EVERYONE)[0]) == {saved.path, grant.path}
    # Another change to the file still leaves it out.
    store.put(folder(home, "More"))
    assert "Granted" not in store.path.read_text(encoding="utf-8")


def test_a_run_only_share_alone_writes_no_file(store, home):
    store.add_run_only(folder(home, "Granted"))
    assert not store.path.exists()


def test_a_run_only_share_can_be_changed_and_removed_for_the_run(store, home):
    grant = store.add_run_only(folder(home, "Granted"))
    lowered = store.change(grant.id, access="read")
    assert lowered.run_only and lowered.access == "read"
    assert store.in_scope(EVERYONE)[1] == (grant.path,)
    store.remove(grant.id)
    assert store.all() == []
    assert not store.path.exists()


def test_a_run_only_name_that_is_taken_gets_a_number(store, home):
    store.put(folder(home, "a", "Clients"))
    assert store.add_run_only(folder(home, "b", "Clients")).name == "Clients 2"


def test_saving_a_granted_folder_makes_it_one_saved_share(store, home):
    granted = folder(home, "Granted")
    grant = store.add_run_only(granted)
    saved = store.put(granted, access="read")
    assert not saved.run_only and saved.name == "Granted" and saved.id == grant.id
    assert store.all() == [saved]


def test_a_run_only_share_still_obeys_the_rules(store, home):
    with pytest.raises(ShareError):
        store.add_run_only(home)


# ── Widening and narrowing (D3) ─────────────────────────────────────


def _share(access: str = "read", scope: str = EVERYONE) -> Share:
    return Share(
        id="f_1",
        name="a",
        path=Path("/x"),
        access=access,
        scope=scope,
        added_ms=1,  # type: ignore[arg-type]
    )


def test_a_new_share_widens():
    assert widens(None, access="read", scope=EVERYONE)


def test_saving_a_granted_folder_widens_whatever_the_access():
    grant = replace(_share("change"), run_only=True)
    assert widens(grant, access="read", scope=routine_scope("r1"))


@pytest.mark.parametrize(
    ("old", "access", "scope", "wider"),
    [
        (("read", "everyone"), "change", "everyone", True),
        (("change", "everyone"), "read", "everyone", False),
        (("read", "routine:r1"), "read", "everyone", True),
        (("read", "everyone"), "read", "routine:r1", False),
        (("read", "routine:r1"), "read", "routine:r2", True),
        (("read", "routine:r1"), "read", "routine:r1", False),
        (("change", "routine:r1"), "read", "everyone", True),
        (("change", "everyone"), "change", "everyone", False),
    ],
)
def test_widens(old, access, scope, wider):
    assert widens(_share(*old), access=access, scope=scope) is wider


def test_the_default_file_is_in_trillions_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "th"))
    assert paths.folders_config_path() == tmp_path / "th" / "folders.json"
    assert Shares().path == tmp_path / "th" / "folders.json"


def test_names_that_read_the_same_are_one_name(store, home):
    store.put(folder(home, "A"), name="Strasse")
    with pytest.raises(ShareError, match="name"):
        store.put(folder(home, "B"), name="STRA\u00dfE")  # sharp s
    with pytest.raises(ShareError, match="name"):
        store.put(folder(home, "C"), name="\uff33trasse")  # full-width S
    assert store.get("stra\u00dfe") is not None


def test_a_share_whose_folder_became_a_link_reaches_nothing(store, home, tmp_path):
    web = folder(home, "Website")
    share = store.put(web)
    elsewhere = folder(tmp_path, "elsewhere")
    web.rmdir()
    try:
        web.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this account may not create symlinks")
    assert store.state(share) == "moved or replaced by a link"
    assert store.in_scope(EVERYONE) == ((), ())


def test_a_share_that_cannot_be_resolved_reaches_nothing(store, home, monkeypatch):
    share = store.put(folder(home, "Website"))

    def boom(_p: Path) -> Path:
        raise RuntimeError("loop")

    monkeypatch.setattr("trillion.files.shares.resolved", boom)
    assert store.state(share) == "moved or replaced by a link"


def test_a_granted_name_a_saved_share_took_is_numbered(store, home):
    grant = store.add_run_only(folder(home, "a", "Clients"))
    # Another process saved a share named Clients after the grant was made.
    other = Shares(store.path, rules=lambda _p: None)
    other.put(folder(home, "b", "Elsewhere"), name="clients")
    names = [s.name for s in store.all()]
    assert names == ["clients", "Clients 2"]
    assert store.get(grant.id).name == "Clients 2"


# ── The view code on the event loop reads ───────────────────────────


def test_the_view_is_empty_until_looked_at_then_follows_each_change(
    store: Shares, home: Path
) -> None:
    assert store.known(EVERYONE) == ((), ())
    a = folder(home, "Documents", "A")
    share = store.put(a, access="change")
    assert store.known(EVERYONE) == ((a,), ())
    store.change(share.id, access="read")
    assert store.known(EVERYONE) == ((a,), (a,))  # narrowing applies at once
    store.remove(share.id)
    assert store.known(EVERYONE) == ((), ())


def test_refresh_looks_again_when_the_file_changes_or_the_view_is_old(home: Path) -> None:
    now = [0.0]
    store = Shares(
        home / ".trillion" / "folders.json", rules=lambda _p: None, monotonic=lambda: now[0]
    )
    a = folder(home, "A")
    store.put(a)
    a.rmdir()
    store.refresh()
    assert store.known(EVERYONE)[0] == (a,)  # a moment ago, and the file is the same
    now[0] += 3.0
    store.refresh()
    assert store.known(EVERYONE)[0] == ()
    # Edited by hand: seen at the next refresh, however recent the view.
    b = folder(home, "B")
    other = Shares(store.path, rules=lambda _p: None)
    other.put(b)
    store.refresh()
    assert store.known(EVERYONE)[0] == (b,)


def test_a_look_begun_before_a_change_never_replaces_its_view(home: Path) -> None:
    during: list[Callable[[], object]] = []

    def judge(_p: Path) -> str | None:
        # The change lands while the look is still at the folders.
        while during:
            during.pop()()
        return None

    store = Shares(home / ".trillion" / "folders.json", rules=judge)
    a = folder(home, "A")
    share = store.put(a, access="change")
    during.append(lambda: store.change(share.id, access="read"))
    store._look()
    assert store.known(EVERYONE) == ((a,), (a,))  # the change's view, not the older one


def test_of_two_looks_ending_out_of_order_the_later_begun_is_kept(home: Path) -> None:
    ticks = iter(range(1000))
    inside: list[Callable[[], object]] = []

    def judge(_p: Path) -> str | None:
        # The first look is at the folder when it goes, and a second look runs then.
        while inside:
            inside.pop()()
        return None

    store = Shares(
        home / ".trillion" / "folders.json", rules=judge, monotonic=lambda: float(next(ticks))
    )
    a = folder(home, "A")
    store.put(a)

    def gone_then_look() -> None:
        a.rmdir()
        store._look()

    inside.append(gone_then_look)
    store._look()  # begun first, ends last, still seeing the folder
    assert store.known(EVERYONE) == ((), ())


# ── For her tools: checks that write nothing, and narrowing under the lock ──


def test_a_name_is_checked_without_writing(store, home):
    web = folder(home, "Documents", "Website")
    notes = folder(home, "Documents", "Notes")
    store.put(web, name="Website project")
    assert store.check_name("  Website project ", web) == "Website project"  # its own
    with pytest.raises(ShareError, match="already named"):
        store.check_name("website PROJECT", notes)
    with pytest.raises(ShareError, match="one line"):
        store.check_name("two\nlines", notes)
    assert len(on_disk(store)["shares"]) == 1


def test_a_share_is_refused_when_its_folder_is_not_the_one_expected(store, home):
    web = folder(home, "Documents", "Website")
    other = folder(home, "Documents", "Other")
    with pytest.raises(ShareError, match="changed since the card"):
        store.put(web, expect=other)
    assert store.all() == []
    assert store.put(web, expect=web).path == web.resolve()


def test_narrowing_judges_the_share_as_it_is_now(store, home):
    web = folder(home, "Documents", "Website")
    share = store.put(web, access="change")
    store.change(share.id, access="read")  # Settings lowered it meanwhile
    narrowed = store.narrow(share.id, scope=routine_scope("a1b2c3d4"))
    assert (narrowed.access, narrowed.scope) == ("read", routine_scope("a1b2c3d4"))
    with pytest.raises(ShareError, match="reach more"):
        store.narrow(share.id, access="change")
    with pytest.raises(ShareError, match="reach more"):
        store.narrow(share.id, scope=routine_scope("ffff0000"))
    with pytest.raises(ShareError, match="reach more"):
        store.narrow(share.id, scope=EVERYONE)
    assert store.all()[0].scope == routine_scope("a1b2c3d4")


def test_narrowing_a_granted_share_keeps_it_this_runs(store, home):
    web = folder(home, "Documents", "Website")
    grant = store.add_run_only(web)
    narrowed = store.narrow(grant.id, access="read")
    assert narrowed.run_only and narrowed.access == "read"
    assert not store.path.exists() or on_disk(store)["shares"] == []
