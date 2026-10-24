"""Her playback on the recognizer's clock (#102).

The page reports each sentence as it starts playing, on the same socket as the
microphone, so the audio the listener has sent the recognizer by then is where the
sentence starts on the recognizer's clock. Lengths and loudness come from her own
audio, measured as it is sent. Pure: times in, answers out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WINDOW_S = 0.02  # her loudness is kept per 20 ms
LEAD_S = 0.15  # a word may be heard this much before a sentence's reported start
TAIL_S = 0.35  # and this much after its end: capture, network and the room


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
        assumed to follow the one before; none can be placed before anything is known."""
        out: list[tuple[Spoken, float]] = []
        prev_end = self.watch_s
        for seq in sorted(self.sentences):
            s = self.sentences[seq]
            start = s.start_s
            if start is None:
                if self.stopped or prev_end is None:
                    break
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
        total, n = 0.0, 0
        for s, start in self._spans():
            for i, level in enumerate(s.levels):
                w0 = start + i * WINDOW_S
                if w0 + WINDOW_S > t0 and w0 < t1:
                    total += level
                    n += 1
        return total / n if n else 0.0

    def ended_at(self) -> float | None:
        """When her echo is over, once every sentence was sent and placed."""
        if not self.finished:
            return None
        spans = self._spans()
        if not spans or len(spans) < len(self.sentences):
            return None
        s, start = spans[-1]
        return start + s.length_s + TAIL_S
