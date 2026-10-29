"""The urgent email watcher against a fake Gmail, a fake clock and a scripted model:
it starts from the moment it is turned on, asks for headers and the snippet only,
watches the senders its notes name, and says one line, or nothing, as the model
judges."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from tests.brain.fakes import ScriptedProvider, say
from tests.scheduler.conftest import FakeClock
from tests.unit.attention.test_runner import Door
from trillion.attention.mail import (
    AT_ONCE,
    FRESH_MS,
    LAG_MS,
    LIST_PATH,
    METADATA,
    Gone,
    GoogleMail,
    NotConnected,
    UrgentEmail,
    answer,
    query,
    watched,
)
from trillion.attention.runner import AttentionRunner, WatchContext
from trillion.attention.settings import DEFAULT, AttentionSettings
from trillion.brain.types import TextBlock, Usage
from trillion.connectors.google.client import GoogleCallError
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.attention_store import SqliteAttentionStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

MIN = 60_000
NOW = 1_800_000_000_000
ON = replace(DEFAULT, watchers={"meetings": False, "email": True, "payments": False})
LINE = "Sam needs the signed contract back today."


def message(
    mid: str,
    *,
    sender: str = "Sam Lee <sam@example.com>",
    subject: str = "Contract today",
    snippet: str = "Can you sign and send it back before five?",
    sent: int = NOW - MIN,
) -> dict[str, Any]:
    return {
        "id": mid,
        "snippet": snippet,
        "internalDate": str(sent),
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Thu, 29 Oct 2026 09:58:00 -0600"},
            ],
            # What a full read would carry: never asked for, never passed on.
            "parts": [{"body": {"data": "U0VDUkVUIEJPRFk="}}],
        },
    }


def verdict(urgent: Mapping[str, bool], line: str = LINE) -> str:
    """The judge's answer as the model writes it."""
    return json.dumps({"urgent": dict(urgent), "line": line})


@dataclass
class Gmail:
    found: dict[str, dict[str, Any]] = field(default_factory=dict)
    on: bool = True
    boom: bool = False
    listed: list[Mapping[str, str]] = field(default_factory=list)
    got: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    # Each read takes this long; these ids fail as given.
    slow_s: float = 0.0
    fail: dict[str, Exception] = field(default_factory=dict)
    busy: int = 0
    most: int = 0
    done: list[str] = field(default_factory=list)

    def connected(self) -> bool:
        return self.on

    async def messages(self, params: Mapping[str, str]) -> dict[str, Any]:
        self.listed.append(dict(params))
        if self.boom:
            raise ConnectionError("gmail said: secret subject")
        return {"messages": [{"id": k, "threadId": k} for k in self.found]}

    async def message(self, message_id: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.got.append((message_id, dict(params)))
        self.busy += 1
        self.most = max(self.most, self.busy)
        try:
            if self.slow_s:
                await asyncio.sleep(self.slow_s)
            if message_id in self.fail:
                raise self.fail[message_id]
        finally:
            self.busy -= 1
        self.done.append(message_id)
        return self.found[message_id]


def ctx(
    clock: FakeClock,
    *,
    last: int | None = NOW - 10 * MIN,
    cursor: str | None = None,
    notes: str = "",
    provider: Any = None,
) -> WatchContext:
    return WatchContext(
        owner="local",
        now_ms=clock(),
        clock=clock,
        settings=ON,
        notes=notes,
        cursor=cursor if cursor is not None else (str(last) if last is not None else None),
        last_check_ms=last,
        provider=provider,
    )


# ── Reading ───────────────────────────────────────────────────────────


async def test_the_first_check_after_turning_on_starts_from_now() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail({"m1": message("m1")})
    c = ctx(clock, last=None)
    assert await UrgentEmail(gmail).check(c) == []
    # Nothing asked of Gmail: the backlog is not news (D2).
    assert gmail.listed == [] and gmail.got == []
    assert c.cursor == str(NOW)


async def test_it_lists_unread_important_mail_since_its_place_and_asks_for_metadata_only() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail({"m1": message("m1"), "m2": message("m2")})
    since = NOW - 10 * MIN
    found = await UrgentEmail(gmail).check(ctx(clock, cursor=str(since)))
    assert gmail.listed == [
        {"q": f"is:unread is:important after:{since // 1000}", "maxResults": "20"}
    ]
    assert gmail.got == [
        ("m1", {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]}),
        ("m2", {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]}),
    ]
    assert METADATA["format"] == "metadata"
    f = found[0]
    assert f.key == "email:m1"
    assert f.facts == {
        "from": "Sam Lee <sam@example.com>",
        "subject": "Contract today",
        "date": "Thu, 29 Oct 2026 09:58:00 -0600",
        "preview": "Can you sign and send it back before five?",
    }
    assert f.detail == "Sam Lee — Contract today"
    # Fresh for half an hour from when Gmail received it, not from when it was found.
    assert f.fresh_until_ms == NOW - MIN + FRESH_MS and f.link is None
    # Never the body.
    assert "U0VDUkVU" not in str(found)


