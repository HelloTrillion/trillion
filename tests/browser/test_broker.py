"""The broker, case by case, in a real headless Chromium against fixture pages.

The fixture servers record every request that reaches them, so "nothing was sent" is
checked where it would have landed, not taken from what the tool said.
"""

from __future__ import annotations

import os
import re
import stat

from tests.browser.conftest import needs_chromium
from tests.browser.helpers import Driven, driven

pytestmark = needs_chromium


def ref_of(content: str, pattern: str) -> str:
    """The reference of the first snapshot line matching `pattern`."""
    for line in content.splitlines():
        if re.search(pattern, line):
            m = re.search(r"\[ref=([a-z0-9]+)\]", line)
            if m:
                return m.group(1)
    raise AssertionError(f"nothing matches {pattern!r} in:\n{content}")


async def open_page(d: Driven, path: str) -> str:
    r = await d.call("browser_open", url=f"http://fixture.test/{path}")
    assert r.status == "succeeded", r
    return r.content or ""


# ── Research mode sends nothing ──────────────────────────────────────────────


async def test_research_mode_refuses_a_submit_button_whatever_it_is_called(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path) as d:
        page = await open_page(d, "hidden.html")
        before = len(fixtures.seen.posts())
        r = await d.call("browser_follow", ref=ref_of(page, r'button "Next"'))
        assert r.status == "refused" or r.status == "failed"
        assert "submits" in (r.error_kind or "") or "submits a form" in (r.content or "")
        assert len(fixtures.seen.posts()) == before


async def test_research_mode_refuses_every_request_that_is_not_a_get(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path) as d:
        before = len(fixtures.seen.posts())
        page = await open_page(d, "beacon.html")
        r = await d.call("browser_follow", ref=ref_of(page, r'link "Read more"'))
        assert r.status in ("failed", "refused")
        assert "nothing was sent" in (r.content or "")
        assert len(fixtures.seen.posts()) == before
        rows = await d.rows()
        assert any(row.event == "refused" and row.detail == "sends" for row in rows)
        assert any(row.event == "refused" and row.detail == "websocket" for row in rows)


async def test_research_mode_never_types_or_presses(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path) as d:
        page = await open_page(d, "form.html")
        typed = await d.call("browser_type", ref=ref_of(page, r"textbox \"Name\""), text="Kim")
        assert typed.status == "failed" and "only reads" in (typed.content or "")
        pressed = await d.call("browser_press", ref=ref_of(page, r'button "Next"'))
        assert pressed.status == "refused" and "only reads" in (pressed.detail or "")


# ── Act mode: a card from what is really about to go ─────────────────────────


async def test_a_press_is_held_and_its_card_shows_the_real_destination_and_every_field(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "form.html")
        assert (
            await d.call("browser_type", ref=ref_of(page, r'textbox "Name"'), text="Kim")
        ).status == "succeeded"
        message = "A quote for 40 people,\nplease."
        r = await d.call("browser_type", ref=ref_of(page, r'textbox "Message"'), text=message)
        assert r.status == "succeeded"
        before = len(fixtures.seen.posts())
        r = await d.call("browser_press", ref=ref_of(page, r'button "Next"'))
        assert r.status == "confirmation_required", r
        # Held in Chromium: nothing reached the site.
        assert len(fixtures.seen.posts()) == before
        card = await d.store.get_pending(r.pending_id)
        assert card is not None
        assert card.summary == "Send to fixture.test"
        assert "POST http://fixture.test/enquiry" in card.detail
        assert "name: Kim" in card.detail
        assert "message: A quote for 40 people,\n    please." in card.detail
        # A yes in words can't approve it: only the tap.
        said = await d.router.resolve(r.pending_id, "confirm", d.ctx(turn=2))
        assert said.status == "refused"
        done = await d.tap(r.pending_id)
        assert done.status == "succeeded", done
        posts = fixtures.seen.posts()[before:]
        assert len(posts) == 1
        _, path, body = posts[0]
        assert path == "/enquiry"
        assert b"name=Kim" in body


async def test_a_declined_press_never_sends(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "form.html")
        await d.call("browser_type", ref=ref_of(page, r'textbox "Name"'), text="Kim")
        before = len(fixtures.seen.posts())
        r = await d.call("browser_press", ref=ref_of(page, r'button "Next"'))
        assert r.status == "confirmation_required"
        no = await d.tap(r.pending_id, "cancel")
        assert no.status == "cancelled"
        await d.task.broker.drop()
        assert len(fixtures.seen.posts()) == before


