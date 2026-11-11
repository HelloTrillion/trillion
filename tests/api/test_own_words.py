"""SPN-20 end to end in a session: the owner, in their own voice, says the rule to keep,
and it is kept in one turn, recorded as approved by their words. Typed, a guest, an
unsure voice, the switch off, words she rephrased, or a file read this turn: the
usual card, or nothing at all for a voice that isn't the owner's (PER-13).

Also Settings → Your voice's switch, on /api/voiceprint."""

# ruff: noqa: F811 - the fixtures are shared

from __future__ import annotations

from typing import Any

from tests.api.test_listening import hearing, rt  # noqa: F401  (the fixture)
from tests.api.test_server import App, home, make  # noqa: F401
from tests.api.test_session import Page, idle
from tests.api.test_speaker_identity import (
    GUEST,
    OWNER,
    enrol_owner,
    near,
    spoken,
    voice,
)
from tests.api.test_voiceprint_api import PATH, get
from tests.brain.fakes import say, use
from trillion.api.runtime import OWN_WORDS_PREF, Runtime
from trillion.api.runtime import OWNER as LOCAL
from trillion.api.session import Session
from trillion.spine.confirm import OWN_WORDS, Status
from trillion.voice import speaker as sp

RULE = "never book anything before nine"
SAID = "keep a rule: never book anything before nine"


async def switch(rt: Runtime, on: bool) -> None:
    await rt.preferences.put(LOCAL, OWN_WORDS_PREF, on, rt.clock())


def keep_then(rt: Runtime, text: str = RULE) -> None:
    rt.provider._replies += [  # type: ignore[union-attr]
        use("rule_keep", {"text": text}, "k1"),
        say("Done, that rule is kept."),
    ]


