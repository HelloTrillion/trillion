"""BRN-9: a standing rule the user gave survives the conversation being summarized.

Plant a rule early, talk past the point where the oldest turns are summarized, and
show that every later model call still carries the rule, while the turn that set it
is gone from what the model is sent. Also: a card raised before the summary can
still be approved after it (SPN-3), the page keeps every message, and a reload
picks up the summary rather than the whole history.
"""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from tests.api.test_server import ORIGIN, App, Chat, home, make, only, propose  # noqa: F401
from tests.brain.fakes import USAGE, ScriptedProvider, say, use
from trillion.brain.types import (
    Done,
    ModelEvent,
    Msg,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolSchema,
)

RULE = "Never email my ex without asking twice."
NOTES = "Sam set a rule about email and talked about the garden."
FILLER = "Tell me more about the garden, the roses, the soil and the weather. " * 12


class Summarizing(ScriptedProvider):
    """The scripted model, plus a summarizer that answers whenever it is asked for notes."""

    def __init__(self, *replies: list[ModelEvent]) -> None:
        super().__init__(*replies)
        self.summaries: list[str] = []

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        quick: bool = False,
    ) -> AsyncIterator[ModelEvent]:
        if system and system[0].text.startswith("You are writing notes"):
            assert tools == []  # nothing in the old conversation can make this call act
            body = messages[0].blocks[0]
            self.summaries.append(body.text if isinstance(body, TextBlock) else "")
            yield TextDelta(text=NOTES)
            yield Done(stop_reason="end", usage=USAGE)
            return
        async for ev in super().stream(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens, quick=quick
        ):
            yield ev


def sent(app: App, call: int) -> tuple[str, str]:
    """The system text and everything else one model call was sent, as plain text."""
    system, messages = app.provider.calls[call]
    rest = "\n".join(
        str(getattr(b, "text", getattr(b, "content", ""))) for m in messages for b in m.blocks
    )
    return system[0].text, rest


def small(app: App) -> App:
    app.rt.compact_at, app.rt.compact_keep = 600, 150
    return app


def test_a_standing_rule_survives_the_conversation_being_summarized(make: Any) -> None:
    replies = [
        use("rule_keep", {"text": RULE}),
        say("I'll keep that as a rule. Tap to confirm."),
        say("Kept."),
        *[say("Roses like it sunny. " * 10) for _ in range(8)],
    ]
    app = small(make())
    app.provider = app.rt.provider = Summarizing(*replies)
    with app.connect() as chat:
        card = only(
            chat.say("From now on: never email my ex without asking twice."), "confirm.request"
        )
        assert card["summary"] == f"Keep as a standing rule: “{RULE}”"
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        chat.until("turn.complete")
        notices = []
        for _ in range(8):
            msgs = chat.say(FILLER)
            notices += [m for m in msgs if m["type"] == "notice"]

    # The conversation was summarized, and the summarizer was shown the rule being set.
    assert app.provider.summaries, "the conversation never grew past the summary point"
    assert "rule_keep" in app.provider.summaries[0]
    assert notices and notices[0]["code"] == "compacted"
    assert (app.rt.rules.all()[0].text, len(app.rt.rules.all())) == (RULE, 1)

    # Every call from the first one sent with a summary onwards.
    first = next(
        i for i in range(len(app.provider.calls)) if "conversation_summary" in sent(app, i)[1]
    )
    for call in range(first, len(app.provider.calls)):
        system, rest = sent(app, call)
        if system.startswith("You are writing notes"):
            continue
        # The rule is in front of her on every turn after the summary...
        assert "STANDING RULES" in system and RULE in system
        # ...though the turn that set it is no longer in what she is sent.
        assert "From now on: never email my ex" not in rest
        assert "conversation_summary" in rest and NOTES in rest


def test_a_card_raised_before_the_summary_can_still_be_answered_after_it(make: Any) -> None:
    app = small(make())
    app.provider = app.rt.provider = Summarizing(
        propose(app),
        say("Shall I write the note?"),
        *[say("Roses like it sunny. " * 10) for _ in range(6)],
        say("Done."),
    )
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        for _ in range(6):
            chat.say(FILLER)
        assert app.provider.summaries  # the turn that raised the card has been summarized away
        _, rest = sent(app, len(app.provider.calls) - 1)
        assert card["pending_id"] in rest  # still listed as waiting, from the database (SPN-3)
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        chat.until("turn.complete")
    assert (app.granted / "note.txt").read_text() == "hi"


def test_the_page_keeps_every_message_and_a_reload_keeps_the_summary(make: Any) -> None:
    app = small(make())
    app.provider = app.rt.provider = Summarizing(
        *[say(f"Answer {i}. " + "Roses like it sunny. " * 10) for i in range(7)]
    )
    with app.connect() as chat:
        for _ in range(6):
            chat.say(FILLER)
        conversation = chat.session["id"]
    assert app.provider.summaries
    calls_before = len(app.provider.calls)
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": app.rt.token, "resume": conversation})
        chat = Chat(ws)
        opening = chat.until("state")
        shown = only(opening, "history")["messages"]
        assert len(shown) == 12  # every question and answer, summarized or not
        chat.say("and the tulips?")
    _, rest = sent(app, calls_before)
    assert "conversation_summary" in rest and NOTES in rest
    assert "Answer 0." not in rest  # the summarized turns are not sent again


def test_when_the_summary_fails_the_turn_goes_ahead_on_the_whole_history(make: Any) -> None:
    class Broken(Summarizing):
        async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
            if kw["system"][0].text.startswith("You are writing notes"):
                yield Done(stop_reason="end", usage=USAGE)  # nothing written
                return
            async for ev in super().stream(**kw):
                yield ev

    app = small(make())
    app.provider = app.rt.provider = Broken(*[say("Roses. " * 30) for _ in range(5)])
    with app.connect() as chat:
        for _ in range(5):
            msgs = chat.say(FILLER)
            assert not [m for m in msgs if m["type"] == "notice"]
    _, rest = sent(app, len(app.provider.calls) - 1)
    assert rest.count("Tell me more about the garden") >= 5  # nothing was dropped
    assert "conversation_summary" not in rest


def test_rules_file_lives_in_the_data_folder(make: Any, home: Path) -> None:
    app = make(use("rule_keep", {"text": RULE}), say("Tap to confirm."), say("Kept."))
    with app.connect() as chat:
        card = only(chat.say("never email my ex without asking twice"), "confirm.request")
        chat.tap("confirm", card["pending_id"])
        chat.until("turn.complete")
    assert RULE in (home / "home" / "rules.md").read_text(encoding="utf-8")
