"""The helper tools (the your helpers spec): made on a card with its first line saved as
hers; changed, narrowed and widened only in its own chat; handed work as a due-now
timed ask; never after an outside read, from a run or by a helper."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import FakeClock, ms
from trillion.brain.types import TextBlock
from trillion.helpers.look import COLORS, ICONS
from trillion.helpers.store import JOB_MAX, MAX_HELPERS, NAME_MAX, NOTES_MAX, OwnHelper
from trillion.spend.types import Work
from trillion.spine.contract import ToolCall, ToolContext
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import (
    SqliteConversationStore,
    SqlitePreferences,
)
from trillion.storage.sqlite.helper_store import SqliteHelperStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore
from trillion.storage.sqlite.spine_store import SqliteSpineStore
from trillion.tools.helper import (
    ASK,
    CATEGORY,
    CREATE,
    NARROW,
    OFFER_PREF,
    SUGGEST,
    UPDATE,
    WIDEN,
    HelperAsk,
    HelperCreate,
    HelperNarrow,
    HelperSuggest,
    HelperUpdate,
    HelperWiden,
    card_look,
)
from trillion.tools.schedule import MAX_TITLE

OWNER = "local"
NOW = ms("2026-11-05T09:00")
JOB = "Keeps track of who owes you and chases late payments."
CTX = ToolContext(owner_id=OWNER, session_id="chat-1", turn=1, call_id="t1", via="text")
NARROWS = "Don't include refunds."
WIDENS = "Also watch the support inbox."
NOT_SAID = ["schedule", "routine", "ask", "refresh", "channel"]


class Setup:
    def __init__(
        self,
        helpers: SqliteHelperStore,
        schedules: SqliteScheduleStore,
        conversations: SqliteConversationStore,
        preferences: SqlitePreferences,
        router: Router,
    ) -> None:
        self.helpers, self.schedules = helpers, schedules
        self.conversations, self.preferences, self.router = conversations, preferences, router
        self.clock = FakeClock(NOW)
        self.capped = False
        self.offered: set[tuple[str, str]] = set()
        self.create, self.update = HelperCreate(), HelperUpdate()
        self.narrow, self.widen = HelperNarrow(), HelperWiden()
        self.ask, self.suggest = HelperAsk(), HelperSuggest()

        async def held(work: Work) -> bool:
            return self.capped and work == "helpers"

        async def offer_open(session_id: str, area: str) -> bool:
            return (session_id, area) in self.offered

        def offer_claim(session_id: str, area: str) -> bool:
            # Taken once, as a session's turn is.
            if (session_id, area) not in self.offered:
                return False
            self.offered.discard((session_id, area))
            return True

        for tool in self.tools:
            tool.bind(
                helpers,
                schedules,
                clock=self.clock,
                owner=OWNER,
                conversations=conversations,
                preferences=preferences,
                zone=lambda: "America/New_York",
                held=held,
                offer_open=offer_open,
                offer_claim=offer_claim,
            )

    @property
    def tools(self) -> tuple[Any, ...]:
        return (self.create, self.update, self.narrow, self.widen, self.ask, self.suggest)

    async def made(self, name: str = "Invoices") -> OwnHelper:
        h = await self.helpers.create(OWNER, name, JOB, NOW)
        assert isinstance(h, OwnHelper)
        return h

    async def notes(self, h: OwnHelper) -> str:
        now = await self.helpers.get(OWNER, h.id)
        assert now is not None
        return now.notes


@pytest.fixture
async def it(tmp_path: Path) -> AsyncIterator[Setup]:
    path = tmp_path / "t.db"
    conns = [await connect(path) for _ in range(4)]
    await migrate(conns[0])
    registry = ToolRegistry()
    router = Router(registry, SqliteSpineStore(conns[0]), policy=DenyPolicy.default())
    setup = Setup(
        SqliteHelperStore(conns[1]),
        SqliteScheduleStore(conns[2]),
        SqliteConversationStore(conns[3]),
        SqlitePreferences(conns[3]),
        router,
    )
    router._clock = setup.clock
    for tool in setup.tools:
        registry.register(tool, module="trillion.tools.helper")
    yield setup
    for c in conns:
        await c.close()


def call(name: str, args: dict[str, Any]) -> ToolCall:
    return ToolCall(name=name, args=args)


def in_chat(h: OwnHelper, **kw: Any) -> ToolContext:
    return dataclasses.replace(CTX, session_id=h.conversation_id, helper_id=h.id, **kw)


def tap(ctx: ToolContext = CTX) -> ToolContext:
    return dataclasses.replace(ctx, via="tap", turn=2)


# ── Making one ───────────────────────────────────────────────────────


async def test_made_on_a_card_that_says_what_it_is(it: Setup) -> None:
    card = await it.router.execute(call(CREATE, {"name": "Invoices", "job": JOB}), CTX)
    assert card.status == "confirmation_required"
    assert card.summary == f"New helper: Invoices. {JOB}"
    assert await it.helpers.all(OWNER) == []
    done = await it.router.resolve(card.pending_id or "", "confirm", tap())
    assert done.status == "succeeded"
    (h,) = await it.helpers.all(OWNER)
    assert (h.name, h.job) == ("Invoices", JOB)
    assert json.loads(done.content) == {
        "helper_id": h.id,
        "conversation_id": h.conversation_id,
    }


async def test_its_picture_is_hers_or_picked_from_the_job(it: Setup) -> None:
    args = {"name": "Invoices", "job": JOB, "icon": "leaf", "color": "rose"}
    card = await it.router.execute(call(CREATE, args), CTX)
    assert card.status == "confirmation_required"
    # The card's words stay text; the look rides beside them (`card_look`).
    assert card.summary == f"New helper: Invoices. {JOB}"
    assert card_look(args) == {"icon": "leaf", "color": "rose"}
    await it.router.resolve(card.pending_id or "", "confirm", tap())
    (h,) = await it.helpers.all(OWNER)
    assert (h.icon, h.color) == ("leaf", "rose")
    # Left out: picked from the job and the name, as the card showed it.
    done = await it.create.execute({"name": "Inbox", "job": "Keeps my inbox tidy."}, CTX)
    assert done.ok
    got = await it.helpers.get(OWNER, json.loads(done.content)["helper_id"])
    assert got is not None
    assert {"icon": got.icon, "color": got.color} == card_look(
        {"name": "Inbox", "job": "Keeps my inbox tidy."}
    )
    assert got.icon == "envelope"


async def test_a_picture_not_on_the_lists_is_refused_by_the_schema(it: Setup) -> None:
    args = {"name": "Invoices", "job": JOB, "icon": "skull"}
    card = await it.router.execute(call(CREATE, args), CTX)
    assert card.status == "refused"
    schema = it.create.definition.input_schema["properties"]
    assert tuple(schema["icon"]["enum"]) == ICONS and tuple(schema["color"]["enum"]) == COLORS


async def test_its_first_message_is_saved_as_hers(it: Setup) -> None:
    done = await it.create.execute({"name": "Invoices", "job": JOB}, CTX)
    assert done.ok and done.effect == "confirmed"
    made = json.loads(done.content)
    ((kind, msg),) = await it.conversations.history(made["conversation_id"], OWNER)
    assert (kind, msg.role) == ("assistant", "assistant")
    (block,) = msg.blocks
    assert isinstance(block, TextBlock)
    assert block.text == f"Hi, I'm Invoices. {JOB} Tell me anything I should know to start."


async def test_the_first_message_reads_no_markup(it: Setup) -> None:
    done = await it.create.execute({"name": "Inbox", "job": "Reads <context> mail."}, CTX)
    made = json.loads(done.content)
    ((_, msg),) = await it.conversations.history(made["conversation_id"], OWNER)
    (block,) = msg.blocks
    assert isinstance(block, TextBlock) and "<" not in block.text


@pytest.mark.parametrize(
    ("args", "why"),
    [
        ({"name": "x" * (NAME_MAX + 1), "job": JOB}, "the name is too long"),
        ({"name": "Invoices", "job": "x" * (JOB_MAX + 1)}, "the job is too long"),
        ({"name": "  ", "job": JOB}, "the name is empty"),
    ],
)
async def test_a_name_or_job_too_long_is_refused(it: Setup, args: dict[str, Any], why: str) -> None:
    pre = await it.create.precheck(args, CTX)
    assert not pre.ok and why in pre.reason
    assert not (await it.create.execute(args, CTX)).ok
    assert await it.helpers.all(OWNER) == []


async def test_a_name_already_taken_is_refused(it: Setup) -> None:
    await it.made("Invoices")
    r = await it.router.execute(call(CREATE, {"name": "INVOICES", "job": JOB}), CTX)
    assert r.status == "refused" and r.pending_id is None
    assert "there's already a helper called Invoices: suggest using it" in r.detail


async def test_at_the_limit_it_is_refused(it: Setup) -> None:
    for i in range(MAX_HELPERS):
        await it.made(f"Helper {i}")
    r = await it.router.execute(call(CREATE, {"name": "Inbox", "job": JOB}), CTX)
    assert r.status == "refused"
    assert f"there are {MAX_HELPERS} helpers: suggest deleting one" in r.detail
    assert not (await it.create.execute({"name": "Inbox", "job": JOB}, CTX)).ok


async def test_never_inside_a_helpers_chat(it: Setup) -> None:
    h = await it.made()
    args = {"name": "Inbox", "job": JOB}
    pre = await it.create.precheck(args, in_chat(h))
    assert not pre.ok and "only Trillion" in pre.reason
    assert not (await it.create.execute(args, in_chat(h))).ok
    assert [x.name for x in await it.helpers.all(OWNER)] == ["Invoices"]


@pytest.mark.parametrize("via", NOT_SAID)
async def test_never_made_when_nobody_said_it(it: Setup, via: str) -> None:
    ctx = dataclasses.replace(CTX, via=via)  # type: ignore[arg-type]
    args = {"name": "Inbox", "job": JOB}
    assert not (await it.create.precheck(args, ctx)).ok
    assert not (await it.create.execute(args, ctx)).ok
    assert await it.helpers.all(OWNER) == []


async def test_never_made_after_an_outside_read(it: Setup) -> None:
    ctx = dataclasses.replace(CTX, read_outside=True)
    r = await it.router.execute(call(CREATE, {"name": "Inbox", "job": JOB}), ctx)
    assert r.status == "refused" and r.pending_id is None
    assert "can't be made or changed after reading" in r.detail
    assert not (await it.create.execute({"name": "Inbox", "job": JOB}, ctx)).ok


# ── Changing one ─────────────────────────────────────────────────────


async def test_changed_on_a_card_in_its_own_chat(it: Setup) -> None:
    h = await it.made()
    args = {"helper": h.id, "name": "Bills", "job": "Pays the bills."}
    card = await it.router.execute(call(UPDATE, args), in_chat(h))
    assert card.status == "confirmation_required"
    assert card.summary == "Change Invoices: name to Bills, job to Pays the bills."
    done = await it.router.resolve(card.pending_id or "", "confirm", tap(in_chat(h)))
    assert done.status == "succeeded"
    now = await it.helpers.get(OWNER, h.id)
    assert now is not None and (now.name, now.job) == ("Bills", "Pays the bills.")


@pytest.mark.parametrize("where", ["chat", "other"])
async def test_changed_only_in_its_own_chat(it: Setup, where: str) -> None:
    h = await it.made()
    other = await it.made("Inbox")
    ctx = CTX if where == "chat" else in_chat(other)
    args = {"helper": h.id, "name": "Bills"}
    r = await it.router.execute(call(UPDATE, args), ctx)
    assert r.status == "refused" and "not this helper's chat" in r.detail
    assert not (await it.update.execute(args, ctx)).ok
    now = await it.helpers.get(OWNER, h.id)
    assert now is not None and now.name == "Invoices"


async def test_a_new_name_already_taken_is_refused(it: Setup) -> None:
    h = await it.made()
    await it.made("Inbox")
    pre = await it.update.precheck({"helper": h.id, "name": "inbox"}, in_chat(h))
    assert not pre.ok and "there's already a helper called Inbox" in pre.reason


@pytest.mark.parametrize("via", NOT_SAID)
async def test_never_changed_when_nobody_said_it(it: Setup, via: str) -> None:
    h = await it.made()
    ctx = in_chat(h, via=via)
    args = {"helper": h.id, "name": "Bills"}
    assert not (await it.update.precheck(args, ctx)).ok
    assert not (await it.update.execute(args, ctx)).ok


async def test_never_changed_after_an_outside_read(it: Setup) -> None:
    h = await it.made()
    ctx = in_chat(h, read_outside=True)
    pre = await it.update.precheck({"helper": h.id, "name": "Bills"}, ctx)
    assert not pre.ok and "after reading" in pre.reason


# ── Teaching one ─────────────────────────────────────────────────────


async def test_a_narrowing_stands_at_once_and_is_read_back(it: Setup) -> None:
    h = await it.made()
    done = await it.router.execute(call(NARROW, {"helper": h.id, "text": NARROWS}), in_chat(h))
    assert done.status == "succeeded"
    assert "Noted for Invoices: Don't include refunds." in done.content
    assert await it.notes(h) == NARROWS


async def test_text_that_widens_is_refused_as_a_narrowing(it: Setup) -> None:
    h = await it.made()
    args = {"helper": h.id, "text": WIDENS}
    r = await it.router.execute(call(NARROW, args), in_chat(h))
    assert r.status == "refused" and "use helper_widen" in r.detail
    direct = await it.narrow.execute(args, in_chat(h))
    assert not direct.ok and "use helper_widen" in direct.content
    assert await it.notes(h) == ""


async def test_a_widening_is_a_card_and_stands_once_approved(it: Setup) -> None:
    h = await it.made()
    card = await it.router.execute(call(WIDEN, {"helper": h.id, "text": WIDENS}), in_chat(h))
    assert card.status == "confirmation_required"
    assert card.summary == f"Add to Invoices: {WIDENS}"
    assert await it.notes(h) == ""
    done = await it.router.resolve(card.pending_id or "", "confirm", tap(in_chat(h)))
    assert done.status == "succeeded" and "Noted for Invoices" in done.content
    assert await it.notes(h) == WIDENS


@pytest.mark.parametrize("name", [NARROW, WIDEN])
async def test_taught_only_in_its_own_chat(it: Setup, name: str) -> None:
    h = await it.made()
    text = NARROWS if name == NARROW else WIDENS
    r = await it.router.execute(call(name, {"helper": h.id, "text": text}), CTX)
    assert r.status == "refused" and "not this helper's chat" in r.detail
    assert await it.notes(h) == ""


@pytest.mark.parametrize("via", NOT_SAID)
async def test_never_widened_when_nobody_said_it(it: Setup, via: str) -> None:
    h = await it.made()
    ctx = in_chat(h, via=via)
    args = {"helper": h.id, "text": WIDENS}
    assert not (await it.widen.precheck(args, ctx)).ok
    assert not (await it.widen.execute(args, ctx)).ok
    assert await it.notes(h) == ""


async def test_never_widened_after_an_outside_read(it: Setup) -> None:
    h = await it.made()
    ctx = in_chat(h, read_outside=True)
    r = await it.router.execute(call(WIDEN, {"helper": h.id, "text": WIDENS}), ctx)
    assert r.status == "refused" and "after reading" in r.detail


async def test_the_notes_stop_at_their_cap_and_say_so(it: Setup) -> None:
    h = await it.made()
    assert await it.helpers.add_note(OWNER, h.id, "x" * (NOTES_MAX - 10))
    done = await it.narrow.execute({"helper": h.id, "text": NARROWS}, in_chat(h))
    assert not done.ok and done.error_kind == "full" and "full" in done.content


# ── Handing it work ──────────────────────────────────────────────────


async def test_asking_makes_a_due_now_ask_for_it(it: Setup) -> None:
    h = await it.made()
    request = "Which invoices are overdue, and by how much in all? " * 2
    done = await it.router.execute(call(ASK, {"helper": h.id, "request": request}), CTX)
    assert done.status == "succeeded"
    assert "say 'Asking Invoices' in one line and carry on" in done.content
    (s,) = await it.schedules.active(OWNER)
    assert (s.kind, s.helper_id, s.repeat, s.target) == ("ask", h.id, None, None)
    assert (s.next_due_ms, s.created_ms, s.created_in) == (NOW, NOW, "chat-1")
    assert s.body == request.strip()
    assert s.title == f"Invoices: {' '.join(request.split())[: MAX_TITLE - 10]}"
    assert (s.zone, s.at_local) == ("America/New_York", "2026-11-05T09:00")
    # The scheduler finds it due now.
    assert [x.id for x in await it.schedules.due(NOW)] == [s.id]


async def test_an_unknown_helper_is_refused(it: Setup) -> None:
    r = await it.router.execute(call(ASK, {"helper": "0" * 32, "request": "Hi."}), CTX)
    assert r.status == "refused" and "no such helper" in r.detail
    assert await it.schedules.active(OWNER) == []


async def test_a_paused_helper_is_not_handed_work(it: Setup) -> None:
    h = await it.made()
    assert await it.helpers.pause(OWNER, h.id, NOW)
    r = await it.router.execute(call(ASK, {"helper": h.id, "request": "Hi."}), CTX)
    assert r.status == "refused"
    assert "Invoices is paused: answer yourself and say so in a few words" in r.detail
    assert not (await it.ask.execute({"helper": h.id, "request": "Hi."}, CTX)).ok
    assert await it.schedules.active(OWNER) == []


async def test_a_capped_helper_is_not_handed_work(it: Setup) -> None:
    h = await it.made()
    it.capped = True
    r = await it.router.execute(call(ASK, {"helper": h.id, "request": "Hi."}), CTX)
    assert r.status == "refused"
    assert "Invoices is at its spend cap: answer yourself and say so" in r.detail
    assert await it.schedules.active(OWNER) == []


async def test_a_helper_never_hands_work_on(it: Setup) -> None:
    h = await it.made()
    other = await it.made("Inbox")
    args = {"helper": other.id, "request": "Hi."}
    assert not (await it.ask.precheck(args, in_chat(h))).ok
    assert not (await it.ask.execute(args, in_chat(h))).ok
    assert await it.schedules.active(OWNER) == []


# ── Offering one ─────────────────────────────────────────────────────

OFFER = {"area": "email", "name": "Inbox", "job": "Keeps your inbox in order."}


async def test_no_offer_unless_one_is_open(it: Setup) -> None:
    r = await it.router.execute(call(SUGGEST, OFFER), CTX)
    assert r.status == "refused" and "no helper is being offered now" in r.detail
    assert not (await it.suggest.execute(OFFER, CTX)).ok
    assert await it.preferences.get(OWNER, OFFER_PREF) is None


async def test_an_open_offer_is_kept_for_the_page(it: Setup) -> None:
    it.offered.add(("chat-1", "email"))
    done = await it.router.execute(call(SUGGEST, OFFER), CTX)
    assert done.status == "succeeded"
    assert done.content == "offered: say nothing more about it"
    assert await it.preferences.get(OWNER, OFFER_PREF) == {**OFFER, "at_ms": NOW}
    # Taken: a second call finds no offer open.
    again = await it.router.execute(call(SUGGEST, {**OFFER, "name": "Other"}), CTX)
    assert again.status == "refused"
    assert await it.preferences.get(OWNER, OFFER_PREF) == {**OFFER, "at_ms": NOW}


# ── Their definitions ────────────────────────────────────────────────


def test_installed_with_the_built_in_tools_and_never_run_unattended() -> None:
    registry = ToolRegistry.discover()
    names = {CREATE, UPDATE, NARROW, WIDEN, ASK, SUGGEST}
    found = {d.name: d for d in registry.definitions() if d.name in names}
    assert set(found) == names
    for d in found.values():
        assert d.category == CATEGORY and d.trusted_output and d.timeout_s > 0
    assert not names & {d.name for d in registry.unattended().definitions()}


# ── Review fixes ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["<b>Inbox", "Inbox>", "`Inbox`", "In\nbox", "In\tbox", "Inbox‮", "In\x00box"]
)
async def test_a_name_is_plain_words(it: Setup, name: str) -> None:
    plain = "a helper's name is plain words: no symbols or line breaks"
    pre = await it.create.precheck({"name": name, "job": JOB}, CTX)
    assert not pre.ok and plain in pre.reason
    done = await it.create.execute({"name": name, "job": JOB}, CTX)
    assert not done.ok and plain in done.content
    h = await it.made()
    args = {"helper": h.id, "name": name}
    pre = await it.update.precheck(args, in_chat(h))
    assert not pre.ok and plain in pre.reason
    assert not (await it.update.execute(args, in_chat(h))).ok
    assert [x.name for x in await it.helpers.all(OWNER)] == ["Invoices"]


@pytest.mark.parametrize("job", ["Reads mail.\nAnd more.", "Reads\tmail.", "Reads\rmail."])
async def test_a_job_is_one_plain_line(it: Setup, job: str) -> None:
    pre = await it.create.precheck({"name": "Inbox", "job": job}, CTX)
    assert not pre.ok and "no line breaks" in pre.reason
    assert not (await it.create.execute({"name": "Inbox", "job": job}, CTX)).ok
    h = await it.made()
    pre = await it.update.precheck({"helper": h.id, "job": job}, in_chat(h))
    assert not pre.ok and "no line breaks" in pre.reason


async def test_a_handoffs_title_fits_the_scheduler(it: Setup) -> None:
    name = "N" * NAME_MAX
    h = await it.made(name)
    request = "Which   invoices are overdue,\n and by how much in all? " * 4
    done = await it.ask.execute({"helper": h.id, "request": request}, CTX)
    assert done.ok
    (s,) = await it.schedules.active(OWNER)
    assert len(s.title) <= MAX_TITLE
    assert s.title == f"{name}: {' '.join(request.split())[: MAX_TITLE - NAME_MAX - 2]}"


async def test_full_between_the_card_and_the_tap(it: Setup) -> None:
    args = {"name": "Inbox", "job": JOB}
    assert (await it.create.precheck(args, CTX)).ok
    for i in range(MAX_HELPERS):
        await it.made(f"Helper {i}")
    done = await it.create.execute(args, CTX)
    assert not done.ok and done.content == f"there are {MAX_HELPERS} helpers: suggest deleting one"
    assert len(await it.helpers.all(OWNER)) == MAX_HELPERS


async def test_taken_between_the_card_and_the_tap(it: Setup) -> None:
    args = {"name": "Inbox", "job": JOB}
    assert (await it.create.precheck(args, CTX)).ok
    await it.made("inbox")
    done = await it.create.execute(args, CTX)
    assert not done.ok and done.content == "there's already a helper called inbox: suggest using it"


async def test_full_or_taken_by_the_store_itself(
    it: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store checks again in its own transaction: what it says is what she hears."""

    async def nothing_seen(owner_id: str) -> list[OwnHelper]:
        return []

    await it.made("Inbox")
    monkeypatch.setattr(it.helpers, "all", nothing_seen)
    taken = await it.create.execute({"name": "INBOX", "job": JOB}, CTX)
    assert not taken.ok and taken.error_kind == "taken"
    assert taken.content == "there's already a helper called INBOX: suggest using it"
    for i in range(MAX_HELPERS - 1):
        await it.made(f"Helper {i}")
    full = await it.create.execute({"name": "Bills", "job": JOB}, CTX)
    assert not full.ok and full.error_kind == "full"
    assert full.content == f"there are {MAX_HELPERS} helpers: suggest deleting one"


async def test_a_new_name_taken_between_the_card_and_the_tap(
    it: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await it.made()
    args = {"helper": h.id, "name": "Inbox"}
    assert (await it.update.precheck(args, in_chat(h))).ok
    await it.made("INBOX")
    done = await it.update.execute(args, in_chat(h))
    assert not done.ok and done.content == "there's already a helper called INBOX: suggest using it"

    # And when only the store sees it.
    async def nothing_seen(owner_id: str) -> list[OwnHelper]:
        return []

    monkeypatch.setattr(it.helpers, "all", nothing_seen)
    done = await it.update.execute(args, in_chat(h))
    assert not done.ok and done.error_kind == "taken"
    assert done.content == "there's already a helper called Inbox: suggest using it"
    now = await it.helpers.get(OWNER, h.id)
    assert now is not None and now.name == "Invoices"
