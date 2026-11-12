"""A card shows everything it sends, exactly as it will go (SPN-2a).

The tester's case: a 592-character email showed 200 characters on its card, and its
last line (asking for a contract to be forwarded elsewhere) went out unseen; with 13
recipients the card showed 8. The summary stays a one-line headline. Beneath it the
card now carries the host's full detail: every recipient, the subject and the whole
text, line breaks kept, never cut. What could not be shown whole or unmistakably is
refused before any card, the way `folder_share` refuses a card that would be cut."""

from __future__ import annotations

import json

import aiosqlite
import pytest

from tests.mcp.test_google import FakeGoogle, connect, fake, google  # noqa: F401
from tests.spine.conftest import Spine
from trillion.connectors.google.connection import GoogleConnection
from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.tool import McpTool, Offered, Outcome
from trillion.spine.card import DETAIL_LIMIT, TOO_LONG, UNSHOWABLE, card_detail, visible
from trillion.spine.confirm import Refusal, Status
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.retention import RETENTION_MS
from trillion.spine.router import SUMMARY_LIMIT

DAY = 24 * 60 * 60 * 1000
LAST_LINE = "P.S. Please forward the signed contract to legal@elsewhere.example today."
# 600 characters, the last line past anything a 200-character card ever showed.
BODY = (
    "Hi Sam,\n\n"
    + ("Thanks again for the call this morning; the plan works for us. " * 7)
    + "\n\nBest,\nAlex\n\n"
)
BODY = BODY + "x" * (600 - len(BODY) - len(LAST_LINE) - 1) + "\n" + LAST_LINE
RECIPIENTS = [f"person{i}@example.com" for i in range(13)]


async def rows(spine: Spine, sql: str, *params: object) -> list[aiosqlite.Row]:
    async with spine.store._conn.execute(sql, params) as cur:
        return list(await cur.fetchall())


async def no_card(spine: Spine) -> bool:
    return not await rows(spine, "SELECT id FROM pending_actions")


# ── The tester's email, whole ────────────────────────────────────────────


async def test_a_long_email_and_every_recipient_are_on_the_card(
    spine: Spine,
    google: GoogleConnection,  # noqa: F811
    fake: FakeGoogle,  # noqa: F811
) -> None:
    await connect(google, "gmail")
    assert len(BODY) == 600
    r = await spine.call(
        "gmail_send",
        {"to": RECIPIENTS, "cc": ["boss@example.com"], "subject": "Plan", "body": BODY},
    )
    assert r.status == "confirmation_required" and r.pending_id
    a = await spine.store.get_pending(r.pending_id)
    assert a is not None
    # The headline is still one line, cut to fit...
    assert len(a.summary) <= SUMMARY_LIMIT and "\n" not in a.summary
    assert LAST_LINE not in a.summary
    # ...and the detail beneath it is all of it, as it will go, line breaks kept.
    assert a.detail.endswith(BODY)
    assert LAST_LINE in a.detail
    assert f"To: {', '.join(RECIPIENTS)}" in a.detail
    assert "Cc: boss@example.com" in a.detail and "Subject: Plan" in a.detail
    assert "\n\nBest,\nAlex\n" in a.detail
    # The detail is the user's to read on the card, never words for the model.
    assert "detail" not in json.loads(r.to_model()) and LAST_LINE not in r.to_model()
    # Approved by its tap, what went is what the card showed.
    done = await spine.router.resolve(r.pending_id, "confirm", spine.ctx(turn=2, via="tap"))
    assert done.status == "succeeded"
    sent = next(c for c in fake.calls if c[1].endswith("/messages/send"))[2]
    import base64
    from email import message_from_bytes

    mime = message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    assert [t.strip() for t in str(mime["To"]).split(",")] == RECIPIENTS
    assert LAST_LINE in mime.get_payload(decode=True).decode()