async def test_its_place_moves_on_but_looks_back_for_mail_filed_late() -> None:
    clock = FakeClock(NOW)
    c = ctx(clock, cursor=str(NOW - 10 * MIN))
    await UrgentEmail(Gmail()).check(c)
    assert c.cursor == str(NOW - LAG_MS)
    # Never back.
    c = ctx(clock, cursor=str(NOW - MIN))
    await UrgentEmail(Gmail()).check(c)
    assert c.cursor == str(NOW - MIN)


async def test_mail_that_came_long_ago_is_shown_not_spoken() -> None:
    clock = FakeClock(NOW)
    old = message("m1", sent=NOW - 2 * 60 * MIN)
    (f,) = await UrgentEmail(Gmail({"m1": old})).check(ctx(clock))
    assert f.fresh_until_ms is not None and f.fresh_until_ms < NOW
    # A date in the future is never fresher than now.
    ahead = message("m1", sent=NOW + 60 * MIN)
    (f,) = await UrgentEmail(Gmail({"m1": ahead})).check(ctx(clock))
    assert f.fresh_until_ms == NOW + FRESH_MS
    # Gmail not saying: from now.
    unsaid = message("m1")
    del unsaid["internalDate"]
    (f,) = await UrgentEmail(Gmail({"m1": unsaid})).check(ctx(clock))
    assert f.fresh_until_ms == NOW + FRESH_MS


async def test_a_long_gap_reads_a_day_back_at_most() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail()
    await UrgentEmail(gmail).check(ctx(clock, cursor=str(NOW - 7 * 24 * 60 * MIN)))
    assert f"after:{(NOW - 24 * 60 * MIN) // 1000}" in gmail.listed[0]["q"]


async def test_one_deadline_covers_the_list_and_every_read() -> None:
    # Each read is quick, but twenty of them five at a time are not.
    clock = FakeClock(NOW)
    gmail = Gmail({f"m{i}": message(f"m{i}") for i in range(20)}, slow_s=0.05)
    c = ctx(clock, cursor="123000")
    with pytest.raises(TimeoutError):
        await UrgentEmail(gmail, fetch_timeout_s=0.12).check(c)
    assert c.cursor == "123000"
    # No read is left running after the check fails.
    finished = len(gmail.done)
    await asyncio.sleep(0.1)
    assert len(gmail.done) == finished and gmail.busy == 0


async def test_reads_run_a_few_at_a_time() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail({f"m{i}": message(f"m{i}") for i in range(12)}, slow_s=0.01)
    found = await UrgentEmail(gmail).check(ctx(clock))
    assert len(found) == 12 and gmail.most == AT_ONCE


async def test_a_message_gone_since_the_list_is_skipped_but_a_failure_fails_the_check() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail({"m1": message("m1"), "m2": message("m2")}, fail={"m1": Gone()})
    found = await UrgentEmail(gmail).check(ctx(clock))
    assert [f.key for f in found] == ["email:m2"]
    # Anything else fails the check as itself, and the other reads are stopped.
    gmail = Gmail(
        {"m1": message("m1"), "m2": message("m2")},
        fail={"m1": ConnectionError("down")},
        slow_s=0.01,
    )
    c = ctx(clock, cursor="123000")
    with pytest.raises(ConnectionError):
        await UrgentEmail(gmail).check(c)
    assert c.cursor == "123000"


async def test_the_snippet_is_read_as_the_sender_wrote_it() -> None:
    clock = FakeClock(NOW)
    escaped = message("m1", snippet="Tom &amp; Jerry&#39;s &quot;plan&quot;")
    (f,) = await UrgentEmail(Gmail({"m1": escaped})).check(ctx(clock))
    assert f.facts["preview"] == 'Tom & Jerry\'s "plan"'


