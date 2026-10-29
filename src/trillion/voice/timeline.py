"""Her playback on the recognizer's clock (#102).

The page reports each sentence as it starts playing, on the same socket as the
microphone, so the audio the listener has sent the recognizer by then is where the
sentence starts on the recognizer's clock. Lengths and loudness come from her own
audio, measured as it is sent. Pure: times in, answers out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median

from trillion.voice.overlap import words_of
from trillion.voice.types import Word

WINDOW_S = 0.02  # her loudness is kept per 20 ms
LEAD_S = 0.15  # a word may be heard this much before a sentence's reported start
TAIL_S = 0.35  # and this much after its end: capture, network and the room
# An estimated end is trusted once this long past: a page playing behind can't hold
# her turn open for ever.
FINAL_CAP_S = 3.0
# Her words heard within this of where she was placed saying them measure how far the
# reports are off the recognizer's clock. Measured only, for the logs: not applied.
SEARCH_S = 1.5
_OFFSETS_KEPT = 50
# A word heard in two places of hers this far apart says nothing about the offset.
_AMBIGUOUS_S = 0.5


@dataclass
class Spoken:
    """One sentence of hers: as written, as said, and how loud each 20 ms of it was."""

    seq: int
    written: str
    spoken: str
    levels: list[float]
    # Where it was reported starting to play, and where its audio was first sent to the
    # page (it can't play before that), on the recognizer's clock.
    start_s: float | None = None
    sent_s: float | None = None
    # Played before a watch began, and over before then.
    over: bool = False

    @property
    def length_s(self) -> float:
        return len(self.levels) * WINDOW_S


@dataclass
class Timeline:
    sentences: dict[int, Spoken] = field(default_factory=dict)
    watch_s: float | None = None
    finished: bool = False
    stopped: bool = False
    # Where she was stopped on the clock, when known: what was playing ends there.
    stopped_s: float | None = None
    # How far her words were heard from where she was placed saying them.
    offsets: list[float] = field(default_factory=list)

    def sentence(
        self,
        seq: int,
        *,
        written: str,
        spoken: str,
        levels: list[float],
        at_s: float | None = None,
    ) -> None:
        """Her audio for one sentence, as it was sent to the page, `at_s` on the clock
        when first known. More for a sentence already known makes it longer."""
        known = self.sentences.get(seq)
        if known is None:
            self.sentences[seq] = Spoken(seq, written, spoken, list(levels), sent_s=at_s)
        else:
            known.levels.extend(levels)
            if known.sent_s is None:
                known.sent_s = at_s

    def begin(self, *, at_s: float, playing: tuple[int, float] | None = None) -> None:
        """A watch opened here, on a new listener's clock: her next sentence can't start
        earlier, and times on an earlier clock no longer place her. `playing` is the
        sentence last reported playing and how long ago: it is placed that far back,
        and the ones before it are over."""
        self.watch_s = at_s
        self.offsets.clear()
        for s in self.sentences.values():
            s.start_s = s.sent_s = None
        if playing is not None and playing[0] in self.sentences:
            seq, ago_s = playing
            for s in self.sentences.values():
                s.over = s.seq < seq
            self.sentences[seq].start_s = at_s - ago_s

    def start(self, seq: int, *, at_s: float) -> None:
        """The page reported sentence `seq` playing, with this much audio sent."""
        s = self.sentences.get(seq)
        if s is not None and s.start_s is None:
            s.start_s = at_s

    def finish(self) -> None:
        """Every sentence has been sent; once the last is played she has ended."""
        self.finished = True

    def stop(self, *, at_s: float | None = None) -> None:
        """She was stopped, at `at_s` on the clock when known: what was playing ends
        there, and sentences not yet under way were never heard."""
        if not self.stopped:
            self.stopped, self.stopped_s = True, at_s

    def _spans(self) -> list[tuple[Spoken, float, float]]:
        """Each placed sentence with its start and end, in order. One not reported yet
        is assumed to follow the one before, and not before its audio was sent; once
        she is stopped, only if it was under way by then. Before anything is known it
        has no span, and later reported sentences still place."""
        out: list[tuple[Spoken, float, float]] = []
        prev_end = self.watch_s
        cut = self.stopped_s if self.stopped else None
        for seq in sorted(self.sentences):
            s = self.sentences[seq]
            if s.over:
                continue
            start = s.start_s
            if start is None:
                known = [t for t in (prev_end, self.watch_s, s.sent_s) if t is not None]
                if not known:
                    continue
                start = max(known)
                if self.stopped and cut is None:
                    continue
            end = start + s.length_s
            if cut is not None:
                if start >= cut:
                    continue
                end = min(end, cut)
            out.append((s, start, end))
            prev_end = end
        return out

    def spans(self) -> list[tuple[float, float]]:
        """When each placed sentence of hers played, without margins."""
        return [(start, end) for _, start, end in self._spans()]

    def at(self, t: float) -> Spoken | None:
        """The sentence playing at `t` on the recognizer's clock, margins included.
        Where margins overlap, the sentence nearest `t` wins."""
        found = self.placed(t)
        return found[0] if found is not None else None

    def placed(self, t: float) -> tuple[Spoken, float, float] | None:
        """As `at`, with the sentence's start and end on the clock."""
        best: tuple[Spoken, float, float] | None = None
        best_gap = 0.0
        for s, start, end in self._spans():
            if not start - LEAD_S <= t <= end + TAIL_S:
                continue
            gap = max(start - t, t - end, 0.0)
            if best is None or gap < best_gap:
                best, best_gap = (s, start, end), gap
        return best

    def her_level(self, t0: float, t1: float) -> float:
        """Her mean loudness over a span (0 where she is silent)."""
        if t1 <= t0:
            return 0.0
        total, n = 0.0, 0
        for s, start, end in self._spans():
            # The windows that overlap [t0, t1): window i covers start + i * WINDOW_S on.
            # Stopped, what the page had already scheduled still plays for a moment.
            if end < start + s.length_s:
                end = min(start + s.length_s, end + TAIL_S)
            played = math.ceil((end - start) / WINDOW_S - 1e-9)
            lo = max(0, math.floor((t0 - start) / WINDOW_S))
            hi = min(len(s.levels), played, math.ceil((t1 - start) / WINDOW_S))
            if hi > lo:
                total += sum(s.levels[lo:hi])
                n += hi - lo
        return total / n if n else 0.0

    def ended_at(self) -> float | None:
        """When her echo is over: once every sentence was sent and placed, or once she
        was stopped, from what was played. An estimate while the last sentence's report
        is still to come."""
        spans = self._spans()
        if not spans:
            return None
        live = sum(not s.over for s in self.sentences.values())
        if not self.stopped and (not self.finished or len(spans) < live):
            return None
        return spans[-1][2] + TAIL_S

    def ended_final(self, *, now_s: float) -> float | None:
        """`ended_at` once it can be relied on: her last sentence was reported played,
        she was stopped, or the estimate is `FINAL_CAP_S` old by `now_s` on the
        recognizer's clock. It can turn final later, so callers ask again rather than
        keep the first answer."""
        end = self.ended_at()
        if end is None:
            return None
        last = self.sentences[max(self.sentences)]
        if self.stopped or last.start_s is not None or now_s >= end + FINAL_CAP_S:
            return end
        return None

    def ending_at(self, at_s: float) -> Timeline:
        """A timeline of her last sentence alone, ending at `at_s`: for a listener that
        opens as she ends, so her echo still in the air is judged hers."""
        out = Timeline(finished=True, stopped=True, stopped_s=at_s)
        if self.sentences:
            last = self.sentences[max(self.sentences)]
            out.sentences[last.seq] = Spoken(
                last.seq,
                last.written,
                last.spoken,
                list(last.levels),
                start_s=at_s - last.length_s,
            )
        return out

    def align(self, word: Word) -> None:
        """A word heard: if it is one of hers, said once within `SEARCH_S` of where she
        was placed saying it, note how far off that is. Its place in her sentence is
        taken as even steps through it."""
        heard = words_of(word.text)
        if len(heard) != 1 or len(heard[0]) < 3:
            return
        found: list[float] = []
        for s, start, _ in self._spans():
            for form in {s.written, s.spoken}:
                hers = words_of(form)
                for i, w in enumerate(hers):
                    if w == heard[0]:
                        at = start + s.length_s * (i + 0.5) / len(hers)
                        if abs(word.mid_s - at) <= SEARCH_S:
                            found.append(word.mid_s - at)
        if not found or max(found) - min(found) > _AMBIGUOUS_S:
            return
        self.offsets.append(sum(found) / len(found))
        del self.offsets[:-_OFFSETS_KEPT]

    def offset_s(self) -> float | None:
        """How far after where she was placed her words were heard, as a median; None
        until any were."""
        return median(self.offsets) if self.offsets else None