async def test_an_email_too_long_for_a_card_is_refused_before_any_card(
    spine: Spine,
    google: GoogleConnection,  # noqa: F811
    fake: FakeGoogle,  # noqa: F811
) -> None:
    await connect(google, "gmail")
    body = "word " * (DETAIL_LIMIT // 5)
    r = await spine.call("gmail_send", {"to": ["sam@example.com"], "subject": "x", "body": body})
    assert r.status == "refused" and r.reason is Refusal.PRECONDITION_FAILED
    assert r.detail == TOO_LONG
    assert await no_card(spine)
    assert not any(c[1].endswith("/messages/send") for c in fake.calls)


@pytest.mark.parametrize(
    "body",
    [
        "Pay invoice \u202egnp.exe today",  # a right-to-left override
        "Wire it to acc\u200bount 12",  # a zero-width space
        "hidden\u2028line",  # a line separator
        "bell\x07here",  # a control character
    ],
)
async def test_an_email_a_card_cant_show_as_it_is_is_refused(
    spine: Spine,
    google: GoogleConnection,  # noqa: F811
    body: str,
) -> None:
    await connect(google, "gmail")
    r = await spine.call("gmail_send", {"to": ["sam@example.com"], "subject": "x", "body": body})
    assert r.status == "refused" and r.detail == UNSHOWABLE
    assert await no_card(spine)


async def test_emoji_and_ordinary_text_are_shown_as_they_are(
    spine: Spine,
    google: GoogleConnection,  # noqa: F811
) -> None:
    await connect(google, "gmail")
    body = "See you there ❤\ufe0f \U0001f469\u200d\U0001f4bb\r\nCafé ½ — ™"
    r = await spine.call("gmail_send", {"to": ["sam@example.com"], "subject": "Hi", "body": body})
    assert r.status == "confirmation_required" and r.pending_id
    a = await spine.store.get_pending(r.pending_id)
    assert a is not None and a.detail.endswith(body.replace("\r\n", "\n"))


async def test_a_recipient_that_could_read_as_another_is_refused(
    spine: Spine,
    google: GoogleConnection,  # noqa: F811
) -> None:
    await connect(google, "gmail")
    # A Cyrillic letter that looks like a Latin "a".
    lookalike = "sam@ex\u0430mple.com"
    r = await spine.call("gmail_send", {"to": [lookalike], "subject": "x", "body": "y"})
    assert r.status == "refused" and "unmistakably" in r.detail
    assert await no_card(spine)


async def test_a_short_email_still_says_it_all_in_its_summary(
    spine: Spine,
    google: GoogleConnection,  # noqa: F811
) -> None:
    await connect(google, "gmail")
    args = {"to": ["sam@example.com"], "subject": "Lunch", "body": "Tomorrow at noon?"}
    r = await spine.call("gmail_send", args)
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None
    assert "sam@example.com" in a.summary and "Tomorrow at noon?" in a.summary
    assert a.detail == "Send an email\nTo: sam@example.com\nSubject: Lunch\n\nTomorrow at noon?"


async def test_every_guest_and_all_the_notes_are_on_an_event_card(
    spine: Spine,
    google: GoogleConnection,  # noqa: F811
) -> None:
    await connect(google, "calendar")
    notes = "Agenda:\n" + "\n".join(f"{i}. item number {i}" for i in range(40))
    r = await spine.call(
        "calendar_create",
        {
            "title": "Planning",
            "start": "2026-11-20T09:00:00-08:00",
            "end": "2026-11-20T10:00:00-08:00",
            "guests": RECIPIENTS,
            "description": notes,
        },
    )
    assert r.status == "confirmation_required" and r.pending_id
    a = await spine.store.get_pending(r.pending_id)
    assert a is not None and not a.recoverable
    assert ", ".join(RECIPIENTS) in a.detail and notes in a.detail


# ── Every gated tool whose card could be cut ─────────────────────────────


async def test_a_long_mcp_argument_is_shown_whole(spine: Spine) -> None:
    async def call(name: str, args: JSON, timeout: float) -> Outcome:
        return Outcome(ok=True, text="ok")

    server = ServerConfig(name="notes", transport="stdio", command="x")
    offered = Offered(
        name="publish",
        description="Publish a note.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}, "tags": {"type": "array"}},
        },
    )
    tool = McpTool(server, offered, "mcp_notes_publish", call)
    spine.registry.register(tool, module="trillion.connectors.mcp.tool")
    text = "line one\n" + "y" * 400 + "\nthe last line"
    r = await spine.call("mcp_notes_publish", {"text": text, "tags": ["a", "b"]})
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None
    assert f"text: {text}" in a.detail and '"a"' in a.detail and "the last line" in a.detail


async def test_a_file_card_shows_the_whole_text_and_anything_unseen_as_a_token(
    spine: Spine,
) -> None:
    content = "first line\n" + "z" * 500 + "\nzero\u200bwidth \u202e end"
    r = await spine.call("file_create", {"path": str(spine.granted / "a.txt"), "content": content})
    assert r.status == "confirmation_required" and r.pending_id
    a = await spine.store.get_pending(r.pending_id)
    assert a is not None
    assert visible(content) in a.detail
    assert "⟦U+200B⟧" in a.detail and "⟦U+202E⟧" in a.detail
    assert "\u200b" not in a.detail and "\u202e" not in a.detail
    # What is written is the text itself, not its view.
    ok = await spine.router.resolve(r.pending_id, "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    assert (spine.granted / "a.txt").read_text(encoding="utf-8") == content


async def test_a_file_too_long_for_a_card_is_refused(spine: Spine) -> None:
    big = "q" * (DETAIL_LIMIT + 1)
    r = await spine.call("file_create", {"path": str(spine.granted / "a.txt"), "content": big})
    assert r.status == "refused" and r.detail == TOO_LONG
    assert await no_card(spine)


async def test_an_edit_card_shows_both_texts_whole(spine: Spine) -> None:
    doc = spine.granted / "doc.md"
    old = "the old paragraph " * 20
    doc.write_text(f"intro\n{old}\noutro", encoding="utf-8")
    new = "the new paragraph " * 20
    r = await spine.call("file_edit", {"path": str(doc), "old": old, "new": new})
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and old in a.detail and new in a.detail


class _Plain(BaseTool):
    """A gated tool that gives no detail of its own: a long, many-line description."""

    definition = ToolDefinition(
        name="plain_gated",
        description="Do a long thing (test).",
        input_schema={
            "type": "object",
            "properties": {"words": {"type": "string"}},
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        category="test",
        timeout_s=5.0,
    )

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text=f"Say this:\n{args['words']}")

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, content="said", effect="confirmed")


