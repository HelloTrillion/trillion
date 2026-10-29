"""The shares the user made, kept in folders.json (the shared folders spec, D1, D9, D10)."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("bad", ["", "   ", "x" * 61, "two\nlines", "a\rb", "tab\x07"])
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
    ],
)
def test_a_damaged_file_is_never_overwritten_and_no_shares_apply(store, home, text):
    store.path.write_text(text, encoding="utf-8")
    problem = store.problem()
    assert problem is not None and "damaged" in problem and str(store.path) in problem
    assert store.all() == []
    assert store.in_scope(EVERYONE) == ((), ())
    with pytest.raises(ShareError, match="damaged"):
        store.put(folder(home, "Clients"))
    with pytest.raises(ShareError, match="damaged"):
        store.remove_routine("r1")
    assert store.path.read_text(encoding="utf-8") == text


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
