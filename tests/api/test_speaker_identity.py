"""§5.3a end to end in a session: the voice each spoken turn is judged in decides
whether its yes counts (SPN-14a, SPN-15a, SPN-18) and what she shares (PER-13).
A fake embedder hands out synthetic voices: no model, no audio worth the name."""

# ruff: noqa: F811, S311 - the fixture is shared; seeded randomness makes synthetic voices

from __future__ import annotations

import json
import math
import random
from collections import deque
from typing import Any

from tests.api.test_listening import hearing, rt, speak  # noqa: F401  (the fixture)
from tests.api.test_session import Page, idle
from tests.brain.fakes import say, use
from tests.voice.audio import room, tone
from trillion.api.runtime import Runtime
from trillion.api.session import GUEST_CARD, Session
from trillion.brain.types import ContextBlock, Msg
from trillion.voice import speaker as sp
from trillion.voice.types import SpeakerEmbedding, Word

MODEL = "fake-embedder"
DIM = 64


def direction(seed: int) -> tuple[float, ...]:
    rnd = random.Random(seed)
    return sp.unit([rnd.gauss(0, 1) for _ in range(DIM)])


def near(base: tuple[float, ...], s: float, seed: int) -> tuple[float, ...]:
    noise = direction(1000 + seed)
    dot = sum(a * b for a, b in zip(base, noise, strict=True))
    ortho = sp.unit([n - dot * b for n, b in zip(noise, base, strict=True)])
    return sp.unit([s * b + math.sqrt(1 - s * s) * o for b, o in zip(base, ortho, strict=True)])


OWNER = direction(1)
GUEST = direction(2)


class Voices:
    """An embedder that says whichever voice the test queued next."""

    name = MODEL

    def __init__(self) -> None:
        self.next: deque[SpeakerEmbedding | None] = deque()

    async def embed(
        self, pcm: bytes, *, start_s: float, words: tuple[Word, ...]
    ) -> SpeakerEmbedding | None:
        return self.next.popleft() if self.next else None


def voice(vector: tuple[float, ...], seed: int) -> SpeakerEmbedding:
    rnd = random.Random(seed)
    fp = tuple(rnd.getrandbits(16) | (1 << 16) for _ in range(60))
    return SpeakerEmbedding(vector=vector, model=MODEL, fingerprint=fp)


def enrol_owner(rt: Runtime) -> Voices:
    phrases = [voice(near(OWNER, 0.9, i), 10 + i) for i in range(3)]
    profile, _ = sp.enrol(MODEL, phrases, now_ms=1)
    assert profile is not None and rt.voiceprints is not None
    rt.voiceprints.save(profile)
    voices = Voices()
    rt.speakers = voices
    return voices


async def spoken(session: Session, stt: Any, words: str) -> None:
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.5))
    stt.stream.say(words, final=True)
    await speak(session, room(0.6))
    await idle(session)


def context_of(msg: Msg) -> str:
    return "".join(b.text for b in msg.blocks if isinstance(b, ContextBlock))


async def card_waiting(rt: Runtime, session: Session, page: Page) -> tuple[str, Any]:
    target = rt.granted_roots[0] / "a.txt"
    rt.provider._replies += [  # type: ignore[union-attr]
        use("file_create", {"path": str(target), "content": "hi"}, "c1"),
        say("Shall I?"),
    ]
    await session.handle({"type": "turn.text", "text": "write a"})
    await idle(session)
    return page.all("confirm.request")[0]["pending_id"], target


def confirm_then(rt: Runtime, card: str, call: str) -> None:
    rt.provider._replies += [  # type: ignore[union-attr]
        use("confirm_pending_action", {"pending_id": card}, call),
        say("Okay."),
    ]


def last_result(rt: Runtime) -> dict[str, Any]:
    msgs = rt.provider.calls[-1][1]  # type: ignore[union-attr]
    blocks = [b for b in msgs[-1].blocks if hasattr(b, "content")]
    return dict(json.loads(blocks[-1].content))