async def test_a_failed_read_leaves_its_place() -> None:
    clock = FakeClock(NOW)
    c = ctx(clock, cursor="123000")
    with pytest.raises(ConnectionError):
        await UrgentEmail(Gmail(boom=True)).check(c)
    assert c.cursor == "123000"


async def test_the_snippet_and_headers_are_cut_to_one_short_line() -> None:
    clock = FakeClock(NOW)
    long = message("m1", subject="Re:\n" + "x" * 500, snippet="y\n" * 400)
    (f,) = await UrgentEmail(Gmail({"m1": long})).check(ctx(clock))
    assert len(f.facts["preview"]) <= 200 and "\n" not in f.facts["preview"]
    assert len(f.facts["subject"]) <= 200 and "\n" not in f.facts["subject"]


async def test_a_sender_with_no_name_is_shown_by_address() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail({"m1": message("m1", sender="sam@example.com", subject="")})
    (f,) = await UrgentEmail(gmail).check(ctx(clock))
    assert f.detail == "sam@example.com — (no subject)"


async def test_an_id_that_is_not_gmails_is_never_asked_for() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail({"../drafts": message("x"), "m1": message("m1")})
    found = await UrgentEmail(gmail).check(ctx(clock))
    assert [f.key for f in found] == ["email:m1"] and [g for g, _ in gmail.got] == ["m1"]


def test_watched_senders_come_from_watch_lines_with_a_plain_address() -> None:
    notes = "\n".join(
        [
            "Not the bank newsletters.",
            "watch: Sam@Example.com",
            "WATCH:  ana@example.com ",
            "watch: sam@example.com",
            "watch: ana@example.com OR in:anywhere",
            "watch: {is:read}",
            "watch: nobody",
            "please watch: cy@example.com",
        ]
    )
    assert watched(notes) == ["sam@example.com", "ana@example.com"]


def test_the_query_ors_important_with_each_watched_sender() -> None:
    assert query(1700, []) == "is:unread is:important after:1700"
    assert query(1700, ["sam@example.com", "ana@example.com"]) == (
        "is:unread after:1700 {is:important from:sam@example.com from:ana@example.com}"
    )


async def test_the_notes_watch_lines_reach_the_query() -> None:
    clock = FakeClock(NOW)
    gmail = Gmail()
    await UrgentEmail(gmail).check(ctx(clock, notes="watch: sam@example.com"))
    assert "{is:important from:sam@example.com}" in gmail.listed[0]["q"]


# ── Judging ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "got"),
    [
        ('{"urgent": {"1": true, "2": false}, "line": "Sam wrote."}', (["1"], "Sam wrote.")),
        ('```json\n{"urgent": {"1": true}, "line": "Sam wrote."}\n```', (["1"], "Sam wrote.")),
        (
            'Here you go: {"urgent": {"2": true}, "line": "Ana wrote."} Hope that helps.',
            (["2"], "Ana wrote."),
        ),
        ('{"urgent": {"1": false, "2": false}, "line": ""}', ([], None)),
        ('{"urgent": {}, "line": ""}', ([], None)),
        # Numbers it was not given, and anything but true, are not urgent.
        ('{"urgent": {"9": true, "1": "yes", "2": 1}, "line": "x"}', ([], "x")),
        # Urgent with no usable line: told, unspoken.
        ('{"urgent": {"1": true}, "line": 3}', (["1"], None)),
        ('{"urgent": {"1": true}}', (["1"], None)),
        ('{"urgent": {"1": true}, "line": "  "}', (["1"], None)),
        ('{"urgent": {"1": true}, "line": "Call 555-123-4567."}', (["1"], None)),
        ("Sam wrote about the contract.", None),
        ('{"urgent": ["1"], "line": "x"}', None),
        ('["1"]', None),
        ('{"urgent": {"1": tru', None),
    ],
)
def test_the_answer_is_strict_json_judging_each_given_number(
    text: str, got: tuple[list[str], str | None] | None
) -> None:
    assert answer(text, ["1", "2"]) == got


async def judged(provider: ScriptedProvider, notes: str = "") -> Any:
    clock = FakeClock(NOW)
    w = UrgentEmail(Gmail({"m1": message("m1"), "m2": message("m2", subject="Newsletter")}))
    c = ctx(clock, notes=notes, provider=provider)
    finds = await w.check(c)
    return await w.judge(c, finds)


