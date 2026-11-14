"""The Brave Search API engine, with the user's own key (`browser/brave.py`).

Called from Trillion's own process, never in her browser, at one fixed address. No key,
a refused key, the plan's limit or no answer falls back once to the next engine. The key
never reaches the log, the model or an error. Every call here goes to a stand-in
transport: nothing reaches Brave.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.api.test_server import home, make
from trillion.browser import brave, tools
from trillion.browser.browse import service_of
from trillion.browser.log import TaskLog
from trillion.browser.quarantine import Quarantine
from trillion.browser.settings import ENGINES, BrowserSettings
from trillion.browser.snapshot import Result
from trillion.browser.task import Task
from trillion.spine.contract import ToolContext

__all__ = ["home", "make"]

KEY = "BSA-test-key-not-real-0123456789"
BING = ENGINES["bing"][1]
REPLY = {
    "web": {
        "results": [
            {
                "title": "Dentists <strong>open</strong> Saturday",
                "url": "https://dentists.example/sat",
                "description": "Open <strong>Saturdays</strong> &amp; taking new patients",
            },
            {"title": "No address", "url": "javascript:alert(1)", "description": "dropped"},
            {"title": "Plain", "url": "https://plain.example/", "description": None},
            "not a result",
        ]
    }
}


class Brave:
    """A stand-in for Brave: what it was asked, and what it answers."""

    def __init__(
        self, status: int = 200, body: Any = REPLY, fail: bool = False, raw: bytes | None = None
    ) -> None:
        self.status, self.body, self.fail, self.raw = status, body, fail, raw
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("no route", request=request)
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class Rows:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str | None, str | None]] = []

    async def append(
        self, owner_id: str, task_id: str, at: int, event: str, origin: Any, detail: Any
    ) -> None:
        self.rows.append((event, origin, detail))


def _task(tmp_path: Path, fake: Brave, key: str | None = KEY) -> tuple[Task, Rows]:
    rows = Rows()
    task = Task(
        runtime=None,  # type: ignore[arg-type]
        log=TaskLog(rows, "local", "t1", lambda: 1),
        quarantine=Quarantine(tmp_path / "q"),
        request="find dentists",
        mode="research",
        named_hosts=frozenset(),
        clock=lambda: 1,
        search_url=brave.MARKER,
        search_fallback=BING,
        search_key=lambda: key,
        search_transport=fake.transport,
    )
    return task, rows


def _bing(task: Task, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Bing's feed, without Chromium: what it opened, and one result."""
    opened: list[str] = []

    async def goto(url: str) -> None:
        opened.append(url)

    async def feed(page: Any) -> list[Result]:
        return [Result(title="From Bing", link="https://bing-result.example/", text="")]

    task._pages = [object()]
    monkeypatch.setattr(task, "goto", goto)
    monkeypatch.setattr(tools, "feed_results", feed)
    return opened


def _ctx() -> ToolContext:
    return ToolContext(owner_id="local", session_id="browse:t1", turn=1, call_id="c", via="text")


async def _search(task: Task, query: str = "dentists open saturday") -> Any:
    return await tools.BrowserSearch(task).execute({"query": query}, _ctx())


# ── The reply ──────────────────────────────────────────────────────────────


def test_results_are_mapped_with_their_tags_stripped_and_only_web_addresses_kept():
    got = brave.results_of(REPLY)
    assert got == [
        Result(
            title="Dentists open Saturday",
            link="https://dentists.example/sat",
            text="Open Saturdays & taking new patients",
        ),
        Result(title="Plain", link="https://plain.example/", text=""),
    ]
    assert brave.results_of({"web": {}}) == [] and brave.results_of("nope") == []
    many = {"web": {"results": [{"url": f"https://e.example/{i}"} for i in range(30)]}}
    assert len(brave.results_of(many)) == brave.COUNT


async def test_the_request_goes_to_the_one_fixed_address_with_only_the_query():
    fake = Brave()
    got = await brave.search("dentists open saturday", KEY, transport=fake.transport)
    assert [r.link for r in got] == ["https://dentists.example/sat", "https://plain.example/"]
    (req,) = fake.requests
    assert req.method == "GET"
    assert str(req.url).startswith(brave.URL + "?")
    assert dict(req.url.params) == {"q": "dentists open saturday", "count": "10"}
    assert req.headers["X-Subscription-Token"] == KEY
    assert req.headers["Accept"] == "application/json"