async def test_the_card_names_the_normalised_origin_not_a_lookalike(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "lookalike.html")
        r = await d.call("browser_press", ref=ref_of(page, r'button "Continue"'))
        assert r.status == "confirmation_required", r
        card = await d.store.get_pending(r.pending_id)
        assert card is not None
        assert "POST http://other.test/collect" in card.detail
        assert "other.test." not in card.detail
        assert "account: 12345" in card.detail
        await d.task.broker.drop()


async def test_a_search_form_is_a_card_that_shows_what_rides_in_the_address(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "form.html")
        await d.call("browser_type", ref=ref_of(page, r'textbox "Search"'), text="opening hours")
        r = await d.call("browser_press", ref=ref_of(page, r'button "Search"'))
        assert r.status == "confirmation_required", r
        card = await d.store.get_pending(r.pending_id)
        assert card is not None
        assert card.summary == "Open fixture.test"
        assert "GET http://fixture.test/search?q=opening+hours" in card.detail
        assert "q: opening hours" in card.detail
        await d.task.broker.drop()


async def test_after_typing_a_page_built_address_is_refused_by_follow(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, mode="act", named=frozenset({"fixture.test"})) as d:
        page = await open_page(d, "search.html")
        await d.call("browser_type", ref=ref_of(page, r"textbox"), text="what the user said")
        r = await d.call("browser_follow", ref=ref_of(page, r'button "Go"'))
        assert r.status in ("failed", "refused")
        assert not any("what+the+user" in p or "what%20the" in p for p in fixtures.seen.paths())


# ── Fields the agent never types in ──────────────────────────────────────────


async def test_password_and_code_fields_are_never_typed_in(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "login.html")
        for label in ("Password", "Code"):
            r = await d.call(
                "browser_type", ref=ref_of(page, rf'textbox "{label}"'), text="hunter2"
            )
            assert "Needs you: sign in on fixture.test" in (r.content or ""), r
            assert d.task.ask is not None and d.task.ask.kind == "step"
            d.task.clear_ask()
        values = await d.task.page.evaluate(
            "() => [...document.querySelectorAll('input')].map((i) => i.value)"
        )
        assert "hunter2" not in values


async def test_a_form_with_a_password_in_it_is_the_users_to_send(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "login.html")
        # As the user would, in control: the password is filled by them, not her.
        await d.task.page.fill("input[type=password]", "their-own")
        before = len(fixtures.seen.posts())
        r = await d.call("browser_press", ref=ref_of(page, r'button "Sign in"'))
        assert r.status == "refused"
        assert "user's step" in (r.detail or "")
        assert len(fixtures.seen.posts()) == before
        assert d.task.ask is not None and d.task.ask.kind == "step"


