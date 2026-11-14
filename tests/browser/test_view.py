"""The in-app view and saved sign-ins (decisions 11, 11a, 12): the live picture goes
only to the page watching, the user's input reaches the page only while they have
control, and a saved sign-in is used only when asked for, after a chip, on its site."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from typing import Any

from tests.browser.conftest import needs_chromium
from tests.browser.helpers import driven
from tests.browser.models import Steps, call, say
from tests.browser.test_helper import run, service, until
from trillion.browser.signins import SignIns, only_site, site_of
from trillion.browser.view import Screencast, give_input

ROOT = Path(__file__).resolve().parents[2]


def test_the_view_can_reach_no_model_router_or_conversation():
    source = (ROOT / "src" / "trillion" / "browser" / "view.py").read_text(encoding="utf-8")
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert all(
        not any(
            word in line
            for word in ("brain", "router", "spine", "conversation", "memory", "storage")
        )
        for line in imports
    ), imports
    # Frames are handed on and dropped: never written to a file or kept on the object.
    assert "open(" not in source and "write" not in source and "self.frames" not in source


def test_a_saved_sign_in_keeps_only_its_own_site(tmp_path):
    state = {
        "cookies": [
            {"name": "a", "domain": ".library.org", "value": "1"},
            {"name": "b", "domain": "catalog.library.org", "value": "2"},
            {"name": "t", "domain": ".tracker.example", "value": "3"},
            {"name": "x", "domain": "library.org.evil.example", "value": "4"},
        ],
        "origins": [
            {"origin": "https://www.library.org", "localStorage": []},
            {"origin": "https://tracker.example", "localStorage": []},
        ],
    }
    kept = only_site(state, "library.org")
    assert [c["name"] for c in kept["cookies"]] == ["a", "b"]
    assert [o["origin"] for o in kept["origins"]] == ["https://www.library.org"]
    signins = SignIns(tmp_path / "signins")
    assert signins.keep("library.org", state) == 2
    path = tmp_path / "signins" / "library.org.json"
    if sys.platform != "win32":  # Windows has no POSIX modes
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert signins.sites() == ["library.org"]
    assert signins.named(frozenset({"www.library.org", "other.example"})) == "library.org"
    assert signins.named(frozenset({"other.example"})) is None
    assert site_of("WWW.Library.ORG.") == "library.org" and site_of("../etc") is None
    assert signins.remove("library.org") and signins.sites() == []


@needs_chromium
async def test_the_picture_goes_to_the_watcher_and_input_only_while_in_control(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        await d.call("browser_open", url="http://fixture.test/form.html")
        frames: list[dict[str, Any]] = []

        async def send(body: dict[str, Any]) -> None:
            frames.append(body)

        cast = Screencast(d.task, send)
        await cast.start()
        await until(lambda: bool(frames), timeout=10)
        assert frames[0]["type"] == "browser.frame"
        assert base64.b64decode(frames[0]["data"])[:2] == b"\xff\xd8"  # a JPEG
        box = await d.task.page.locator("input[name=name]").bounding_box()
        click = {"kind": "click", "x": box["x"] + 5, "y": box["y"] + 5}
        # Watching only: nothing reaches the page.
        assert not await give_input(d.task, click)
        assert not await give_input(d.task, {"kind": "text", "text": "typed by the user"})
        assert await d.task.page.input_value("input[name=name]") == ""
        # In control: the user's own clicks and keys reach it, and the model is shown
        # nothing while they do.
        d.task.in_control = True
        assert await give_input(d.task, click)
        assert await give_input(d.task, {"kind": "text", "text": "typed by the user"})
        assert await d.task.page.input_value("input[name=name]") == "typed by the user"
        r = await d.call("browser_read")
        assert r.status == "refused" and "the user has the browser" in (r.detail or "")
        assert not await give_input(d.task, {"kind": "key", "key": "Meta+Q"})
        await cast.stop()


@needs_chromium
async def test_the_user_in_control_may_send_what_the_model_may_not(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path) as d:
        await d.call("browser_open", url="http://fixture.test/form.html")
        before = len(fixtures.seen.posts())
        d.task.in_control = True
        await d.task.page.fill("input[name=name]", "Kim")
        await d.task.page.click("button:has-text('Next')")
        await d.task.page.wait_for_load_state()
        assert len(fixtures.seen.posts()) == before + 1


@needs_chromium
async def test_a_saved_sign_in_is_used_only_when_named_after_its_chip_and_stays_on_site(
    browser_rt, tmp_path, fixtures
):
    def model() -> Steps:
        return Steps(
            call("browser_open", url="http://fixture.test/two.html"),
            call("browser_open", url="http://other.test/two.html"),
            say("Waiting."),
            call("browser_done", answer="Done."),
            say(""),
            delay_s=0.5,
        )

    async with service(browser_rt, tmp_path, model()) as (svc, log):
        # The user signs in ahead of time, in her browser outside a task.
        free = await svc.open_free()
        assert free.in_control
        assert await svc.goto_free("http://fixture.test/signed-in") is None
        assert await svc.keep_signin("free") == "fixture.test"
        assert await svc.goto_free("http://localhost/") is not None
        await svc.close_free()
        assert svc.signins.sites() == ["fixture.test"]

        # Named, and allowed on its chip: the task has the cookie, and stays on the site.
        job = asyncio.create_task(run(svc))
        await until(lambda: svc.current() is not None and svc.current().task.ask is not None)  # type: ignore[union-attr]
        running = svc.current()
        assert running.task.ask.kind == "signin"  # type: ignore[union-attr]
        assert running.task.ask.text == "Use your sign-in for fixture.test?"  # type: ignore[union-attr]
        await svc.answer(running.id, running.task.ask.id, "allowed")  # type: ignore[union-attr]
        await until(lambda: running.task.site == "fixture.test" and running.task.steps >= 1)  # type: ignore[union-attr]
        cookies = await running.task.context.cookies()  # type: ignore[union-attr]
        assert [c["name"] for c in cookies] == ["session"]
        result = await asyncio.wait_for(job, 30)
        assert result.outcome == "answered"
        rows = await log.for_task("local", running.id, svc._clock())
        assert ("refused", "off_site") in [(r.event, r.detail) for r in rows]

    async with service(browser_rt, tmp_path, model()) as (svc, _):
        svc.signins.keep(
            "fixture.test",
            {
                "cookies": [
                    {"name": "session", "value": "yes", "domain": "fixture.test", "path": "/"}
                ],
                "origins": [],
            },
        )
        # Not named: never asked, never used.
        never = Steps(call("browser_done", answer="Nothing."), say(""))
        svc._provider = lambda: never  # type: ignore[assignment]
        result = await run(svc, named_hosts=frozenset())
        assert result.outcome == "answered"
        # Named but declined: no cookies.
        slow = Steps(
            call("browser_open", url="http://fixture.test/two.html"),
            say("x"),
            say("y"),
            delay_s=1.5,
        )
        svc._provider = lambda: slow  # type: ignore[assignment]
        job = asyncio.create_task(run(svc))
        await until(lambda: svc.current() is not None and svc.current().task.ask is not None)  # type: ignore[union-attr]
        running = svc.current()
        await svc.answer(running.id, running.task.ask.id, "declined")  # type: ignore[union-attr]
        await until(lambda: running.task.steps >= 1)  # type: ignore[union-attr]
        assert await running.task.context.cookies() == []  # type: ignore[union-attr]
        assert running.task.site is None  # type: ignore[union-attr]
        await asyncio.wait_for(job, 30)


@needs_chromium
async def test_after_the_users_sign_in_step_the_task_stays_on_that_site(
    browser_rt, tmp_path, fixtures
):
    model = Steps(
        call("browser_open", url="http://fixture.test/login.html"),
        call("browser_your_step", kind="sign_in"),
        say("Waiting."),
        call("browser_open", url="http://other.test/two.html"),
        call("browser_done", answer="Done."),
        say(""),
    )
    async with service(browser_rt, tmp_path, model) as (svc, log):
        job = asyncio.create_task(run(svc))
        await until(lambda: svc.current() is not None and svc.current().task.ask is not None)  # type: ignore[union-attr]
        running = svc.current()
        assert running.task.ask.text == "Needs you: sign in on fixture.test"  # type: ignore[union-attr]
        assert await svc.take_control(running.id)
        assert await svc.give_back(running.id)
        await asyncio.wait_for(job, 30)
        assert running.task.broker.site == "fixture.test"  # type: ignore[union-attr]
        rows = await log.for_task("local", running.id, svc._clock())
    assert ("refused", "off_site") in [(r.event, r.detail) for r in rows]