@pytest.mark.parametrize(
    ("fake", "kind"),
    [
        (Brave(401), "key_refused"),
        (Brave(403), "key_refused"),
        (Brave(422), "key_refused"),
        (Brave(429), "limit_reached"),
        (Brave(500), "unavailable"),
        (Brave(200, raw=b"<html>not json"), "unavailable"),
        (Brave(fail=True), "unreachable"),
    ],
)
async def test_each_failure_is_a_kind_with_fixed_words_never_the_key(fake: Brave, kind: str):
    with pytest.raises(brave.SearchFailed) as e:
        await brave.search("q", KEY, transport=fake.transport)
    assert e.value.kind == kind
    assert str(e.value) == brave.WORDS[kind]
    # Nothing of the request travels with it, the whole chain included.
    assert e.value.__cause__ is None
    assert e.value.__context__ is None or e.value.__suppress_context__
    assert KEY not in "".join(traceback.format_exception(e.value))


async def test_no_key_never_calls_brave():
    with pytest.raises(brave.SearchFailed) as e:
        await brave.search("q", None, transport=Brave(fail=True).transport)
    assert e.value.kind == "no_key"


def test_a_limit_reached_says_so_plainly():
    assert brave.WORDS["limit_reached"] == "Brave's monthly limit is reached"


# ── The engine in Settings ─────────────────────────────────────────────────


def test_the_api_engine_round_trips_and_falls_back_to_bing_never_the_other_way():
    s = BrowserSettings.from_json({"search": brave.ENGINE})
    assert s.search == brave.ENGINE
    assert BrowserSettings.from_json(s.to_json()) == s
    assert BrowserSettings().changed({"search": brave.ENGINE}).search == brave.ENGINE
    assert s.search_urls == (brave.MARKER, BING)
    assert ENGINES[brave.ENGINE][0] == "Brave Search API (your key)"
    # The API is never anyone's fallback: it needs a key and is used only when chosen.
    for engine in ENGINES:
        if engine != brave.ENGINE:
            assert brave.MARKER not in BrowserSettings(search=engine).search_urls


def test_her_browser_is_never_allowed_the_api_address(tmp_path: Path):
    task, _ = _task(tmp_path, Brave())
    assert task.broker.opening(brave.URL) == "chip"


# ── The search tool ────────────────────────────────────────────────────────


async def test_the_api_answers_from_this_process_and_its_links_count_as_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake = Brave()
    task, rows = _task(tmp_path, fake)
    opened = _bing(task, monkeypatch)
    r = await _search(task)
    assert r.ok
    assert "1. Dentists open Saturday" in r.content and "https://dentists.example/sat" in r.content
    assert opened == []  # her browser opened nothing
    assert len(fake.requests) == 1
    assert rows.rows == [("searched", brave.ORIGIN, "dentists open saturday")]
    assert task.broker.opening("https://dentists.example/other") == "open"


async def test_with_no_key_it_falls_back_to_bing_and_calls_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake = Brave()
    task, rows = _task(tmp_path, fake, key=None)
    opened = _bing(task, monkeypatch)
    r = await _search(task)
    assert r.ok and "From Bing" in r.content
    assert r.content.startswith("(No Brave Search API key is saved, so these come from")
    assert fake.requests == []
    assert opened == [BING.replace("{q}", "dentists+open+saturday")]
    assert ("refused", brave.ORIGIN, "no_key") in rows.rows
    assert ("searched", brave.ORIGIN, "dentists open saturday") not in rows.rows