async def test_the_owners_spoken_yes_approves(rt: Runtime) -> None:
    stt = hearing(rt)
    voices = enrol_owner(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    card, target = await card_waiting(rt, session, page)
    confirm_then(rt, card, "c2")
    voices.next.append(voice(near(OWNER, 0.85, 50), 50))
    await spoken(session, stt, "yes, go ahead")
    assert target.read_text(encoding="utf-8") == "hi"
    # Kept for the replay check.
    assert len(rt.voiceprints.get().heard) == 1  # type: ignore[union-attr]
    await session.close()


async def test_a_guests_yes_approves_nothing_and_the_card_stays(rt: Runtime) -> None:
    stt = hearing(rt)
    voices = enrol_owner(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    card, target = await card_waiting(rt, session, page)
    confirm_then(rt, card, "c2")
    voices.next.append(voice(GUEST, 60))
    await spoken(session, stt, "yes, do it")
    assert not target.exists()
    assert last_result(rt)["reason"] == "not_owner"
    # She was told who she may be talking to, and the card's words were not passed on.
    sent = context_of(rt.provider.calls[-2][1][-1])  # type: ignore[union-attr]
    assert "not the owner's enrolled voice" in sent and GUEST_CARD in sent
    assert "a.txt" not in sent
    # The tap is unchanged: the owner taps, and it runs.
    rt.provider._replies.append(say("Done."))  # type: ignore[union-attr]
    await session.handle({"type": "confirm", "pending_id": card})
    await idle(session)
    assert target.read_text(encoding="utf-8") == "hi"
    await session.close()


async def test_a_recording_of_the_owners_yes_is_refused(rt: Runtime) -> None:
    stt = hearing(rt)
    voices = enrol_owner(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    yes = voice(near(OWNER, 0.85, 70), 70)
    # The owner says something; it is accepted, and kept.
    rt.provider._replies.append(say("Hi."))  # type: ignore[union-attr]
    voices.next.append(yes)
    await spoken(session, stt, "yes hello")
    card, target = await card_waiting(rt, session, page)
    confirm_then(rt, card, "c2")
    # The same recording, played back to approve the card.
    voices.next.append(yes)
    await spoken(session, stt, "yes hello")
    assert not target.exists()
    assert last_result(rt)["reason"] == "not_owner"
    await session.close()


async def test_an_unsure_yes_asks_again_then_the_owner_is_heard(rt: Runtime) -> None:
    stt = hearing(rt)
    voices = enrol_owner(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    card, target = await card_waiting(rt, session, page)
    confirm_then(rt, card, "c2")
    voices.next.append(None)  # too short to tell
    await spoken(session, stt, "yes")
    assert not target.exists()
    result = last_result(rt)
    assert result["reason"] == "voice_unsure" and "say the yes again" in result["detail"]
    confirm_then(rt, card, "c3")
    voices.next.append(voice(near(OWNER, 0.85, 80), 80))
    await spoken(session, stt, "yes, go ahead with it")
    assert target.read_text(encoding="utf-8") == "hi"
    await session.close()


async def test_a_guest_gets_nothing_personal(rt: Runtime) -> None:
    stt = hearing(rt)
    voices = enrol_owner(rt)
    await rt.memory.write(
        type="user",
        text="I take my coffee black.",
        moves=False,
        source="user_statement",
        now_ms=rt.clock(),
    )
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    rt.provider._replies += [  # type: ignore[union-attr]
        use("memory_recall", {"query": "coffee"}, "c1"),
        say("That's his to share."),
    ]
    voices.next.append(voice(GUEST, 90))
    await spoken(session, stt, "how does he take his coffee")
    first = rt.provider.calls[0]  # type: ignore[union-attr]
    # No memory rode with the words, and no folder is named to her.
    assert "coffee black" not in context_of(first[1][-1])
    assert all(str(rt.granted_roots[0]) not in b.text for b in first[0])
    # Only what anyone in the room may have is offered...
    offered = {t.name for t in rt.provider.tool_calls[0]}  # type: ignore[union-attr]
    assert "memory_recall" not in offered and "file_read" not in offered
    assert "web_fetch" in offered
    # ...and what was not offered is refused anyway, before it runs.
    assert last_result(rt)["reason"] == "private"
    assert "coffee black" not in json.dumps(last_result(rt))
    await session.close()


async def test_the_owners_turn_after_a_guests_is_the_owners_again(rt: Runtime) -> None:
    stt = hearing(rt)
    voices = enrol_owner(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    rt.provider._replies += [say("Hello."), say("Hi.")]  # type: ignore[union-attr]
    voices.next.append(voice(GUEST, 91))
    await spoken(session, stt, "hello there")
    voices.next.append(voice(near(OWNER, 0.85, 92), 92))
    await spoken(session, stt, "it's me again")
    offered = {t.name for t in rt.provider.tool_calls[-1]}  # type: ignore[union-attr]
    assert "memory_recall" in offered
    assert "enrolled voice" not in context_of(rt.provider.calls[-1][1][-1])  # type: ignore[union-attr]
    await session.close()


async def test_a_typed_turn_is_the_sessions(rt: Runtime) -> None:
    hearing(rt)
    enrol_owner(rt)
    rt.provider._replies.append(say("Sure."))  # type: ignore[union-attr]
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "what do you remember"})
    await idle(session)
    offered = {t.name for t in rt.provider.tool_calls[-1]}  # type: ignore[union-attr]
    assert "memory_recall" in offered
    await session.close()


async def test_nobody_enrolled_is_as_before(rt: Runtime) -> None:
    stt = hearing(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    card, target = await card_waiting(rt, session, page)
    confirm_then(rt, card, "c2")
    await spoken(session, stt, "yes, go ahead")
    assert target.read_text(encoding="utf-8") == "hi"
    await session.close()