async def test_the_judge_is_one_call_with_no_tools_and_the_mail_wrapped() -> None:
    provider = ScriptedProvider(say(verdict({"1": True, "2": False})))
    said = await judged(provider, notes="Not the newsletters.")
    assert said.text == LINE and [f.key for f in said.told] == ["email:m1"]
    assert said.usage != Usage()
    ((_, messages),) = provider.calls
    assert provider.tool_calls == [[]]
    (block,) = messages[0].blocks
    assert isinstance(block, TextBlock)
    inside, notes = block.text.split("</untrusted_tool_result>", 1)
    assert "Contract today" in inside and "Contract today" not in notes
    # Numbered for the judge; Gmail's ids never reach it.
    assert '"n": "1"' in inside and '"n": "2"' in inside
    assert "m1" not in block.text and "m2" not in block.text
    # The user's notes are theirs: outside the untrusted part.
    assert "Not the newsletters." in notes and "Not the newsletters." not in inside


async def test_each_number_is_judged_on_its_own() -> None:
    said = await judged(ScriptedProvider(say(verdict({"1": False, "2": True}))))
    assert [f.key for f in said.told] == ["email:m2"]


async def test_a_line_repeating_a_number_or_link_from_the_mail_is_not_said() -> None:
    for line in ("Sam says call 555 123 4567.", "Sam wants you at https://x.example."):
        said = await judged(ScriptedProvider(say(verdict({"1": True}, line))))
        assert [f.key for f in said.told] == ["email:m1"] and said.text is None


async def test_an_answer_that_does_not_parse_says_nothing() -> None:
    said = await judged(ScriptedProvider(say("Sam needs you!")))
    assert said.told == [] or said.told == ()
    assert said.text is None


async def test_no_model_says_nothing() -> None:
    clock = FakeClock(NOW)
    w = UrgentEmail(Gmail({"m1": message("m1")}))
    c = ctx(clock)
    said = await w.judge(c, await w.check(c))
    assert list(said.told) == [] and said.text is None


async def test_a_judge_that_hangs_says_nothing() -> None:
    class Hangs(ScriptedProvider):
        async def stream(self, **kw: Any) -> AsyncIterator[Any]:
            await asyncio.sleep(10)
            yield None

    clock = FakeClock(NOW)
    w = UrgentEmail(Gmail({"m1": message("m1")}), timeout_s=0.05)
    c = ctx(clock, provider=Hangs())
    said = await w.judge(c, await w.check(c))
    assert list(said.told) == []


# ── In the runner: once, one notice, silence ─────────────────────────


@dataclass
class Rig:
    tmp: Path
    clock: FakeClock
    gmail: Gmail
    provider: ScriptedProvider | None = None
    door: Door = field(default_factory=Door)
    settings: AttentionSettings = ON
    conns: list[Any] = field(default_factory=list)
    store: SqliteAttentionStore | None = None

    async def runner(self) -> AttentionRunner:
        a, b = await connect(self.tmp / "t.db"), await connect(self.tmp / "t.db")
        await migrate(a)
        self.conns += [a, b]
        self.store = SqliteAttentionStore(a)
        r = AttentionRunner(
            self.store,
            SqliteScheduleStore(b),
            self.door,
            clock=self.clock,
            owner="local",
            provider=lambda: self.provider,
            settings=lambda: self.settings,
        )
        r.add(UrgentEmail(self.gmail))
        return r

    def found_now(self) -> None:
        """Its mail came a minute before the check that finds it."""
        sent = NOW + 10 * MIN - MIN
        for m in self.gmail.found.values():
            m["internalDate"] = str(sent)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path, FakeClock(NOW), Gmail({"m1": message("m1")}))
    try:
        yield r
    finally:
        for c in r.conns:
            await c.close()


async def turned_on(rig: Rig) -> AttentionRunner:
    """A runner whose first check, the moment it was turned on, is done."""
    r = await rig.runner()
    await r.tick()
    assert rig.gmail.listed == []
    rig.clock.advance(10 * MIN)
    return r