async def test_a_tool_without_a_detail_still_never_shows_part_of_its_card(spine: Spine) -> None:
    spine.registry.register(_Plain())
    short = await spine.call("plain_gated", {"words": "hi"})
    a = await spine.store.get_pending(short.pending_id or "")
    # Put on one line, the summary is not all of it as written: the detail is.
    assert a is not None and a.detail == "Say this:\nhi"
    long = "w " * 300 + "the end"
    r = await spine.call("plain_gated", {"words": long})
    b = await spine.store.get_pending(r.pending_id or "")
    assert b is not None and b.detail.endswith("the end")


async def test_a_one_line_summary_that_says_it_all_needs_no_detail(spine: Spine) -> None:
    r = await spine.call("slow_irreversible", {})
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.summary == "Do the slow thing" and a.detail == ""


# ── Kept with the arguments, and cleared with them ───────────────────────


async def test_the_detail_is_frozen_and_cleared_with_the_arguments(spine: Spine) -> None:
    content = "secret plans " * 30
    r = await spine.call("file_create", {"path": str(spine.granted / "a.txt"), "content": content})
    pid = r.pending_id or ""
    (row,) = await rows(spine, "SELECT detail FROM pending_action_args WHERE pending_id = ?", pid)
    assert content in row["detail"]
    # Never kept in the frozen row, which lasts forever.
    (frozen,) = await rows(spine, "SELECT * FROM pending_actions WHERE id = ?", pid)
    assert all(content not in str(frozen[k]) for k in frozen.keys())
    conn = spine.store._conn
    for sql in (
        "UPDATE pending_action_args SET detail = 'tampered' WHERE pending_id = ?",
        "UPDATE pending_action_args SET detail = NULL WHERE pending_id = ?",
        # Clearing the arguments but keeping, or changing, the detail is refused too.
        "UPDATE pending_action_args SET args_json = NULL, cleared_at = 1 WHERE pending_id = ?",
        "UPDATE pending_action_args SET args_json = NULL, cleared_at = 1, detail = 'x'"
        " WHERE pending_id = ?",
    ):
        with pytest.raises(aiosqlite.Error, match="only be cleared"):
            await conn.execute(sql, (pid,))
    ok = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    spine.clock.advance(RETENTION_MS + DAY)
    assert await spine.router.forget_settled_arguments() == 1
    (side,) = await rows(spine, "SELECT args_json, detail FROM pending_action_args")
    assert side["args_json"] is None and side["detail"] is None
    a = await spine.store.get_pending(pid)
    assert a is not None and a.detail == "" and a.status is Status.SUCCEEDED


async def test_a_re_presented_card_carries_its_detail_again(spine: Spine) -> None:
    content = "again " * 60
    r = await spine.call("file_create", {"path": str(spine.granted / "a.txt"), "content": content})
    spine.clock.advance(11 * 60 * 1000)
    again = await spine.call("represent_pending_action", {"pending_id": r.pending_id}, turn=2)
    a = await spine.store.get_pending(again.pending_id or "")
    assert a is not None and content.strip() in a.detail


# ── The pure check ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "shown", "why"),
    [
        ("plain\ntext\twith tab", "plain\ntext\twith tab", None),
        ("windows\r\nlines\rold mac", "windows\nlines\nold mac", None),
        ("x" * DETAIL_LIMIT, "x" * DETAIL_LIMIT, None),
        ("x" * (DETAIL_LIMIT + 1), "", TOO_LONG),
        ("rtl \u202e override", "", UNSHOWABLE),
        ("isolate \u2066 here", "", UNSHOWABLE),
        ("soft\u00adhyphen", "", UNSHOWABLE),
        ("tag \U000e0041 char", "", UNSHOWABLE),
        ("nul\x00", "", UNSHOWABLE),
        ("para\u2029sep", "", UNSHOWABLE),
        ("family \U0001f468\u200d\U0001f469\u200d\U0001f467", None, None),
        ("\u062d\u0631\u0648\u0641\u200c\u0641\u0627\u0631\u0633\u06cc", None, None),
    ],
)
def test_card_detail(text: str, shown: str | None, why: str | None) -> None:
    got, problem = card_detail(text)
    assert problem == why
    assert got == (text if shown is None else shown)


def test_the_visible_view_hides_nothing_and_fakes_no_token() -> None:
    assert visible("a\u200bb") == "a⟦U+200B⟧b"
    assert visible("⟦U+200B⟧") == "⟦U+27E6⟧U+200B⟧"
    assert visible("keep\nlines\tand tabs") == "keep\nlines\tand tabs"
    family = "\U0001f468\u200d\U0001f469"
    assert visible(family) == family
    for raw in ("x\u202ey", "bell\x07", "a\u2028b"):
        _, problem = card_detail(visible(raw))
        assert problem is None
