"""Search (decision 9): a refusal or a check page is a stop, never a puzzle. The chosen
engine, then the next one once, in the same step; then the task stops and says so."""

from __future__ import annotations

from tests.browser.conftest import needs_chromium
from tests.browser.helpers import driven
from tests.browser.models import Steps, call, say
from tests.browser.test_helper import run, service
from trillion.browser.broker import search_refused
from trillion.browser.service import STOPPED_WORDS
from trillion.browser.settings import DEFAULT_ENGINE, ENGINES, BrowserSettings

REFUSED = "http://fixture.test/refused?q={q}"
EMPTY = "http://fixture.test/empty?q={q}"
WORKS = "http://fixture.test/search?q={q}"
FEED = "http://fixture.test/feed?q={q}"
LINKS = ["https://a.example/1", "https://b.example/2", "https://c.example/3"]


def test_known_block_pages_and_pages_without_results_are_refusals():
    assert search_refused("https://x.example/sorry/index", "", "", LINKS)
    assert search_refused("https://x.example/s", "Captcha - Search", "", LINKS)
    assert search_refused("https://x.example/s", "", "detected unusual traffic", LINKS)
    assert search_refused("https://x.example/s", "Results", "ten", ["mailto:help@x.example"])
    assert not search_refused("https://x.example/s", "Results", "ten results", LINKS)


def test_the_default_engine_and_one_fallback():
    assert DEFAULT_ENGINE == "bing" and next(iter(ENGINES)) == "bing"
    first, then = BrowserSettings().search_urls
    assert first.startswith("https://www.bing.com/") and then != first
    assert BrowserSettings(search="google").search_urls[0].startswith("https://www.google.com/")


@needs_chromium
async def test_a_refusing_engine_falls_back_once_in_the_same_step(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, search_url=REFUSED, search_fallback=WORKS) as d:
        r = await d.call("browser_search", query="dentists")
        assert r.status == "succeeded", r
        assert "Second page" in (r.content or "")
        rows = await d.rows()
        assert ("refused", "search_refused") in [(x.event, x.detail) for x in rows]
        # The refusing engine isn't asked again.
        assert d.task.search_urls == [WORKS]


@needs_chromium
async def test_when_both_refuse_the_task_stops_and_says_so_without_burning_steps(
    browser_rt, tmp_path, fixtures, monkeypatch
):
    model = Steps(
        call("browser_search", query="dentists open saturday"),
        call("browser_search", query="dentists open saturday near me"),
        call("browser_search", query="saturday dentists"),
        say(""),
    )
    monkeypatch.setattr(BrowserSettings, "search_urls", property(lambda self: (REFUSED, EMPTY)))
    async with service(browser_rt, tmp_path, model) as (svc, log):
        result = await run(svc)
        assert result.outcome == "stopped"
        assert result.text == STOPPED_WORDS["search"]
        assert result.text.startswith("The search engine turned her browser away")
        assert svc.listing()[0]["outcome"] == "stopped"
        rows = await log.for_task("local", svc.listing()[0]["id"], svc._clock())
        # The chosen engine and the one fallback, each once; then nothing more.
        assert len([x for x in rows if x.event == "searched"]) == 2
        assert ("stopped", "search") in [(x.event, x.detail) for x in rows]
        assert model.calls == 1


@needs_chromium
async def test_a_results_feed_is_read_and_its_links_open_as_followed(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, search_url=FEED) as d:
        r = await d.call("browser_search", query="the answer")
        assert r.status == "succeeded", r
        assert "1. Second page" in (r.content or "") and "http://other.test/two.html" in (
            r.content or ""
        )
        # A result is a link the page showed: no chip to open it.
        opened = await d.call("browser_open", url="http://other.test/two.html")
        assert opened.status == "succeeded", opened
