"""Recall by meaning beside recall by words (MEM-2), with a fake model, and the real
model's download and checks (ONB-7). No test here loads the real model or reaches
the network."""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest

from trillion.memory import meaning
from trillion.memory.book import MemoryBook
from trillion.memory.meaning import Vector
from trillion.memory.rank import blend
from trillion.memory.types import Memory
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.memory_store import SqliteMemoryStore
from trillion.voice.models import ModelFile, ModelPack

NOW = 1_791_600_000_000

# A few fixed subjects. A text's vector points along each subject it names, so
# "sibling" and "sister" mean the same thing and share no letters.
_SUBJECTS = (
    {"coffee", "drink", "espresso", "tea"},
    {"sister", "sibling", "brother", "family"},
    {"gym", "exercise", "workout"},
    {"car", "drive", "vehicle"},
    {"lisbon", "live", "lives", "city"},
)


class FakeEmbedder:
    model = "fake-5"
    floor = 0.6

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bool]] = []
        self.fail = False

    def embed(self, texts: Sequence[str], *, query: bool) -> list[Vector]:
        self.calls.append((tuple(texts), query))
        if self.fail:
            raise RuntimeError("the model broke")
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> Vector:
        said = {w.strip(".,?!'").lower() for w in text.split()}
        raw = [float(bool(said & s)) for s in _SUBJECTS] + [0.0] * (meaning.DIMENSIONS - 5)
        raw[-1] = 0.2  # nothing is ever exactly zero
        size = math.sqrt(sum(x * x for x in raw))
        return tuple(x / size for x in raw)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteMemoryStore]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    yield SqliteMemoryStore(conn)
    await conn.close()


async def opened(store: SqliteMemoryStore, root: Path, embedder: FakeEmbedder | None) -> MemoryBook:
    b = MemoryBook(store, root, "local", embedder=embedder)
    await b.sync(NOW)
    return b


async def remember(b: MemoryBook, text: str, **kw: object) -> str:
    args: dict[str, object] = {
        "type": "user", "source": "user_statement", "moves": False, "now_ms": NOW,
    } | kw  # fmt: skip
    return (await b.write(text=text, **args)).memory.id  # type: ignore[arg-type]


def texts(found: Sequence[Memory]) -> list[str]:
    return [m.text for m in found]


# ── Ranking ──────────────────────────────────────────────────────────────


async def test_a_question_sharing_no_word_is_found_by_meaning(
    store: SqliteMemoryStore, tmp_path: Path
) -> None:
    b = await opened(store, tmp_path / "memory", FakeEmbedder())
    await remember(b, "My sister Ana is a pilot.")
    await remember(b, "I take my coffee black.")
    assert b.recall("which sibling flies planes") == []  # no shared word
    assert texts(await b.search("which sibling flies planes")) == ["My sister Ana is a pilot."]
    assert texts(await b.search("what do I drink")) == ["I take my coffee black."]


async def test_an_exact_word_still_wins_over_a_closer_meaning(
    store: SqliteMemoryStore, tmp_path: Path
) -> None:
    b = await opened(store, tmp_path / "memory", FakeEmbedder())
    await remember(b, "My brother drives a car to the gym.")  # close in meaning to the question
    await remember(b, "The locker number is 4417.")  # shares only the number
    found = await b.search("sibling vehicle workout 4417")
    assert texts(found) == ["The locker number is 4417.", "My brother drives a car to the gym."]


async def test_nothing_under_the_floor_comes_back(store: SqliteMemoryStore, tmp_path: Path) -> None:
    b = await opened(store, tmp_path / "memory", FakeEmbedder())
    await remember(b, "I take my coffee black.")
    assert await b.search("tell me a joke") == []


def test_blend_counts_meaning_at_most_half_of_the_best_word_match() -> None:
    def m(i: str, text: str) -> Memory:
        return Memory(
            id=i, type="user", text=text, source="user_statement", moves=False,
            created_at=NOW, as_of=NOW,
        )  # fmt: skip

    pool = [m("a", "Ana's number is 4417."), m("b", "Something else entirely.")]
    near = {"a": 0.0, "b": 1.0}  # b is as close in meaning as anything can be
    assert [x.id for x in blend(pool, "4417", near, floor=0.6, limit=5)] == ["a", "b"]
    assert [x.id for x in blend(pool, "nothing shared", near, floor=0.6, limit=5)] == ["b"]
    assert blend(pool, "nothing shared", {"b": 0.59}, floor=0.6, limit=5) == []


