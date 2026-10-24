"""Her playback on the recognizer's clock (#102).

The page reports each sentence as it starts playing, on the same socket as the
microphone, so the audio the listener has sent the recognizer by then is where the
sentence starts on the recognizer's clock. Lengths and loudness come from her own
audio, measured as it is sent. Pure: times in, answers out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

WINDOW_S = 0.02  # her loudness is kept per 20 ms
LEAD_S = 0.15  # a word may be heard this much before a sentence's reported start
TAIL_S = 0.35  # and this much after its end: capture, network and the room
# An estimated end is trusted once this long past: a page playing behind can't hold
# her turn open for ever.
FINAL_CAP_S = 3.0


@dataclass
class Spoken:
    """One sentence of hers: as written, as said, and how loud each 20 ms of it was."""

    seq: int
    written: str
    spoken: str
    levels: list[float]
    start_s: float | None = None

    @property
    def length_s(self) -> float:
        return len(self.levels) * WINDOW_S


@dataclass
class Timeline:
    sentences: dict[int, Spoken] = field(default_factory=dict)
    watch_s: float | None = None
    finished: bool = False
    stopped: bool = False

    def sentence(self, seq: int, *, written: str, spoken: str, levels: list[float]) -> None:
        """Her audio for one sentence, as it was sent to the page. More for a sentence
        already known makes it longer."""
        known = self.sentences.get(seq)
        if known is None:
            self.sentences[seq] = Spoken(seq, written, spoken, list(levels))
        else:
            known.levels.extend(levels)

    def begin(self, *, at_s: float) -> None:
        """The watch opened here: her first sentence can't start earlier."""
        self.watch_s = at_s

    def start(self, seq: int, *, at_s: float) -> None:
        """The page reported sentence `seq` playing, with this much audio sent."""
        s = self.sentences.get(seq)
        if s is not None and s.start_s is None:
            s.start_s = at_s

    def finish(self) -> None:
        """Every sentence has been sent; once the last is played she has ended."""
        self.finished = True

    def stop(self) -> None:
        """She was stopped: sentences not yet reported played were never heard."""
        self.stopped = True

    def _spans(self) -> list[tuple[Spoken, float]]:
        """Each placed sentence with its start, in order. One not reported yet is
        assumed to follow the one before; once she is stopped, or before anything is
        known, it has no span, and later reported sentences still place."""
        out: list[tuple[Spoken, float]] = []
        prev_end = self.watch_s
        for seq in sorted(self.sentences):
            s = self.sentences[seq]
            start = s.start_s
            if start is None:
                if self.stopped or prev_end is None:
                    continue
                start = prev_end
            out.append((s, start))
            prev_end = start + s.length_s
        return out

    def at(self, t: float) -> Spoken | None:
        """The sentence playing at `t` on the recognizer's clock, margins included.
        Where margins overlap, the sentence nearest `t` wins."""
        best: Spoken | None = None
        best_gap = 0.0
        for s, start in self._spans():
            end = start + s.length_s
            if not start - LEAD_S <= t <= end + TAIL_S:
                continue
            gap = max(start - t, t - end, 0.0)
            if best is None or gap < best_gap:
                best, best_gap = s, gap
        return best

    def her_level(self, t0: float, t1: float) -> float:
        """Her mean loudness over a span (0 where she is silent)."""
        if t1 <= t0:
            return 0.0
        total, n = 0.0, 0
        for s, start in self._spans():
            # The windows that overlap [t0, t1): window i covers start + i * WINDOW_S on.
            lo = max(0, math.floor((t0 - start) / WINDOW_S))
            hi = min(len(s.levels), math.ceil((t1 - start) / WINDOW_S))
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
        if not self.stopped and (not self.finished or len(spans) < len(self.sentences)):
            return None
        s, start = spans[-1]
        return start + s.length_s + TAIL_S

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
