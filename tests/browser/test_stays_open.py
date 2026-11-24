"""Her browser stays open after she answers (the stays-open spec): the page a task
answered on is left open as her browser, for the user to look at and for a follow-up
in the same conversation to carry on from. Each step is still a task of its own, with
its own broker, proxy and log."""

from __future__ import annotations

import asyncio
from typing import Any

from tests.browser.conftest import needs_chromium
from tests.browser.models import Steps, call, say
from tests.browser.test_helper import run, service, until
from trillion.browser import service as service_module

TWO = "http://fixture.test/two.html"


def answers(text: str = "It says 42.") -> Steps:
    return Steps(call("browser_open", url=TWO), call("browser_done", answer=text), say(""))


@needs_chromium
async def test_the_page_she_answered_on_stays_open_as_her_browser(browser_rt, tmp_path, fixtures):
    async with service(browser_rt, tmp_path, answers()) as (svc, _):
        result = await run(svc)
        assert result.outcome == "answered"
        free = svc.free_json()
        assert free is not None and free["url"] == TWO and free["left"] is True
        # The user can look and click: it is theirs to drive until she is asked again.
        assert svc.free is not None and svc.free.in_control
        await svc.close_free()
        assert svc.free is None


@needs_chromium
async def test_a_follow_up_carries_on_from_that_page_and_knows_what_came_before(
    browser_rt, tmp_path, fixtures
):
    told: list[str] = []

    def first(text: str) -> list[Any]:
        told.append(text)
        return call("browser_done", answer="It's the second page.")

    # An answer ends a task's turn at once: the next step is the follow-up's first.
    model = Steps(
        call("browser_open", url=TWO), call("browser_done", answer="It says 42."),
        first, say(""),
    )  # fmt: skip
    async with service(browser_rt, tmp_path, model) as (svc, log):
        await run(svc, "what does the second page say")
        left = svc.free
        result = await run(svc, "and what's the page called?")
        assert result.outcome == "answered"
        assert f"Your browser is already on {TWO}" in told[0]
        assert "what does the second page say" in told[0] and "It says 42." in told[0]
        # The follow-up was a task of its own, on that page, and left it open again.
        assert left is not None and left.closed
        newest = svc.listing()[-1]["id"]
        rows = await log.for_task("local", newest, svc._clock())
        assert ("opened", "http://fixture.test") in [(r.event, r.origin) for r in rows]
        assert svc.free_json() is not None and svc.free_json()["url"] == TWO  # type: ignore[index]
        await svc.close_free()


@needs_chromium
async def test_another_conversation_leaves_the_page_alone(browser_rt, tmp_path, fixtures):
    model = Steps(
        call("browser_open", url=TWO), call("browser_done", answer="It says 42."),
        call("browser_done", answer="Something else."), say(""),
    )  # fmt: skip
    async with service(browser_rt, tmp_path, model) as (svc, _):
        await run(svc)
        left = svc.free
        await svc.run(
            "find something else",
            conversation_id="another",
            named_hosts=frozenset(),
            act=False,
            via="text",
        )
        # Still the first conversation's page, untouched.
        assert left is not None and not left.closed and svc.free is left
        await svc.close_free()


@needs_chromium
async def test_a_browser_the_user_opened_passes_on_its_address_never_its_cookies(
    browser_rt, tmp_path, fixtures
):
    model = Steps(say("Looking."), call("browser_done", answer="Done."), say(""), delay_s=0.5)
    async with service(browser_rt, tmp_path, model) as (svc, _):
        await svc.open_free()
        # Signed in by the user, then on another page of the site.
        assert await svc.goto_free("http://fixture.test/signed-in") is None
        assert await svc.goto_free(TWO) is None
        assert await svc.free.context.cookies()  # type: ignore[union-attr]
        job = asyncio.create_task(run(svc, "what is on this page"))
        await until(lambda: svc.current() is not None and svc.current().task is not None)  # type: ignore[union-attr]
        task = svc.current().task  # type: ignore[union-attr]

        def there() -> bool:
            try:
                return str(task.page.url) == TWO
            except Exception:
                return False  # its page isn't open yet

        await until(there)
        assert await task.context.cookies() == []
        await asyncio.wait_for(job, 30)
        await svc.close_free()


@needs_chromium
async def test_a_task_that_found_nothing_leaves_nothing_open(browser_rt, tmp_path, fixtures):
    model = Steps(say(""), say(""), say(""), say(""), say(""), say(""), say(""), say(""))
    async with service(browser_rt, tmp_path, model) as (svc, _):
        result = await run(svc)
        assert result.outcome == "failed"
        assert svc.free is None


@needs_chromium
async def test_an_untouched_page_closes_on_its_own(browser_rt, tmp_path, fixtures, monkeypatch):
    monkeypatch.setattr(service_module, "LEFT_OPEN_S", 0.2)
    async with service(browser_rt, tmp_path, answers()) as (svc, _):
        await run(svc)
        assert svc.free is not None
        await until(lambda: svc.free is None, timeout=10)