async def test_a_checkout_stops_the_task(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        r = await d.call("browser_open", url="http://fixture.test/checkout.html")
        assert "checkout or payment page" in (r.content or "")
        assert d.task.broker.stopped == "payment"
        again = await d.call("browser_read")
        assert again.status == "refused"


async def test_a_tls_error_stops_the_task(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, named=frozenset({"badtls.test"})) as d:
        r = await d.call("browser_open", url="https://badtls.test/index.html")
        assert r.status == "failed"
        assert r.error_kind == "tls"
        assert d.task.broker.stopped == "tls"
        again = await d.call("browser_open", url="http://fixture.test/index.html")
        assert again.status == "refused" and "certificate" in (again.detail or "")


# ── Where it may go ──────────────────────────────────────────────────────────


async def test_private_addresses_are_refused_on_every_request(browser_rt, tmp_path, fixtures):
    named = frozenset({"fixture.test", "lan.test", "metadata.test"})
    async with driven(browser_rt, tmp_path, named=named) as d:
        # Written out, refused at once; by name, refused by the proxy after DNS.
        for url in (
            "http://localhost/",
            "http://10.0.0.1/",
            "http://[::1]/",
            "http://lan.test/",
            "http://metadata.test/latest",
        ):
            r = await d.call("browser_open", url=url)
            assert r.status == "failed" and r.error_kind == "private_address", (url, r)
        r = await d.call("browser_open", url="http://127.0.0.1:8080/")
        assert r.status == "failed" and r.error_kind == "bad_port"
        page = await open_page(d, "private.html")
        r = await d.call("browser_follow", ref=ref_of(page, r"link \"A link that redirects"))
        assert "/admin" not in fixtures.seen.paths()
        rows = await d.rows()
        refused = {(row.origin, row.detail) for row in rows if row.event == "refused"}
        assert ("http://lan.test", "private_address") in refused
        assert ("http://metadata.test", "private_address") in refused
        assert ("http://127.0.0.1", "private_address") in refused


async def test_a_new_origin_the_model_typed_needs_a_chip(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path) as d:
        r = await d.call("browser_open", url="http://other.test/two.html")
        assert r.error_kind == "chip"
        assert d.task.ask is not None and d.task.ask.text == "Open other.test?"
        assert "/two.html" not in [
            p for h, p in ((h, p) for _, h, p, _ in fixtures.seen.requests if h == "other.test")
        ]
        d.task.clear_ask()
        # A link a page showed is one she follows, not one she made up.
        await open_page(d, "index.html")
        r = await d.call("browser_open", url="http://other.test/two.html")
        assert r.status == "succeeded", r


async def test_a_lookalike_with_a_trailing_dot_is_asked_about_by_its_real_name(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path, named=frozenset({"bank.test"})) as d:
        await open_page(d, "lookalike.html")
        r = await d.call("browser_open", url="http://bank.test./login")
        # The user named bank.test, and this is bank.test written with a dot: the same
        # site, and the proxy finds no such site here.
        assert r.status == "failed"
        r = await d.call("browser_open", url="http://other.test./collect")
        assert r.error_kind == "chip"
        assert d.task.ask is not None and d.task.ask.text == "Open other.test?"


async def test_unattended_nobody_answers_a_chip(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, attended=False) as d:
        r = await d.call("browser_open", url="http://other.test/two.html")
        assert r.error_kind == "chip"
        assert d.task.ask is None


# ── Files ────────────────────────────────────────────────────────────────────


async def test_a_download_is_quarantined_under_a_safe_name_and_never_runnable(
    browser_rt, tmp_path, fixtures
):
    async with driven(browser_rt, tmp_path) as d:
        page = await open_page(d, "download.html")
        await d.call("browser_follow", ref=ref_of(page, r'link "Download it"'))
        for _ in range(50):
            if d.task.downloads:
                break
            import asyncio

            await asyncio.sleep(0.1)
        assert len(d.task.downloads) == 1
        kept = d.task.downloads[0]
        assert kept.name.endswith("evil.sh") and "/" not in kept.name
        assert kept.path.is_relative_to(tmp_path / "quarantine")
        mode = stat.S_IMODE(os.stat(kept.path).st_mode)
        assert mode & 0o111 == 0
        rows = await d.rows()
        assert any(row.event == "download" and row.detail == "quarantined" for row in rows)


async def test_an_upload_is_refused(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "upload.html")
        r = await d.call(
            "browser_follow", ref=ref_of(page, r"button \"Choose File\"|button \"f\"|button")
        )
        assert r.status in ("failed", "refused")


# ── The log ──────────────────────────────────────────────────────────────────


async def test_the_log_holds_no_page_text_and_no_field_values(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, mode="act") as d:
        page = await open_page(d, "form.html")
        await d.call("browser_type", ref=ref_of(page, r'textbox "Name"'), text="Zebediah Quill")
        r = await d.call("browser_press", ref=ref_of(page, r'button "Next"'))
        await d.tap(r.pending_id)
        await d.call("browser_search", query="opening hours")
        rows = await d.rows()
        assert {row.event for row in rows} >= {
            "started",
            "opened",
            "typed",
            "card",
            "sent",
            "searched",
        }
        for row in rows:
            for value in (row.origin or "", row.detail or ""):
                assert "Zebediah" not in value and "Enquiry" not in value and "Thanks" not in value
            if row.origin is not None:
                assert re.fullmatch(r"https?://[a-z0-9.-]+(:\d+)?", row.origin), row.origin
        assert [row.detail for row in rows if row.event == "searched"] == ["opening hours"]


# ── A saved sign-in stays on its site ────────────────────────────────────────


async def test_a_task_on_a_saved_sign_in_cant_leave_its_site(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path, site="fixture.test") as d:
        page = await open_page(d, "index.html")
        r = await d.call("browser_open", url="http://other.test/two.html")
        assert r.status == "failed" and r.error_kind == "off_site"
        hits = len([1 for _, h, _, _ in fixtures.seen.requests if h == "other.test"])
        await d.call("browser_follow", ref=ref_of(page, r'link "Another site"'))
        assert len([1 for _, h, _, _ in fixtures.seen.requests if h == "other.test"]) == hits


async def test_a_task_without_a_sign_in_has_no_cookies(browser_rt, tmp_path, fixtures):
    async with driven(browser_rt, tmp_path) as d:
        await open_page(d, "index.html")
        assert await d.task.context.cookies() == []
