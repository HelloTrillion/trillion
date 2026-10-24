"""One stretch of listening: from the microphone opening to the words she is given.

The recognizer turns audio into segments; this decides when the user has
finished and assembles what they said (VOI-15a). Nothing heard is ever logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from trillion.voice.endpoint import (
    NOTHING_AFTER_S,
    EndOfSpeech,
    Onsets,
    Verdict,
    leads_on,
    sounds_finished,
)
from trillion.voice.overlap import keys
from trillion.voice.types import Heard, STTProvider, STTStream, VoiceError

logger = logging.getLogger(__name__)

# How long the recognizer gets to hand over its last words once the audio stops.
STREAMING_WRAP_UP_S = 3.0
BATCH_WRAP_UP_S = 60.0
# Audio that arrives while the recognizer is still connecting is kept, so the first
# syllable is never clipped (VOI-2). Bounded: ten seconds of 16 kHz PCM16.
_MAX_BACKLOG = 16_000 * 2 * 10
# The window that reopens after she speaks waits this long for a first word (VOI-8).
FOLLOW_UP_S = 3.0
# A recognizer reports a word about this long after it was said, and never sooner
# than the shorter time.
REPORT_LAG_S = 0.4
MIN_LAG_S = 0.15


@dataclass(frozen=True, kw_only=True)
class Utterance:
    text: str
    # The seam for knowing who spoke (VOI-15). Nothing fills it yet.
    speaker: tuple[float, ...] | None = None


def describe(
    provider: STTProvider | None, kind: str | None, choice: str | None
) -> dict[str, Any] | None:
    """What the page needs to know about listening: that it can, and how words arrive."""
    if provider is None or kind is None:
        return None
    return {
        "kind": kind,
        "choice": choice,
        "streaming": provider.streaming,
        "turns": provider.turns,
    }


class Listener:
    def __init__(
        self,
        provider: STTProvider,
        choice: str,
        *,
        on_partial: Callable[[str], Awaitable[None]],
        patience_s: float = NOTHING_AFTER_S,
        on_turn: Callable[[str, str], Awaitable[None]] | None = None,
        watch: bool = False,
    ) -> None:
        self._provider = provider
        self._choice = choice
        self._on_partial = on_partial
        self._on_turn = on_turn
        self._rules = self._new_rules(patience_s)
        # Watching while she speaks (VOI-9): every frame goes to the recognizer, its
        # turns come and go, and nothing ends until `hear` makes this a turn.
        self._watching = watch
        # The recognizer said its current turn is over; its next words start another.
        self._turn_ended = False
        # Words at the start of the recognizer's current turn already judged and let go.
        # Its reports repeat the whole turn so far, so they are skipped, not deleted, and
        # matched as words, not counted: it may revise them ("or writes" to "Skip that").
        self._forgotten: tuple[str, ...] = ()
        # When the user's first words were said (the loop's clock), once listening, and
        # whether a voice starting was heard then, or only the report is to go by.
        self.began: float | None = None
        self.began_heard = False
        # When the recognizer's current turn was first reported, and when voices
        # started in the audio, by the loop's clock, latest last.
        self._turn_seen: float | None = None
        self._onsets = Onsets()
        self._starts: deque[float] = deque(maxlen=32)
        self._stream: STTStream | None = None
        self._reader: asyncio.Task[None] | None = None
        self._backlog = bytearray()
        # Turns the recognizer called done on words that lead on ("... and"): kept,
        # and what follows joins them (VOI-12).
        self._held: list[str] = []
        self._holding = False
        self._finals: list[str] = []
        self._interim = ""
        self._error: VoiceError | None = None
        self._verdict: Verdict | None = None
        self.frames = 0

    def _new_rules(self, patience_s: float) -> EndOfSpeech:
        p = self._provider
        return EndOfSpeech(streaming=p.streaming, patience_s=patience_s, judged=p.turns)

    @property
    def open(self) -> bool:
        return self._stream is not None

    @property
    def watching(self) -> bool:
        return self._watching

    @property
    def verdict(self) -> Verdict | None:
        return self._verdict

    @property
    def heard_s(self) -> float:
        """Seconds of audio received."""
        return self._rules.now

    @property
    def peak(self) -> float:
        """The loudest the audio has been, 0 to 1."""
        return self._rules.peak

    @property
    def quiet_s(self) -> float:
        """How long the user has been quiet, in the audio received so far."""
        return self._rules.quiet_s

    def text(self) -> str:
        return " ".join(p for p in (*self._held, self._this_turn()) if p).strip()

    def _raw_turn(self) -> str:
        return " ".join(p for p in (*self._finals, self._interim) if p).strip()

    def _skipped(self, tokens: list[str]) -> int:
        """How many of the turn's words, from its start, are still the ones let go."""
        n = 0
        for heard, gone in zip(keys(" ".join(tokens)), self._forgotten, strict=False):
            if heard != gone:
                break
            n += 1
        return n

    def _this_turn(self) -> str:
        said = self._raw_turn()
        if not self._forgotten:
            return said
        tokens = said.split()
        return " ".join(tokens[self._skipped(tokens) :])

    @property
    def turn_done(self) -> bool:
        """The recognizer has ended its current turn (even one held as leading on)."""
        return self._turn_ended

    @property
    def turn_open(self) -> bool:
        """The recognizer's current turn has words and has not ended."""
        return bool(self._raw_turn()) and not self._turn_ended

    def whole_turn(self) -> str:
        """The recognizer's current turn as it reports it, words let go included (and
        words held, when it ended on one that leads on)."""
        return " ".join(p for p in (*self._held, self._raw_turn()) if p)

    def turn_text(self) -> str:
        """The words of the recognizer's current turn, less any forgotten. A turn it
        has ended stays current until its next words arrive."""
        return self._this_turn()

    def forget(self, words: int | None = None) -> None:
        """Let go of words already judged (VOI-9): this many more from the start of the
        current turn, or, with none given, everything heard so far."""
        tokens = self._raw_turn().split()
        if words is None:
            self._held = []
            upto = len(tokens)
        else:
            upto = self._skipped(tokens) + words
        self._forgotten = keys(" ".join(tokens[:upto]))

    def hear(self, *, patience_s: float = NOTHING_AFTER_S) -> None:
        """Stop watching: from here this is listening to the user, with fresh rules for
        when they have finished. Words of the current turn count as heard, and a turn
        the recognizer already ended is over (or held, if it leads on)."""
        self._watching = False
        self._verdict = None
        self._holding = False
        said = self._this_turn()
        self._rules = self._new_rules(patience_s)
        if not said:
            return
        self.began, self.began_heard = self.turn_began() or self._placed(self._now())
        self._rules.heard(final=self._turn_ended, has_text=True)
        if self._turn_ended:
            self._end_turn()

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()

    def _placed(self, seen: float) -> tuple[float, bool]:
        """When words first reported at `seen` were said: the last voice to start at
        least the recognizer's shortest lag before then (the last, not the first since
        some point: a click before them must not count; and a fast report must not skip
        the user's own start), and True; else the report less its usual lag, False."""
        cutoff = seen - MIN_LAG_S
        start = next((t for t in reversed(self._starts) if t <= cutoff), None)
        return (start, True) if start is not None else (seen - REPORT_LAG_S, False)

    def turn_began(self) -> tuple[float, bool] | None:
        """When the recognizer's current turn was said (see `_placed`), if it has words."""
        return self._placed(self._turn_seen) if self._turn_seen is not None else None

    def watch(self) -> None:
        """Back to watching, after deciding that what was heard was not a turn. What
        was heard is let go."""
        self.forget()
        self._watching = True
        self._verdict = None
        self._holding = False
        self._rules = self._new_rules(NOTHING_AFTER_S)

    def promote(
        self,
        *,
        on_partial: Callable[[str], Awaitable[None]],
        on_turn: Callable[[str, str], Awaitable[None]] | None,
    ) -> None:
        """Words heard over her turned out to be the user's turn (VOI-9): from here they
        are shown, and the recognizer's turn signals are passed on."""
        self._on_partial = on_partial
        self._on_turn = on_turn

    async def start(self) -> None:
        """Connect to the recognizer. Raises VoiceError; audio sent meanwhile is kept."""
        stream = await self._provider.open(choice=self._choice)
        self._stream = stream
        self._reader = asyncio.create_task(self._read(stream))
        if self._backlog:
            held, self._backlog = bytes(self._backlog), bytearray()
            await self._feed(held)

    async def audio(self, pcm: bytes) -> Verdict | None:
        """More from the microphone. Returns a verdict once, when listening should end."""
        if self._verdict is not None and not self._watching:
            return None
        self.frames += 1
        # Frames arrive as they are captured, so their arrival places a voice in time.
        if (ago := self._onsets.audio(pcm)) is not None:
            self._starts.append(self._now() - ago)
        if self._error is not None:
            raise self._error
        if self._stream is None:
            if len(self._backlog) + len(pcm) <= _MAX_BACKLOG:
                self._backlog.extend(pcm)
            return None
        return await self._feed(pcm)

    async def _feed(self, pcm: bytes) -> Verdict | None:
        assert self._stream is not None  # noqa: S101 - narrowed by the callers
        await self._stream.send(pcm)
        if self._watching:
            return None  # nothing ends while watching: the session decides
        self._verdict = self._rules.audio(pcm)
        return self._verdict

    def _end_turn(self) -> bool:
        """The recognizer's turn is over: held when its words lead on, else ended.
        True when held."""
        if leads_on(self.text()):
            # Done on "and" or "um": they are likely finding their words. Keep
            # listening; the local rules end it if quiet goes on.
            added, self._finals = [self._this_turn()], []
            self._held.extend(a for a in added if a)
            self._forgotten = ()
            self._holding = True
            self._rules.hold()
            return bool(added[0])
        self._rules.turn_over()
        return False

    async def _read(self, stream: STTStream) -> None:
        try:
            async for heard in stream.events():
                if self._rules.is_over and not self._watching:
                    # The next turn's first words can arrive while we wrap up; they
                    # are not part of this one.
                    continue
                if heard.text and heard.turn != "done" and self._turn_ended:
                    # The recognizer's next turn. Only words held from the last are kept.
                    self._turn_ended = False
                    self._finals, self._interim, self._forgotten = [], "", ()
                    self._turn_seen = None
                if heard.text and self._turn_seen is None:
                    self._turn_seen = self._now()
                if self._watching:
                    self._watched(heard)
                    if heard.text or heard.final:
                        await self._on_partial(self.turn_text())
                    continue
                # They carried on after a done that led on.
                resumed = bool(heard.text) and self._holding and heard.turn != "done"
                if resumed:
                    self._holding = False
                if heard.turn == "done":
                    # The whole turn, as the recognizer finally heard it. An empty
                    # done keeps what the interim words said.
                    kept = self._raw_turn()
                    self._finals = [heard.text] if heard.text else ([kept] if kept else [])
                    self._interim = ""
                    self._turn_ended = True
                    self._rules.heard(final=True, has_text=bool(heard.text))
                    if self._end_turn():
                        # As "probably finished": a draft may start (VOI-12).
                        await self._signal("maybe")
                elif heard.final:
                    if heard.text:
                        self._finals.append(heard.text)
                    self._interim = ""
                else:
                    self._interim = heard.text
                if heard.turn != "done":
                    # Ended quickly only when the recognizer heard a pause and the whole
                    # message so far reads as finished; otherwise the patient rules apply.
                    done = heard.pause and sounds_finished(self.text())
                    self._rules.heard(final=heard.final, has_text=bool(heard.text), done=done)
                if resumed:
                    await self._signal("resumed")
                if heard.turn in ("maybe", "resumed") and self._provider.turns:
                    await self._signal(heard.turn)
                if self.began is None and self.text() and not self._watching:
                    # When they began: a report comes well after the word.
                    self.began, self.began_heard = self.turn_began() or self._placed(self._now())
                if self._provider.streaming and (heard.text or heard.final):
                    await self._on_partial(self.text())
        except VoiceError as e:
            self._error = e
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The class name only: a recognizer's message can quote what it heard.
            logger.error("recognizer %s failed: %s", self._provider.name, type(e).__name__)
            self._error = VoiceError("internal")

    def _watched(self, heard: Heard) -> None:
        """A report while watching: the current turn's words, and nothing more."""
        if heard.turn == "done":
            kept = self._raw_turn()
            self._finals = [heard.text] if heard.text else ([kept] if kept else [])
            self._interim = ""
            self._turn_ended = True
        elif heard.final:
            if heard.text:
                self._finals.append(heard.text)
            self._interim = ""
        else:
            self._interim = heard.text

    async def _signal(self, kind: str) -> None:
        if self._on_turn is None:
            return
        try:
            await self._on_turn(kind, self.text())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A broken handler must not stop hearing. The class name only.
            logger.error("turn signal handler failed: %s", type(e).__name__)

    async def end(self) -> Utterance:
        """Stop the audio and collect everything that was said."""
        stream, reader = self._stream, self._reader
        if stream is None or reader is None:
            return Utterance(text="")

        async def wrap_up() -> None:
            await stream.finish()
            await reader

        try:
            limit = STREAMING_WRAP_UP_S if self._provider.streaming else BATCH_WRAP_UP_S
            # Words already heard are kept even if the last ones never arrive.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wrap_up(), timeout=limit)
        finally:
            await self.abandon()
        if self._error is not None and not self.text():
            raise self._error
        return Utterance(text=self.text())

    async def abandon(self) -> None:
        """Stop without using what was heard. Safe to call twice."""
        reader, stream = self._reader, self._stream
        self._reader = self._stream = None
        self._backlog.clear()
        if reader is not None and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.close()
