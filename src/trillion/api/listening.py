"""One stretch of listening: from the microphone opening to the words she is given.

The recognizer turns audio into segments; this decides when the user has
finished and assembles what they said (VOI-15a). Nothing heard is ever logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from trillion.voice.endpoint import (
    NOTHING_AFTER_S,
    SAMPLE_RATE,
    EndOfSpeech,
    Verdict,
    leads_on,
    level,
    sounds_finished,
)
from trillion.voice.overlap import words_of
from trillion.voice.types import Heard, STTProvider, STTStream, VoiceError, Word

logger = logging.getLogger(__name__)

# How long the recognizer gets to hand over its last words once the audio stops.
STREAMING_WRAP_UP_S = 3.0
BATCH_WRAP_UP_S = 60.0
# Audio that arrives while the recognizer is still connecting is kept, so the first
# syllable is never clipped (VOI-2). Bounded: ten seconds of 16 kHz PCM16.
_MAX_BACKLOG = 16_000 * 2 * 10
# The window that reopens after she speaks waits this long for a first word (VOI-8).
FOLLOW_UP_S = 3.0
# After she stays quiet because they trailed off mid-thought, the microphone stays open
# this long for them to go on before she nudges them once (VOI-7).
QUIET_LISTEN_S = 8.0
# A word reported without a time is taken as said this long before its report, the
# usual lag of a recognizer, and lasting this long either side of then (#102).
UNTIMED_LAG_S = 0.4
_UNTIMED_HALF_S = 0.1
# The microphone's loudness is kept per 20 ms of audio sent, for this long (#102).
LEVEL_WINDOW_S = 0.02
_SAMPLE_BYTES = 2  # PCM16
_WINDOW_BYTES = _SAMPLE_BYTES * round(SAMPLE_RATE * LEVEL_WINDOW_S)
_BYTES_PER_S = _SAMPLE_BYTES * SAMPLE_RATE
LEVELS_KEPT_S = 30.0
# The room's level: the quiet of the last this-many seconds, away from any word by
# at least NEAR_WORD_S, and what it is taken to be before anything is known.
ROOM_LOOKBACK_S = 10.0
NEAR_WORD_S = 0.25
ROOM_DEFAULT = 0.002


# Punctuation that never starts what the user said, once her words before it are gone.
_LEADING = "-\u2013\u2014\u2026.,;: "
# Which of these words to keep as the user's (#102): one answer per word.
Judge = Callable[[Sequence[Word]], Sequence[bool]]


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
        words_only: bool = False,
        on_report: Callable[[Heard], Awaitable[None]] | None = None,
    ) -> None:
        self._provider = provider
        self._choice = choice
        self._on_partial = on_partial
        self._on_turn = on_turn
        # Told of every report while the user's turn is taken, words or not: when a
        # listening cue may play is weighed on them (PER-11).
        self._on_report = on_report
        self._rules = self._new_rules(patience_s, words_only=words_only)
        # Watching while she speaks (VOI-9): every frame goes to the recognizer, its
        # turns come and go, and nothing ends until `hear` makes this a turn.
        self._watching = watch
        # The recognizer said its current turn is over; its next words start another.
        self._turn_ended = False
        # The talk key is held down: a pause never ends the turn, only letting go does.
        self.key_held = False
        # A turn the recognizer ends on words the judge says are hers (a listening cue's
        # echo, PER-11) is held as if it led on: the user had only paused.
        self.hold_on_echo = False
        # Once heard over her, which words are the user's: her echo is left out (#102).
        self._judge: Judge | None = None
        self._stream: STTStream | None = None
        self._reader: asyncio.Task[None] | None = None
        self._backlog = bytearray()
        # The recognizer's clock (#102): bytes sent since the stream opened, a part
        # window not yet measured, and the loudness of each 20 ms: (end, level).
        self._sent = 0
        self._carry = b""
        self._levels: deque[tuple[float, float]] = deque(
            maxlen=round(LEVELS_KEPT_S / LEVEL_WINDOW_S)
        )
        # The current recognizer turn's timed words, as the turn's text is kept: final
        # stretches, then the latest interim. Flux repeats the whole turn each report.
        self._final_words: list[Word] = []
        self._interim_words: tuple[Word, ...] = ()
        # The spans of earlier turns' words, for knowing when the room was quiet.
        self._past_spans: deque[tuple[float, float]] = deque(maxlen=512)
        # Turns the recognizer called done on words that lead on ("... and"): kept,
        # with their words, and what follows joins them (VOI-12).
        self._held: list[tuple[str, tuple[Word, ...]]] = []
        self._holding = False
        self._finals: list[str] = []
        self._interim = ""
        self._error: VoiceError | None = None
        self._verdict: Verdict | None = None
        self.frames = 0

    def _new_rules(self, patience_s: float, *, words_only: bool = False) -> EndOfSpeech:
        p = self._provider
        return EndOfSpeech(
            streaming=p.streaming, patience_s=patience_s, judged=p.turns, words_only=words_only
        )

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

    @property
    def sent_s(self) -> float:
        """Seconds of audio sent to the recognizer since its stream opened: its clock,
        on which word times are given (the backlog sent on connecting included)."""
        return self._sent / _BYTES_PER_S

    def mic_level(self, t0: float, t1: float) -> float:
        """The microphone's mean loudness (0 to 1) over the 20 ms windows of audio sent
        that overlap `t0`..`t1` on the recognizer's clock; 0 when none are kept."""
        t1 = max(t1, t0 + 1e-9)
        got = [v for end, v in self._levels if end > t0 and end - LEVEL_WINDOW_S < t1]
        return sum(got) / len(got) if got else 0.0

    def word_level(self, t0: float, t1: float) -> float:
        """The microphone's loudness through the first half of a word. A word's start
        is where its voice begins; its end often runs on, into the next voice: a word
        of hers just before the user starts would take their loudness."""
        return self.mic_level(t0, t0 + max(LEVEL_WINDOW_S, (t1 - t0) / 2))

    def turn_words(self) -> tuple[Word, ...]:
        """The timed words of the recognizer's current turn, latest report. A turn it
        has ended keeps its words until its next turn's words arrive. Words reported
        without times are placed at their report less the usual lag."""
        return (*self._final_words, *self._interim_words)

    def room_level(self, *, busy: Iterable[tuple[float, float]] = ()) -> float:
        """The room's own loudness: the median of the last ten seconds' windows with no
        word heard near them, nor in any `busy` span. Until there are any, a quiet
        room's level. The listener does not know her playback: callers must pass her
        sentences' spans as `busy`, or her voice counts as the room."""
        spans = [
            *self._past_spans,
            *((w.start_s, w.end_s) for w in self.turn_words()),
        ]
        since = self.sent_s - ROOM_LOOKBACK_S
        # Only spans reaching into the last ten seconds matter.
        near = [(a - NEAR_WORD_S, b + NEAR_WORD_S) for a, b in spans if b + NEAR_WORD_S > since]
        near.extend((a, b) for a, b in busy if b > since)
        quiet = [
            v
            for end, v in self._levels
            if end > since and not any(end > a and end - LEVEL_WINDOW_S < b for a, b in near)
        ]
        return statistics.median(quiet) if quiet else ROOM_DEFAULT

    def text(self) -> str:
        """What the user said: words held, then the current turn's, less her echo once
        a judge is set."""
        held = [self._kept(text, words) for text, words in self._held]
        return " ".join(p for p in (*held, self._this_turn()) if p).strip()

    def raw_text(self) -> str:
        """Everything the recognizer heard, her echo included."""
        held = [text for text, _ in self._held]
        return " ".join(p for p in (*held, self._raw_turn()) if p).strip()

    def heard_words(self) -> tuple[Word, ...]:
        """The words held and the current turn's, each with when it was said."""
        held = [w for _, words in self._held for w in words]
        return (*held, *self.turn_words())

    def _raw_turn(self) -> str:
        return " ".join(p for p in (*self._finals, self._interim) if p).strip()

    def _kept(self, text: str, words: Sequence[Word]) -> str:
        """The words the judge keeps, spelled as the text has them when it can be lined
        up with them, so punctuation stays ("Tuesday?")."""
        if self._judge is None or not text:
            return text
        keep = list(self._judge(words))
        if all(keep):
            return text
        tokens = text.split()
        wordy = [i for i, t in enumerate(tokens) if words_of(t)]
        if len(wordy) != len(words):
            return " ".join(w.text for w, k in zip(words, keep, strict=True) if k)
        kept: list[str] = []
        at = -1  # the word last passed, by its place in `words`
        for token in tokens:
            if words_of(token):
                at += 1
                if keep[at]:
                    kept.append(token)
            # Punctuation alone ("--", "…") stays only between two words kept.
            elif 0 <= at < len(keep) - 1 and keep[at] and keep[at + 1]:
                kept.append(token)
        said = " ".join(kept)
        # And never leads what they said.
        return said.lstrip(_LEADING)

    def _this_turn(self) -> str:
        return self._kept(self._raw_turn(), self.turn_words())

    @property
    def turn_done(self) -> bool:
        """The recognizer has ended its current turn (even one held as leading on)."""
        return self._turn_ended

    def turn_text(self) -> str:
        """The words of the recognizer's current turn. A turn it has ended stays current
        until its next words arrive."""
        return self._this_turn()

    def hear(
        self,
        *,
        patience_s: float = NOTHING_AFTER_S,
        judge: Judge | None = None,
        words_only: bool = False,
    ) -> None:
        """Stop watching: from here this is listening to the user, with fresh rules for
        when they have finished. Words of the current turn count as heard, those `judge`
        keeps when one is given, and a turn the recognizer already ended is over (or
        held, if it leads on). With `words_only`, only words count as speaking."""
        self._watching = False
        self._verdict = None
        self._holding = False
        self._judge = judge
        said = self._this_turn()
        self._rules = self._new_rules(patience_s, words_only=words_only)
        if not said:
            return
        self._rules.heard(final=self._turn_ended, has_text=True)
        if self._turn_ended:
            self._end_turn()

    def judge_by(self, judge: Judge) -> None:
        """Keep only the words `judge` keeps as the user's (#102)."""
        self._judge = judge

    @property
    def judge(self) -> Judge | None:
        return self._judge

    def watch(self) -> None:
        """Back to watching, after deciding that what was heard was not a turn. Words
        held are let go; which words were heard is the session's to remember."""
        self._held = []
        self._judge = None
        self._watching = True
        self._verdict = None
        self._holding = False
        self._rules = self._new_rules(NOTHING_AFTER_S)

    def promote(
        self,
        *,
        on_partial: Callable[[str], Awaitable[None]],
        on_turn: Callable[[str, str], Awaitable[None]] | None,
        on_report: Callable[[Heard], Awaitable[None]] | None = None,
    ) -> None:
        """Words heard over her turned out to be the user's turn (VOI-9): from here they
        are shown, and the recognizer's turn signals are passed on."""
        self._on_partial = on_partial
        self._on_turn = on_turn
        self._on_report = on_report

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
        if self._error is not None:
            raise self._error
        if self._stream is None:
            if len(self._backlog) + len(pcm) <= _MAX_BACKLOG:
                self._backlog.extend(pcm)
            return None
        return await self._feed(pcm)

    async def _feed(self, pcm: bytes) -> Verdict | None:
        assert self._stream is not None  # noqa: S101 - narrowed by the callers
        # Measured before the send waits, so audio is placed in the order it is sent,
        # even while the backlog's send is still going.
        self._measure(pcm)
        await self._stream.send(pcm)
        if self._watching:
            return None  # nothing ends while watching: the session decides
        verdict = self._rules.audio(pcm)
        if self.key_held and verdict in ("end", "nothing"):
            # Quiet, or the recognizer's own sense of an end, is not the end while the
            # key is down. A dead microphone and the length cap still are.
            verdict = None
        self._verdict = verdict
        return self._verdict

    def _measure(self, pcm: bytes) -> None:
        """Count audio sent and keep its loudness per 20 ms, carrying a part window."""
        data = self._carry + pcm
        whole = len(data) - len(data) % _WINDOW_BYTES
        start = self._sent - len(self._carry)
        for i in range(0, whole, _WINDOW_BYTES):
            end = (start + i + _WINDOW_BYTES) / _BYTES_PER_S
            self._levels.append((end, level(data[i : i + _WINDOW_BYTES])))
        self._carry = data[whole:]
        self._sent += len(pcm)

    def _took_words(self, heard: Heard) -> None:
        """A report's timed words, kept as its text is. Words reported without times
        are placed at the report less the usual lag."""
        if heard.turn == "done":
            if heard.text:
                words = heard.words or self._untimed(heard.text, self.turn_words())
                self._final_words = list(words)
            else:
                self._final_words.extend(self._interim_words)
            self._interim_words = ()
        elif heard.final:
            if heard.text:
                words = heard.words or self._untimed(heard.text, self._interim_words)
                self._final_words.extend(words)
            self._interim_words = ()
        else:
            self._interim_words = heard.words or self._untimed(heard.text, self._interim_words)

    def _untimed(self, text: str, before: Sequence[Word]) -> tuple[Word, ...]:
        """Words with no times, placed at this report less the usual lag. A word the
        last report had in the same place keeps its time: reports repeat the turn."""
        at = max(_UNTIMED_HALF_S, self.sent_s - UNTIMED_LAG_S)
        lo, hi = at - _UNTIMED_HALF_S, at + _UNTIMED_HALF_S
        out: list[Word] = []
        # Punctuation alone ("--") is not a word, and has no time.
        for i, token in enumerate(t for t in text.split() if words_of(t)):
            prior = before[i] if i < len(before) else None
            if prior is not None and words_of(prior.text) == words_of(token):
                out.append(prior)
            else:
                out.append(Word(text=token, start_s=lo, end_s=hi))
        return tuple(out)

    def _end_turn(self) -> bool:
        """The recognizer's turn is over: held when its words lead on, else ended.
        True when held. A turn with no words of the user's in it is not theirs to end."""
        said = self._this_turn()
        if not said and self._raw_turn():
            # Only her echo: over, quietly, if anything was heard; the rules keep
            # waiting for the user if nothing was.
            self._rules.turn_over()
            return False
        if leads_on(self.text()) or self._ends_in_echo():
            # Done on "and" or "um": they are likely finding their words. Keep
            # listening; the local rules end it if quiet goes on.
            raw, words = self._raw_turn(), self.turn_words()
            if raw:
                self._held.append((raw, words))
            self._past_spans.extend((w.start_s, w.end_s) for w in words)
            self._finals, self._final_words, self._interim_words = [], [], ()
            self._holding = True
            self._rules.hold()
            return bool(said)
        self._rules.turn_over()
        return False

    def _ends_in_echo(self) -> bool:
        """The current turn's last word is hers, by the judge, and the session asked
        for that to hold the turn."""
        words = self.turn_words()
        if not self.hold_on_echo or self._judge is None or not words:
            return False
        return not list(self._judge(words))[-1]

    def _theirs_in_turn(self, heard: Heard) -> bool:
        """The latest report has words of the user's in it: with a judge, only her
        echo is not them going on."""
        if not heard.text:
            return False
        words = self.turn_words()
        if self._judge is None or not words:
            return True
        return any(self._judge(words))

    async def _read(self, stream: STTStream) -> None:
        try:
            async for heard in stream.events():
                if not heard.text and not heard.final and heard.turn is None:
                    # Only how sure the recognizer is that the turn is over: no words.
                    if not self._watching and not self._rules.is_over:
                        await self._report(heard)
                    continue
                if self._rules.is_over and not self._watching:
                    # The next turn's first words can arrive while we wrap up; they
                    # are not part of this one.
                    continue
                if heard.text and heard.turn != "done" and self._turn_ended:
                    # The recognizer's next turn. Only words held from the last are kept.
                    self._turn_ended = False
                    self._finals, self._interim = [], ""
                    self._past_spans.extend((w.start_s, w.end_s) for w in self.turn_words())
                    self._final_words, self._interim_words = [], ()
                self._took_words(heard)
                if self._watching:
                    self._watched(heard)
                    if heard.text or heard.final:
                        await self._on_partial(self.turn_text())
                    continue
                # They carried on after a done that led on.
                resumed = self._holding and heard.turn != "done" and self._theirs_in_turn(heard)
                if resumed:
                    self._holding = False
                if heard.turn == "done":
                    # The whole turn, as the recognizer finally heard it. An empty
                    # done keeps what the interim words said.
                    kept = self._raw_turn()
                    self._finals = [heard.text] if heard.text else ([kept] if kept else [])
                    self._interim = ""
                    self._turn_ended = True
                    self._rules.heard(final=True, has_text=self._has_text(heard))
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
                    has_text = self._has_text(heard)
                    self._rules.heard(final=heard.final, has_text=has_text, done=done)
                if resumed:
                    await self._signal("resumed")
                if heard.turn in ("maybe", "resumed") and self._provider.turns:
                    await self._signal(heard.turn)
                if self._provider.streaming and (heard.text or heard.final):
                    await self._on_partial(self.text())
                await self._report(heard)
        except VoiceError as e:
            self._error = e
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The class name only: a recognizer's message can quote what it heard.
            logger.error("recognizer %s failed: %s", self._provider.name, type(e).__name__)
            self._error = VoiceError("internal")

    def _has_text(self, heard: Heard) -> bool:
        """Whether a report has words of the user's in it: with a judge, her echo alone
        is not the user speaking."""
        return bool(heard.text) and (self._judge is None or bool(self._this_turn()))

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

    async def _report(self, heard: Heard) -> None:
        if self._on_report is None:
            return
        try:
            await self._on_report(heard)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A broken handler must not stop hearing. The class name only.
            logger.error("report handler failed: %s", type(e).__name__)

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
