"""Settings → Your voice (§5.3a): /api/voiceprint reports, enrols, re-enrols and
forgets. The embedder is faked: no model, no download, no real audio."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import App, home, make  # noqa: F401
from trillion import paths
from trillion.voice import speaker as sp
from trillion.voice.providers import wespeaker
from trillion.voice.types import SpeakerEmbedding, UnknownSpeaker

PATH = "/api/voiceprint"
AUDIO = base64.b64encode(b"\x00\x01" * 16_000).decode()


def vec(*head: float) -> tuple[float, ...]:
    return sp.unit((*head, *([0.0] * (8 - len(head)))))


OWNER = vec(1.0, 0.1)
OTHER = vec(0.0, 0.0, 1.0)


class Phrases:
    """Stands in for the model: each phrase checked returns the next answer."""

    def __init__(self) -> None:
        self.answers: list[tuple[SpeakerEmbedding | None, str | None]] = []
        self.heard: list[bytes] = []

    async def __call__(self, _self: Any, pcm: bytes) -> tuple[SpeakerEmbedding | None, str | None]:
        self.heard.append(pcm)
        return self.answers.pop(0)


@pytest.fixture
def phrases(monkeypatch: pytest.MonkeyPatch) -> Phrases:
    fake = Phrases()
    monkeypatch.setattr(wespeaker, "available", lambda: True)
    monkeypatch.setattr(wespeaker, "ready", lambda root=None: True)
    monkeypatch.setattr(wespeaker.LocalSpeaker, "phrase", lambda self, pcm: fake(self, pcm))
    return fake


def good(v: tuple[float, ...]) -> tuple[SpeakerEmbedding, None]:
    return SpeakerEmbedding(vector=v, model=wespeaker.NAME), None


def get(app: App) -> dict[str, Any]:
    r = app.client.get(PATH, headers=app.auth)
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def phrase(app: App, index: int, pcm: str = AUDIO) -> Any:
    return app.client.post(f"{PATH}/phrase", json={"index": index, "pcm": pcm}, headers=app.auth)


def test_status_before_anything(make: Any) -> None:
    app: App = make()
    state = get(app)
    assert state["enrolled"] is False and state["working"] is False
    assert len(state["prompts"]) == sp.PHRASES
    assert "CC BY 4.0" in state["model"]["credit"]
    assert state["model"]["size"] == wespeaker.PACK.size


def test_needs_a_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401


def test_without_the_optional_parts_nothing_is_recorded(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wespeaker, "available", lambda: False)
    app: App = make()
    assert phrase(app, 0).json() == {"error": "not_installed"}
    r = app.client.post(f"{PATH}/model", json={}, headers=app.auth)
    assert r.status_code == 400 and r.json() == {"error": "not_installed"}


def test_without_the_model_it_asks_for_the_download(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wespeaker, "available", lambda: True)
    monkeypatch.setattr(wespeaker, "ready", lambda root=None: False)
    app: App = make()
    assert phrase(app, 0).json() == {"error": "needs_download"}


def test_enrol_then_forget(make: Any, phrases: Phrases) -> None:
    app: App = make()
    phrases.answers = [(None, "quiet"), good(OWNER), good(OWNER), good(vec(1.0, 0.2))]
    first = phrase(app, 0).json()
    assert first == {"ok": False, "problem": "quiet", "recorded": []}
    for i in range(3):
        r = phrase(app, i).json()
        assert r["ok"] is True
        # Nothing about the voice goes back to the page.
        assert set(r) == {"ok", "problem", "recorded"}
    saved = app.client.post(f"{PATH}/save", json={}, headers=app.auth).json()
    assert saved["ok"] is True and saved["enrolled"] is True and saved["working"] is True
    assert isinstance(app.rt.speakers, wespeaker.LocalSpeaker)
    assert paths.voiceprint_path().is_file()
    gone = app.client.request("DELETE", PATH, json={}, headers=app.auth).json()
    assert gone["enrolled"] is False and not paths.voiceprint_path().exists()
    assert isinstance(app.rt.speakers, UnknownSpeaker)


def test_a_phrase_in_another_voice_is_said_again(make: Any, phrases: Phrases) -> None:
    app: App = make()
    phrases.answers = [good(OWNER), good(OTHER), good(OWNER), good(OWNER)]
    for i in range(3):
        phrase(app, i)
    r = app.client.post(f"{PATH}/save", json={}, headers=app.auth).json()
    assert r == {"ok": False, "problem": "voices", "phrase": 1, "recorded": [0, 2]}
    assert not paths.voiceprint_path().exists()
    phrase(app, 1)
    assert app.client.post(f"{PATH}/save", json={}, headers=app.auth).json()["ok"] is True


def test_save_needs_every_phrase(make: Any, phrases: Phrases) -> None:
    app: App = make()
    phrases.answers = [good(OWNER)]
    phrase(app, 0)
    r = app.client.post(f"{PATH}/save", json={}, headers=app.auth)
    assert r.status_code == 400 and r.json() == {"error": "phrases_missing"}
    assert (
        app.client.request("DELETE", f"{PATH}/draft", json={}, headers=app.auth).json()["recorded"]
        == []
    )


def test_bad_input(make: Any, phrases: Phrases) -> None:
    app: App = make()
    assert phrase(app, 0, "not base64!").json() == {"error": "bad_audio"}
    assert phrase(app, 5).status_code == 422
    too_long = "A" * (base64_limit() + 4)
    assert phrase(app, 0, too_long).status_code == 422


def base64_limit() -> int:
    from trillion.api.voiceprint import MAX_PHRASE_B64

    return MAX_PHRASE_B64


def damaged() -> None:
    """The owner enrolled, then the file was damaged, before this start."""
    paths.voiceprint_path().parent.mkdir(parents=True, exist_ok=True)
    paths.voiceprint_path().write_text("not json", encoding="utf-8")


def test_a_file_that_cant_be_read_is_reported_not_as_not_set_up(make: Any) -> None:
    # 2026-11-12 spec: someone enrolled, and no voice can be checked. Not "not set up".
    damaged()
    app: App = make()
    state = get(app)
    assert state["enrolled"] is True and state["working"] is False
    assert state["unreadable"] == "damaged" and state["enrolled_ms"] is None
    # Looking never repairs: the file is kept as it was, evidence and the owner's choice.
    assert paths.voiceprint_path().read_text(encoding="utf-8") == "not json"


def test_a_readable_file_and_none_report_no_problem(make: Any, phrases: Phrases) -> None:
    app: App = make()
    assert get(app)["unreadable"] is None


def test_teaching_her_again_replaces_an_unreadable_file(make: Any, phrases: Phrases) -> None:
    damaged()
    app: App = make()
    phrases.answers = [good(OWNER), good(OWNER), good(vec(1.0, 0.2))]
    for i in range(3):
        assert phrase(app, i).json()["ok"] is True
    saved = app.client.post(f"{PATH}/save", json={}, headers=app.auth).json()
    # Put right without a restart: the cached state is the new voiceprint.
    assert saved["ok"] is True and saved["unreadable"] is None and saved["working"] is True
    assert get(app)["unreadable"] is None
    assert isinstance(app.rt.voiceprints.get(), sp.Profile)  # type: ignore[union-attr]
    assert isinstance(app.rt.speakers, wespeaker.LocalSpeaker)


def test_forgetting_an_unreadable_file_is_nobody_enrolled(make: Any) -> None:
    damaged()
    app: App = make()
    gone = app.client.request("DELETE", PATH, json={}, headers=app.auth).json()
    assert gone["enrolled"] is False and gone["unreadable"] is None
    assert not paths.voiceprint_path().exists()
    assert app.rt.voiceprints.get() is None  # type: ignore[union-attr]


def test_the_file_is_where_paths_says(tmp_path: Path) -> None:
    # The test suite's own folder (tests/conftest.py): never the machine's.
    assert paths.voiceprint_path().parent == tmp_path


def test_own_words_cant_be_turned_on_while_the_file_cant_be_read(make: Any) -> None:
    # SPN-20 needs a voice to check against; an unreadable file is none (2026-11-12 spec).
    damaged()
    app: App = make()
    r = app.client.put(PATH, json={"own_words": True}, headers=app.auth)
    assert r.status_code == 400 and r.json() == {"error": "not_enrolled"}
    assert get(app)["own_words"] is False