async def test_urgent_mail_is_one_notice_with_its_detail(rig: Rig) -> None:
    rig.gmail.found["m2"] = message("m2", sender="Ana <ana@example.com>", subject="Server down")
    rig.found_now()
    rig.provider = ScriptedProvider(say(verdict({"1": True, "2": True})))
    r = await turned_on(rig)
    await r.tick()
    (n,) = rig.door.notices
    assert (n.kind, n.title, n.text) == ("heads_up", "Important email", LINE)
    assert n.detail == "Sam Lee — Contract today; Ana — Server down"
    assert n.fresh_until_ms == rig.clock() - MIN + FRESH_MS
    # Once: the next check finds the same mail and says nothing more.
    rig.clock.advance(10 * MIN)
    await r.tick()
    assert len(rig.door.notices) == 1 and len(rig.provider.calls) == 1


async def test_mail_judged_not_urgent_is_silent(rig: Rig) -> None:
    rig.provider = ScriptedProvider(say('{"urgent": {"1": false}, "line": ""}'))
    r = await turned_on(rig)
    await r.tick()
    assert rig.door.notices == []
    assert rig.store is not None
    (f,) = await rig.store.recent("local", rig.clock())
    assert (f.key, f.status) == ("email:m1", "silent")


async def test_an_answer_that_does_not_parse_is_no_notice(rig: Rig) -> None:
    rig.provider = ScriptedProvider(say("URGENT!!! Sam needs you"))
    r = await turned_on(rig)
    await r.tick()
    assert rig.door.notices == []


async def test_urgent_with_no_usable_line_is_shown_by_title_and_detail(rig: Rig) -> None:
    rig.provider = ScriptedProvider(say(verdict({"1": True}, "x" * 400)))
    r = await turned_on(rig)
    await r.tick()
    (n,) = rig.door.notices
    assert (n.title, n.text, n.detail) == ("Important email", None, "Sam Lee — Contract today")


async def test_with_email_off_or_gmail_not_connected_nothing_is_read(rig: Rig) -> None:
    rig.settings = DEFAULT
    r = await rig.runner()
    await r.tick()
    rig.settings = ON
    rig.gmail.on = False
    rig.clock.advance(10 * MIN)
    await r.tick()
    assert rig.gmail.listed == [] and rig.door.notices == []


# ── The real Gmail: the same Google client and account the Gmail tools use ──


class _Google:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    gone = False

    async def call(self, client: object, email: str, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((client, email, method, path, kw))
        if self.gone:
            raise GoogleCallError("not_found", "Google found nothing with that id")
        return {"messages": []}


class _Saved:
    def __init__(self, accounts: list[str]) -> None:
        self.accounts = accounts

    def with_service(self, service: str) -> list[str]:
        return list(self.accounts) if service == "gmail" else []


class _Connection:
    def __init__(self, accounts: list[str], client: object | None = "client") -> None:
        self.saved = _Saved(accounts)
        self.google = _Google()
        self._client = client

    def client(self) -> object | None:
        return self._client


async def test_google_mail_asks_as_the_first_gmail_account() -> None:
    conn = _Connection(["me@example.com", "work@example.com"])
    mail = GoogleMail(lambda: conn)  # type: ignore[arg-type, return-value]
    assert mail.connected()
    await mail.messages({"q": "x"})
    await mail.message("m1", METADATA)
    assert conn.google.calls == [
        ("client", "me@example.com", "GET", LIST_PATH, {"params": {"q": "x"}}),
        ("client", "me@example.com", "GET", f"{LIST_PATH}/m1", {"params": dict(METADATA)}),
    ]
    assert LIST_PATH == "/gmail/v1/users/me/messages"
    with pytest.raises(ValueError):
        await mail.message("../drafts", METADATA)
    # A message gone since the list is Gone; the list's own failure is Google's.
    conn.google.gone = True
    with pytest.raises(Gone):
        await mail.message("m1", METADATA)
    with pytest.raises(GoogleCallError):
        await mail.messages({"q": "x"})


async def test_google_mail_not_connected() -> None:
    assert not GoogleMail(lambda: None).connected()
    none = _Connection([])
    assert not GoogleMail(lambda: none).connected()  # type: ignore[arg-type, return-value]
    with pytest.raises(NotConnected):
        await GoogleMail(lambda: none).messages({})  # type: ignore[arg-type, return-value]
    unset = _Connection(["me@example.com"], client=None)
    with pytest.raises(NotConnected):
        await GoogleMail(lambda: unset).messages({})  # type: ignore[arg-type, return-value]
