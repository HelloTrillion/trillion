"""Writing, recalling, correcting, forgetting, and agreeing with the files
(MEM-1, MEM-6a, MEM-6b, MEM-7)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

from trillion.memory import book as book_module
from trillion.memory import files
from trillion.memory.book import MemoryBook
from trillion.memory.types import MAX_TEXT, MemoryError_
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.memory_store import SqliteMemoryStore

NOW = 1_791_600_000_000


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteMemoryStore]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    yield SqliteMemoryStore(conn)
    await conn.close()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "memory"


async def opened(store: SqliteMemoryStore, root: Path) -> MemoryBook:
    b = MemoryBook(store, root, "local")
    await b.sync(NOW)
    return b


async def remember(b: MemoryBook, text: str, **kw: object) -> str:
    args: dict[str, object] = {
        "type": "user", "source": "user_statement", "moves": False, "now_ms": NOW,
    } | kw  # fmt: skip
    return (await b.write(text=text, **args)).memory.id  # type: ignore[arg-type]


async def test_a_memory_outlives_the_process(store: SqliteMemoryStore, root: Path) -> None:
    first = await opened(store, root)
    assert first.count() == 0 and not first.last_sync.diverged
    memory_id = await remember(first, "  I take my coffee black.  ")
    assert (root / "user" / f"{memory_id}.md").is_file()

    again = await opened(store, root)  # a restart: nothing held in memory survives
    assert again.count() == 1 and not again.last_sync.diverged
    (found,) = again.recall("how do I take my coffee")
    assert found.text == "I take my coffee black." and found.source == "user_statement"
    assert again.peek(memory_id) == found and again.peek("mem_000000000000") is None


async def test_saying_the_same_thing_twice_stores_it_once(
    store: SqliteMemoryStore, root: Path
) -> None:
    b = await opened(store, root)
    first = await b.write(
        type="user", text="I take my coffee black.", source="user_statement", moves=False,
        now_ms=NOW,
    )  # fmt: skip
    second = await b.write(
        type="feedback", text="i take my   coffee BLACK.", source="agent_inference", moves=True,
        now_ms=NOW + 5,
    )  # fmt: skip
    assert second.duplicate and second.memory == first.memory and b.count() == 1


async def test_a_correction_supersedes_and_keeps_the_history(
    store: SqliteMemoryStore, root: Path
) -> None:
    b = await opened(store, root)
    old = await remember(b, "I live at 12 Oak Street.", moves=True)
    new = await remember(b, "I live at 9 Elm Road.", moves=True, replaces=old, now_ms=NOW + 60_000)

    assert [m.text for m in b.recall("where do I live")] == ["I live at 9 Elm Road."]
    history = {m.id: m for m in b.recall("where do I live", history=True)}
    assert history[old].status == "superseded" and history[old].superseded_by == new
    assert history[old].version == 2 and history[new].supersedes == old
    # The old address is still on disk, marked: nothing was deleted (SPN-9).
    assert "status: superseded" in (root / "user" / f"{old}.md").read_text(encoding="utf-8")
    assert (await opened(store, root)).peek(old) == history[old]

    with pytest.raises(MemoryError_) as e:
        await remember(b, "I live on the moon.", replaces=old)  # already replaced
    assert e.value.kind == "not_found"
    with pytest.raises(MemoryError_):
        await remember(b, "x", replaces="mem_ffffffffffff")


async def test_forgetting_retracts_and_is_never_recalled_as_current(
    store: SqliteMemoryStore, root: Path
) -> None:
    b = await opened(store, root)
    memory_id = await remember(b, "My gym code is 1234.")
    gone = await b.forget(memory_id, NOW + 1)
    assert gone.status == "retracted" and gone.version == 2
    assert b.recall("gym code") == [] and b.recall() == [] and b.count() == 0
    assert [m.status for m in b.recall("gym code", history=True)] == ["retracted"]
    assert (await b.forget(memory_id, NOW + 2)) == gone  # twice is the same as once
    assert (root / "user" / f"{memory_id}.md").is_file()
    with pytest.raises(MemoryError_) as e:
        await b.forget("mem_ffffffffffff", NOW)
    assert e.value.kind == "not_found"
    # Said again after being forgotten, it is remembered again as a new memory.
    assert await remember(b, "My gym code is 1234.") != memory_id


async def test_recall_with_no_question_is_newest_first_and_can_filter(
    store: SqliteMemoryStore, root: Path
) -> None:
    b = await opened(store, root)
    await remember(b, "First.", now_ms=NOW)
    await remember(b, "Second.", type="project", now_ms=NOW + 1)
    await remember(b, "Third.", now_ms=NOW + 2)
    assert [m.text for m in b.recall()] == ["Third.", "Second.", "First."]
    assert [m.text for m in b.recall(type="project")] == ["Second."]
    assert [m.text for m in b.recall(limit=1)] == ["Third."]


@pytest.mark.parametrize(
    ("text", "kind"),
    [("", "empty"), ("   \n ", "empty"), ("x" * (MAX_TEXT + 1), "too_long")],
)
async def test_bounds_are_refused_with_a_reason(
    store: SqliteMemoryStore, root: Path, text: str, kind: str
) -> None:
    b = await opened(store, root)
    with pytest.raises(MemoryError_) as e:
        await remember(b, text)
    assert e.value.kind == kind and b.count() == 0


async def test_a_full_memory_refuses_new_facts_but_still_takes_corrections(
    store: SqliteMemoryStore, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(book_module, "MAX_ACTIVE", 2)
    b = await opened(store, root)
    first = await remember(b, "One.")
    await remember(b, "Two.")
    with pytest.raises(MemoryError_) as e:
        await remember(b, "Three.")
    assert e.value.kind == "full"
    await remember(b, "One, corrected.", replaces=first)
    assert b.count() == 2


async def test_a_disk_that_refuses_is_reported_and_nothing_is_half_remembered(
    store: SqliteMemoryStore, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = await opened(store, root)

    def refuse(*_: object) -> str:
        raise OSError("disk full: 'I take my coffee black.'")

    monkeypatch.setattr(files, "write", refuse)
    with pytest.raises(MemoryError_) as e:
        await remember(b, "I take my coffee black.")
    assert e.value.kind == "disk" and "coffee" not in str(e.value)
    assert b.count() == 0 and await store.all("local") == []


# ── The files are the truth (MEM-1, MEM-6b) ──────────────────────────────


async def test_an_edit_made_by_hand_is_adopted_and_reported(
    store: SqliteMemoryStore, root: Path
) -> None:
    b = await opened(store, root)
    memory_id = await remember(b, "I take my coffee black.")
    path = root / "user" / f"{memory_id}.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("black", "with oat milk"), encoding="utf-8"
    )
    again = await opened(store, root)
    assert again.last_sync.changed == 1 and again.last_sync.diverged
    assert again.peek(memory_id).text == "I take my coffee with oat milk."  # type: ignore[union-attr]
    assert not (await opened(store, root)).last_sync.diverged  # reported once, then settled


async def test_a_file_dropped_in_by_hand_is_adopted(store: SqliteMemoryStore, root: Path) -> None:
    b = await opened(store, root)
    await remember(b, "Something.")
    visitor = replace(
        b.recall()[0], id="mem_aaaaaaaaaaaa", text="Ana is my sister.", source="imported_claim"
    )
    files.write(root, visitor)
    again = await opened(store, root)
    assert again.last_sync.added == 1 and again.count() == 2
    assert again.recall("who is Ana")[0].source == "imported_claim"


async def test_a_missing_file_is_reported_not_recalled_and_not_deleted(
    store: SqliteMemoryStore, root: Path
) -> None:
    b = await opened(store, root)
    memory_id = await remember(b, "I take my coffee black.")
    kept = (root / "user" / f"{memory_id}.md").read_text(encoding="utf-8")
    (root / "user" / f"{memory_id}.md").unlink()

    again = await opened(store, root)
    assert again.last_sync.missing == 1 and again.count() == 0 and again.recall("coffee") == []
    (row,) = await store.all("local")
    assert row.missing and row.memory.text == "I take my coffee black."  # the row is kept

    # The file comes back (a sync client catching up): so does the memory.
    (root / "user" / f"{memory_id}.md").write_text(kept, encoding="utf-8")
    back = await opened(store, root)
    assert back.count() == 1 and back.last_sync.changed == 1
    assert not (await store.all("local"))[0].missing


async def test_a_file_that_cannot_be_read_right_now_does_not_lose_its_memory(
    store: SqliteMemoryStore, root: Path
) -> None:
    b = await opened(store, root)
    memory_id = await remember(b, "I take my coffee black.")
    (root / "user" / f"{memory_id}.md").write_text("half a fi", encoding="utf-8")  # cut short
    again = await opened(store, root)
    assert again.last_sync.unreadable == 1 and again.last_sync.missing == 0
    # What the index last knew is still used, and the damaged file is left alone.
    assert again.recall("coffee")[0].text == "I take my coffee black."
    assert (root / "user" / f"{memory_id}.md").read_text(encoding="utf-8") == "half a fi"


async def test_a_folder_that_cannot_be_listed_implies_nothing_is_gone(
    store: SqliteMemoryStore, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = await opened(store, root)
    await remember(b, "I take my coffee black.")
    await remember(b, "The launch is in May.", type="project")
    real = Path.iterdir

    def broken(self: Path):  # type: ignore[no-untyped-def]
        if self.name == "user":
            raise PermissionError("no")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", broken)
    again = await opened(store, root)
    assert not again.last_sync.complete and again.last_sync.missing == 0
    assert again.count() == 2 and again.recall("coffee")
    assert not any(r.missing for r in await store.all("local"))


async def test_divergence_is_logged_in_counts_never_in_words(
    store: SqliteMemoryStore, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    b = await opened(store, root)
    memory_id = await remember(b, "My PIN is 4821.")
    (root / "user" / f"{memory_id}.md").unlink()
    await opened(store, root)
    assert "1 missing" in caplog.text and "4821" not in caplog.text
