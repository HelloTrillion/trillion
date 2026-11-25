"""Dropdowns (native <select>): Chromium draws a dropdown's list outside the page, so
neither she nor the user in control could pick from it in the live picture. She picks
by the option's words with browser_select (act mode, like typing); the user, in
control, gets the app's own list of the dropdown they clicked."""

from __future__ import annotations

from tests.browser.conftest import needs_chromium
from tests.browser.helpers import driven
from tests.browser.test_broker import ref_of
from tests.browser.test_helper import until
from trillion.browser.view import dropdown_here, give_input

PAGE = "http://fixture.test/choose.html"


async def _value(d, name: str = "help") -> str:  # type: ignore[no-untyped-def]
    return await d.task.page.eval_on_selector(f"select[name={name}]", "s => s.value")


@needs_chromium
async def test_she_chooses_an_option_by_its_words_and_nothing_is_sent(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        await d.call("browser_open", url=PAGE)
        page = (await d.call("browser_read")).content or ""
        help_ref = ref_of(page, r"combobox")
        before = len(fixtures.seen.posts())
        r = await d.call("browser_select", ref=help_ref, option="ai implementation & automation")
        assert r.status == "succeeded", r
        assert await _value(d) == "automation"
        assert await d.task.page.text_content("#seen") == "changed to automation"
        assert len(fixtures.seen.posts()) == before  # chosen, not sent


@needs_chromium
async def test_a_missing_or_disabled_option_is_refused_with_what_is_offered(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        await d.call("browser_open", url=PAGE)
        help_ref = ref_of((await d.call("browser_read")).content or "", r"combobox")
        for option in ("Pricing", "Not offered now"):
            r = await d.call("browser_select", ref=help_ref, option=option)
            assert r.status == "failed" and "AI Strategy & Transformation" in (r.content or "")
        assert await _value(d) == ""


@needs_chromium
async def test_a_reading_task_chooses_nothing_and_a_card_dropdown_is_never_chosen(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path) as d:
        await d.call("browser_open", url=PAGE)
        help_ref = ref_of((await d.call("browser_read")).content or "", r"combobox")
        r = await d.call("browser_select", ref=help_ref, option="AI Strategy & Transformation")
        assert r.status == "failed" and await _value(d) == ""
    async with driven(browser_rt, tmp_path, mode="act") as d:
        await d.call("browser_open", url=PAGE)
        snap = (await d.call("browser_read")).content or ""
        refs = [line for line in snap.splitlines() if "combobox" in line]
        exp_ref = ref_of(refs[-1], r"combobox")
        r = await d.call("browser_select", ref=exp_ref, option="02")
        assert r.status == "failed" and await _value(d, "exp") == "01"


@needs_chromium
async def test_typing_into_a_dropdown_points_her_to_choosing(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        await d.call("browser_open", url=PAGE)
        help_ref = ref_of((await d.call("browser_read")).content or "", r"combobox")
        r = await d.call("browser_type", ref=help_ref, text="Automation")
        assert r.status == "failed" and "browser_select" in (r.content or "")


@needs_chromium
async def test_the_user_in_control_gets_the_apps_own_list_and_their_choice_lands(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        await d.call("browser_open", url=PAGE)
        box = await d.task.page.locator("#help").bounding_box()
        click = {"kind": "click", "x": box["x"] + 10, "y": box["y"] + box["height"] / 2}
        # Watching only: no list, and nothing chosen.
        assert not await give_input(d.task, click)
        assert await dropdown_here(d.task) is None
        d.task.in_control = True
        assert await give_input(d.task, click)
        found = await dropdown_here(d.task)
        assert found is not None and found["type"] == "browser.dropdown"
        labels = [o["label"] for o in found["options"]]
        assert labels[:3] == [
            "Choose one",
            "AI Strategy & Transformation",
            "AI Implementation & Automation",
        ]
        assert found["options"][3]["disabled"] is True
        assert await give_input(d.task, {"kind": "choose", "value": "strategy"})
        await until(lambda: True)
        assert await _value(d) == "strategy"
        assert await d.task.page.text_content("#seen") == "changed to strategy"
        # A disabled option, or one that isn't there, is refused.
        assert not await give_input(d.task, {"kind": "choose", "value": "gone"})
        assert not await give_input(d.task, {"kind": "choose", "value": "nope"})
        assert await _value(d) == "strategy"