async def started(rt: Runtime) -> tuple[Any, Any, Page, Session]:
    stt = hearing(rt)
    voices = enrol_owner(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    return stt, voices, page, session


def kept(rt: Runtime) -> list[str]:
    return [r.text for r in rt.rules.all()]


async def test_the_owner_keeps_a_rule_in_one_turn(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    keep_then(rt)
    voices.next.append(voice(near(OWNER, 0.85, 50), 50))
    await spoken(session, stt, SAID)
    assert kept(rt) == [RULE]
    # A card appears already settled, on their word.
    (card,) = page.all("confirm.request")
    (resolved,) = page.all("confirm.resolved")
    assert resolved["pending_id"] == card["pending_id"]
    assert resolved["status"] == "succeeded" and resolved["by"] == "own_words"
    assert "never book anything before nine" in card["summary"]
    # Recorded like any approval: approved by the owner's words, spoken.
    decisions = await rt.spine_store.list_decisions(card["pending_id"])
    approved = next(d for d in decisions if d.event == "approved")
    assert (approved.decided_by, approved.via) == (OWN_WORDS, "voice")
    a = await rt.spine_store.get_pending(card["pending_id"])
    assert a is not None and a.status is Status.SUCCEEDED
    await session.close()


async def test_the_switch_off_is_the_card(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    keep_then(rt)
    voices.next.append(voice(near(OWNER, 0.85, 51), 51))
    await spoken(session, stt, SAID)
    assert kept(rt) == []
    assert len(page.all("confirm.request")) == 1 and page.all("confirm.resolved") == []
    await session.close()


async def test_typed_is_the_card(rt: Runtime) -> None:
    _, _, page, session = await started(rt)
    await switch(rt, True)
    keep_then(rt)
    await session.handle({"type": "turn.text", "text": SAID})
    await idle(session)
    assert kept(rt) == []
    assert len(page.all("confirm.request")) == 1 and page.all("confirm.resolved") == []
    await session.close()


async def test_a_guest_keeps_nothing(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    keep_then(rt)
    voices.next.append(voice(GUEST, 60))
    await spoken(session, stt, SAID)
    # Default-private (PER-13): nothing personal runs for that voice, not even a card.
    assert kept(rt) == []
    assert page.all("confirm.resolved") == []
    await session.close()


async def test_an_unsure_voice_keeps_nothing(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    keep_then(rt)
    voices.next.append(None)  # could not be told
    await spoken(session, stt, SAID)
    assert kept(rt) == [] and page.all("confirm.resolved") == []
    await session.close()


async def test_words_she_rephrased_are_the_card(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    keep_then(rt, "never book anything before 9am")
    voices.next.append(voice(near(OWNER, 0.85, 52), 52))
    await spoken(session, stt, SAID)
    assert kept(rt) == []
    assert len(page.all("confirm.request")) == 1 and page.all("confirm.resolved") == []
    await session.close()


async def test_after_reading_a_file_this_turn_is_the_card(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    note = rt.granted_roots[0] / "note.txt"
    note.write_text("never book anything before nine", encoding="utf-8")
    rt.provider._replies += [  # type: ignore[union-attr]
        use("file_read", {"path": str(note)}, "r1"),
        use("rule_keep", {"text": RULE}, "k1"),
        say("I've put it on a card."),
    ]
    voices.next.append(voice(near(OWNER, 0.85, 53), 53))
    await spoken(session, stt, SAID)
    assert kept(rt) == []
    assert len(page.all("confirm.request")) == 1 and page.all("confirm.resolved") == []
    await session.close()


async def test_a_tap_note_after_it_is_not_heard_again(rt: Runtime) -> None:
    # The turn a card's tap starts answers the host's note, not the user's words: it
    # never takes the path, even in the same turn number as the spoken words.
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    target = rt.granted_roots[0] / "a.txt"
    rt.provider._replies += [  # type: ignore[union-attr]
        use("file_create", {"path": str(target), "content": "hi"}, "c1"),
        say("Shall I?"),
    ]
    voices.next.append(voice(near(OWNER, 0.85, 54), 54))
    await spoken(session, stt, "create a and " + SAID)
    (card,) = page.all("confirm.request")
    rt.provider._replies += [  # type: ignore[union-attr]
        use("rule_keep", {"text": RULE}, "k2"),
        say("Done."),
    ]
    await session.handle({"type": "confirm", "pending_id": card["pending_id"]})
    await idle(session)
    assert target.read_text(encoding="utf-8") == "hi"
    assert kept(rt) == []
    assert all(m.get("by") != "own_words" for m in page.all("confirm.resolved"))
    await session.close()


async def remember(rt: Runtime, text: str) -> str:
    done = await rt.memory.write(
        type="user", text=text, moves=False, source="user_statement", now_ms=rt.clock()
    )
    return done.memory.id


async def test_the_owner_forgets_a_memory_by_its_words(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    dentist = await remember(rt, "The dentist is on Tuesday at ten.")
    kept_one = await remember(rt, "I take my coffee black.")
    rt.provider._replies += [  # type: ignore[union-attr]
        use("memory_forget", {"about": "the dentist on Tuesday"}, "f1"),
        say("Done, I've forgotten it."),
    ]
    voices.next.append(voice(near(OWNER, 0.85, 55), 55))
    await spoken(session, stt, "forget the memory about the dentist on Tuesday")
    assert rt.memory.peek(dentist).status == "retracted"  # type: ignore[union-attr]
    assert rt.memory.peek(kept_one).current  # type: ignore[union-attr]
    (card,) = page.all("confirm.request")
    assert card["summary"] == "Forget: \u201cThe dentist is on Tuesday at ten.\u201d"
    assert page.all("confirm.resolved")[0]["by"] == "own_words"
    await session.close()


async def test_two_memories_with_those_words_forget_nothing_and_she_asks(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    first = await remember(rt, "The dentist is on Tuesday at ten.")
    second = await remember(rt, "Call the dentist back on Tuesday.")
    rt.provider._replies += [  # type: ignore[union-attr]
        use("memory_forget", {"about": "the dentist on Tuesday"}, "f1"),
        say("Which one?"),
    ]
    voices.next.append(voice(near(OWNER, 0.85, 56), 56))
    await spoken(session, stt, "forget the memory about the dentist on Tuesday")
    assert rt.memory.peek(first).current and rt.memory.peek(second).current  # type: ignore[union-attr]
    assert page.all("confirm.request") == []
    msgs = rt.provider.calls[-1][1]  # type: ignore[union-attr]
    assert "2 memories match" in str(msgs[-1].blocks)
    await session.close()


async def test_forgetting_by_its_words_typed_is_the_card(rt: Runtime) -> None:
    _, _, page, session = await started(rt)
    await switch(rt, True)
    dentist = await remember(rt, "The dentist is on Tuesday at ten.")
    rt.provider._replies += [  # type: ignore[union-attr]
        use("memory_forget", {"about": "the dentist on Tuesday"}, "f1"),
        say("Shall I?"),
    ]
    await session.handle(
        {"type": "turn.text", "text": "forget the memory about the dentist on Tuesday"}
    )
    await idle(session)
    assert rt.memory.peek(dentist).current  # type: ignore[union-attr]
    (card,) = page.all("confirm.request")
    assert "The dentist is on Tuesday" in card["summary"]
    assert page.all("confirm.resolved") == []
    await session.close()


async def test_the_owner_drops_a_rule_by_its_words(rt: Runtime) -> None:
    stt, voices, page, session = await started(rt)
    await switch(rt, True)
    rt.rules.keep(RULE)
    rt.rules.keep("never use emojis in a reply")
    rt.provider._replies += [  # type: ignore[union-attr]
        use("rule_drop", {"about": "book anything before nine"}, "d1"),
        say("Done, I've dropped it."),
    ]
    voices.next.append(voice(near(OWNER, 0.85, 57), 57))
    await spoken(session, stt, "drop the rule about book anything before nine")
    assert kept(rt) == ["never use emojis in a reply"]
    assert page.all("confirm.resolved")[0]["by"] == "own_words"
    await session.close()


# ── Settings → Your voice: the switch ─────────────────────────────────────


def test_the_switch_is_off_and_needs_a_voice(make: Any) -> None:
    app: App = make()
    assert get(app)["own_words"] is False
    r = app.client.put(PATH, json={"own_words": True}, headers=app.auth)
    assert r.status_code == 400 and r.json() == {"error": "not_enrolled"}
    assert get(app)["own_words"] is False


def test_the_switch_needs_a_token(make: Any) -> None:
    app: App = make()
    assert app.client.put(PATH, json={"own_words": True}).status_code == 401


def test_on_then_off_and_forgetting_the_voice_turns_it_off(make: Any) -> None:
    app: App = make()
    phrases = [
        sp.unit((1.0, 0.1, *([0.0] * 6))),
        sp.unit((1.0, 0.12, *([0.0] * 6))),
        sp.unit((1.0, 0.11, *([0.0] * 6))),
    ]
    from trillion.voice.types import SpeakerEmbedding

    profile, _ = sp.enrol("m", [SpeakerEmbedding(vector=v, model="m") for v in phrases], now_ms=1)
    assert profile is not None
    if app.rt.voiceprints is None:
        app.rt.voiceprints = sp.Voiceprints()
    app.rt.voiceprints.save(profile)
    on = app.client.put(PATH, json={"own_words": True}, headers=app.auth)
    assert on.status_code == 200 and on.json()["own_words"] is True
    off = app.client.put(PATH, json={"own_words": False}, headers=app.auth).json()
    assert off["own_words"] is False
    app.client.put(PATH, json={"own_words": True}, headers=app.auth)
    gone = app.client.request("DELETE", PATH, json={}, headers=app.auth).json()
    assert gone["enrolled"] is False and gone["own_words"] is False
    # Taught again, it starts off.
    app.rt.voiceprints.save(profile)
    assert get(app)["own_words"] is False


def test_the_switch_takes_only_a_boolean(make: Any) -> None:
    app: App = make()
    for body in ({"own_words": "yes"}, {}, {"own_words": 1}, {"own_words": "true"}):
        r = app.client.put(PATH, json=body, headers=app.auth)
        assert r.status_code == 422, body