@pytest.mark.parametrize(
    ("fake", "kind", "words"),
    [
        (Brave(401), "key_refused", "Brave didn't accept the saved key"),
        (Brave(429), "limit_reached", "Brave's monthly limit is reached"),
        (Brave(fail=True), "unreachable", "Brave Search couldn't be reached"),
    ],
)
async def test_a_failure_falls_back_once_and_the_api_isnt_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: Brave, kind: str, words: str
):
    task, rows = _task(tmp_path, fake)
    opened = _bing(task, monkeypatch)
    r = await _search(task)
    assert r.ok and "From Bing" in r.content and words in r.content
    assert ("refused", brave.ORIGIN, kind) in rows.rows
    again = await _search(task, "dentists near me")
    assert again.ok and "From Bing" in again.content
    # One call to Brave in all, then Bing for the rest of the task.
    assert len(fake.requests) == 1
    assert len(opened) == 2 and task.search_urls == [BING]
    for row in rows.rows:
        assert KEY not in json.dumps(row)
    assert KEY not in r.content and KEY not in again.content


async def test_when_the_fallback_also_refuses_the_task_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task, rows = _task(tmp_path, Brave(429))
    _bing(task, monkeypatch)

    async def empty(page: Any) -> list[Result]:
        return []

    monkeypatch.setattr(tools, "feed_results", empty)
    r = await _search(task)
    assert not r.ok and r.error_kind == "search"
    assert task.broker.stopped == "search"
    assert ("stopped", None, "search") in rows.rows


# ── Settings → Her browser: the key ────────────────────────────────────────


def test_the_key_is_checked_saved_reported_only_as_saved_and_removed(make: Any):
    app = make(model=False)
    fake = Brave()
    service_of(app.rt).search_transport = fake.transport  # type: ignore[union-attr]
    put = {**app.auth, "content-type": "application/json"}
    before = app.client.get("/api/browser", headers=app.auth).json()
    assert before["brave"] == {
        "engine": brave.ENGINE,
        "saved": False,
        "keychain": True,
        "key_link": brave.KEY_LINK,
    }
    assert app.client.put("/api/browser/brave-key", json={"key": KEY}).status_code == 401
    r = app.client.put("/api/browser/brave-key", json={"key": f"  {KEY}  "}, headers=put)
    assert r.status_code == 200
    assert r.json()["brave"]["saved"] is True
    assert KEY not in r.text
    assert app.secrets.get(brave.SECRET) == KEY
    # Checked with one search of one result.
    (req,) = fake.requests
    assert dict(req.url.params) == {"q": brave.CHECK_QUERY, "count": "1"}
    assert KEY not in app.client.get("/api/browser", headers=app.auth).text
    gone = app.client.request("DELETE", "/api/browser/brave-key", json={}, headers=put)
    assert gone.status_code == 200 and gone.json()["brave"]["saved"] is False
    assert app.secrets.values() == []


@pytest.mark.parametrize(("status", "fail"), [(401, False), (403, False), (500, False), (0, True)])
def test_a_key_brave_doesnt_take_is_never_stored(make: Any, status: int, fail: bool):
    app = make(model=False)
    service_of(app.rt).search_transport = Brave(status, fail=fail).transport  # type: ignore[union-attr]
    put = {**app.auth, "content-type": "application/json"}
    r = app.client.put("/api/browser/brave-key", json={"key": KEY}, headers=put)
    assert r.status_code == 400
    assert KEY not in r.text
    assert app.secrets.values() == []


def test_a_key_at_its_limit_is_saved_and_says_so(make: Any):
    app = make(model=False)
    service_of(app.rt).search_transport = Brave(429).transport  # type: ignore[union-attr]
    put = {**app.auth, "content-type": "application/json"}
    r = app.client.put("/api/browser/brave-key", json={"key": KEY}, headers=put)
    assert r.status_code == 200
    assert "monthly limit is reached" in r.json()["note"]
    assert app.secrets.get(brave.SECRET) == KEY


def test_a_key_needs_a_keychain_and_an_empty_one_is_refused(make: Any):
    app = make(model=False, keychain=False)
    service_of(app.rt).search_transport = Brave(fail=True).transport  # type: ignore[union-attr]
    put = {**app.auth, "content-type": "application/json"}
    r = app.client.put("/api/browser/brave-key", json={"key": KEY}, headers=put)
    assert r.status_code == 409 and KEY not in r.text
    other = make(model=False)
    bad = other.client.put(
        "/api/browser/brave-key",
        json={"key": "   "},
        headers={**other.auth, "content-type": "application/json"},
    )
    assert bad.status_code == 422 and other.secrets.values() == []