async def test_retired_and_forgotten_memories_are_never_found_as_current(
    store: SqliteMemoryStore, tmp_path: Path
) -> None:
    b = await opened(store, tmp_path / "memory", FakeEmbedder())
    old = await remember(b, "My sister lives in Porto.")
    await remember(b, "My sister lives in Lisbon.", replaces=old)
    gone = await remember(b, "I drink espresso.")
    await b.forget(gone, NOW)
    assert texts(await b.search("where is my sibling")) == ["My sister lives in Lisbon."]
    assert await b.search("what do I drink") == []
    history = await b.search("where is my sibling", history=True)
    assert {m.status for m in history} == {"active", "superseded"}


async def test_a_file_whose_memory_is_missing_is_not_found(
    store: SqliteMemoryStore, tmp_path: Path
) -> None:
    root = tmp_path / "memory"
    b = await opened(store, root, FakeEmbedder())
    memory_id = await remember(b, "My sister is a pilot.")
    (root / "user" / f"{memory_id}.md").unlink()
    again = await opened(store, root, FakeEmbedder())
    assert await again.search("sibling") == []


# ── Vectors ──────────────────────────────────────────────────────────────


async def test_a_memory_is_embedded_as_it_is_written_and_kept(
    store: SqliteMemoryStore, tmp_path: Path
) -> None:
    fake = FakeEmbedder()
    b = await opened(store, tmp_path / "memory", fake)
    memory_id = await remember(b, "I take my coffee black.")
    assert fake.calls == [(("I take my coffee black.",), False)]
    stored = await store.vectors("local", "fake-5")
    digest, raw = stored[memory_id]
    assert digest == hashlib.sha256(b"I take my coffee black.").hexdigest()
    assert len(raw) == 4 * 384 and meaning.unpack(raw) == pytest.approx(fake._one("coffee"))
    # A restart reads the vector back rather than making it again.
    again_fake = FakeEmbedder()
    again = await opened(store, tmp_path / "memory", again_fake)
    assert await again.catch_up(lambda: NOW) == 0
    assert texts(await again.search("what do I drink")) == ["I take my coffee black."]
    assert again_fake.calls == [(("what do I drink",), True)]


async def test_a_file_edited_by_hand_is_embedded_again(
    store: SqliteMemoryStore, tmp_path: Path
) -> None:
    root = tmp_path / "memory"
    b = await opened(store, root, FakeEmbedder())
    memory_id = await remember(b, "I take my coffee black.")
    path = root / "user" / f"{memory_id}.md"
    path.write_text(
        path.read_text("utf-8").replace("I take my coffee black.", "My brother is a pilot."),
        "utf-8",
    )
    fake = FakeEmbedder()
    again = await opened(store, root, fake)
    assert await again.search("what do I drink") == []  # the old vector no longer counts
    assert await again.catch_up(lambda: NOW) == 1
    assert texts(await again.search("which sibling")) == ["My brother is a pilot."]


async def test_memories_without_vectors_are_caught_up_in_batches(
    store: SqliteMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memory"
    plain = await opened(store, root, None)  # written while meaning search was off
    for i in range(5):
        await remember(plain, f"Note {i} about my sister.")
    monkeypatch.setattr("trillion.memory.book.EMBED_BATCH", 2)
    fake = FakeEmbedder()
    b = await opened(store, root, fake)
    assert await b.search("sibling") == []  # found by meaning only once embedded
    fake.calls.clear()
    b.start_catching_up(lambda: NOW)
    await b._catching_up  # type: ignore[misc]
    assert [len(t) for t, _ in fake.calls] == [2, 2, 1]
    assert len(await b.search("sibling")) == 5
    await b.close()


# ── Falling back to words ────────────────────────────────────────────────


async def test_without_a_model_recall_is_by_words(store: SqliteMemoryStore, tmp_path: Path) -> None:
    b = await opened(store, tmp_path / "memory", None)
    await remember(b, "I take my coffee black.")
    assert not b.by_meaning
    assert await b.search("what do I drink") == []
    assert texts(await b.search("coffee")) == ["I take my coffee black."]
    b.start_catching_up(lambda: NOW)  # nothing to do, and nothing started
    await b.close()


async def test_a_model_that_fails_falls_back_to_words_and_stays_off(
    store: SqliteMemoryStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeEmbedder()
    b = await opened(store, tmp_path / "memory", fake)
    await remember(b, "I take my coffee black.")
    fake.fail = True
    with caplog.at_level(logging.WARNING):
        assert texts(await b.search("coffee please")) == ["I take my coffee black."]
    assert not b.by_meaning
    assert "search by meaning is off for this run: RuntimeError" in caplog.text
    assert "coffee" not in caplog.text  # the kind of failure, never the text
    calls = len(fake.calls)
    await remember(b, "My sister lives in Lisbon.")  # still written, by words only
    assert len(fake.calls) == calls
    assert texts(await b.search("sister")) == ["My sister lives in Lisbon."]


async def test_a_model_that_fails_while_writing_never_loses_the_memory(
    store: SqliteMemoryStore, tmp_path: Path
) -> None:
    fake = FakeEmbedder()
    fake.fail = True
    b = await opened(store, tmp_path / "memory", fake)
    await remember(b, "I take my coffee black.")
    assert b.count() == 1 and texts(await b.search("coffee")) == ["I take my coffee black."]


def test_with_nothing_installed_there_is_no_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(meaning, "runtime_installed", lambda: False)
    assert meaning.state(tmp_path) == "no_runtime" and meaning.local(tmp_path) is None
    monkeypatch.setattr(meaning, "runtime_installed", lambda: True)
    assert meaning.state(tmp_path) == "no_model" and meaning.local(tmp_path) is None


# ── The model files (ONB-7) ──────────────────────────────────────────────


def _tiny_pack(monkeypatch: pytest.MonkeyPatch, body: dict[str, bytes]) -> None:
    files = tuple(
        ModelFile(name=n, size=len(b), sha256=hashlib.sha256(b).hexdigest())
        for n, b in body.items()
    )
    monkeypatch.setattr(
        meaning, "PACK", ModelPack(id="tiny", base_url="https://models.example/r1", files=files)
    )


def _serving(body: dict[str, bytes]) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        name = request.url.path.removeprefix("/r1/")
        return httpx.Response(200, content=body[name]) if name in body else httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


_GOOD = {"onnx/model.onnx": b"model bytes", "tokenizer.json": b"{}"}


async def test_the_model_is_fetched_into_its_folder_and_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tiny_pack(monkeypatch, _GOOD)
    monkeypatch.setattr(meaning, "runtime_installed", lambda: True)
    async with _serving(_GOOD) as client:
        await meaning.fetch(tmp_path, client=client)
    assert (tmp_path / "tiny" / "onnx" / "model.onnx").read_bytes() == b"model bytes"
    assert meaning.state(tmp_path) == "on"
    meaning.verify(tmp_path / "tiny")  # and it passes the check made at load


async def test_a_download_whose_checksum_differs_is_refused_and_left_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tiny_pack(monkeypatch, _GOOD)
    tampered = {"onnx/model.onnx": b"MODEL BYTES", "tokenizer.json": b"{}"}  # same size
    async with _serving(tampered) as client:
        with pytest.raises(meaning.MeaningError) as refused:
            await meaning.fetch(tmp_path, client=client)
    assert refused.value.kind == "download_mismatch"
    assert not (tmp_path / "tiny" / "onnx" / "model.onnx").exists()
    assert not list((tmp_path / "tiny").rglob("*.part"))


async def test_a_file_changed_after_download_is_never_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tiny_pack(monkeypatch, _GOOD)
    async with _serving(_GOOD) as client:
        await meaning.fetch(tmp_path, client=client)
    (tmp_path / "tiny" / "onnx" / "model.onnx").write_bytes(b"MODEL BYTES")
    with pytest.raises(meaning.MeaningError) as refused:
        meaning.verify(tmp_path / "tiny")
    assert refused.value.kind == "checksum"
    embedder = meaning.OnnxEmbedder(tmp_path / "tiny")
    with pytest.raises(meaning.MeaningError):
        embedder.embed(["anything"], query=False)  # refused before the runtime is touched


async def test_an_unreachable_host_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _tiny_pack(monkeypatch, _GOOD)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("the network is off", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        with pytest.raises(meaning.MeaningError) as refused:
            await meaning.fetch(tmp_path, client=client)
    assert refused.value.kind == "unreachable"


def test_the_pinned_model_is_the_one_the_prd_names() -> None:
    assert meaning.PACK.id == "bge-small-en-v1.5" and meaning.DIMENSIONS == 384
    assert "/resolve/" in meaning.PACK.base_url and "/main" not in meaning.PACK.base_url
    assert all(len(f.sha256) == 64 and f.size > 0 for f in meaning.PACK.files)
