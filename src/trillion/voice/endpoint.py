"""Deciding that the user has stopped talking (VOI-3, VOI-4, VOI-15a).

Pure and clocked by the audio itself: time is counted in samples received, so
the rules behave the same in a test as on a slow machine. Two signals are
weighed, local loudness and what the recognizer reports, because each is wrong
in a different way: loudness cannot tell a pause from the end, and a recognizer
can lag behind a voice that is still going.
"""

from __future__ import annotations

from array import array
from typing import Literal

Verdict = Literal["end", "inaudible", "nothing", "too_long"]

SAMPLE_RATE = 16_000
_WINDOW = SAMPLE_RATE // 50  # 20 ms
# After the recognizer calls a segment final, this much quiet ends the turn.
FAST_S = 0.4
# With no final to lean on, this much quiet does.
SLOW_S = 1.0
# A recognizer that only transcribes once the turn is over (local Whisper) gives
# nothing to lean on at all, so quiet is the only sign. A second cut people off
# mid-thought; two lets them pause to think, at the cost of a later reply.
BATCH_S = 2.0
# Quiet is not believed while the recognizer was hearing words this recently.
RECENT_WORDS_S = 0.5
# When the words sound finished and the recognizer heard the pause too, this much
# quiet in the voice ends the turn: the recognizer's own pause (300 ms) is already
# behind it by the time its final words arrive, so in practice the turn ends then.
DONE_S = 0.25
# Words a thought does not end on, whatever punctuation the recognizer gave them.
_GOING_ON = frozenset(
    "and but or so because if then than that which who to of for with the a an my "
    "your um uh er erm like".split()
)
# A microphone that delivers nothing louder than this is not delivering sound at
# all: muted, unplugged, or the wrong device. A real threshold, not "above zero".
DEAD_LEVEL = 0.0006
DEAD_AFTER_S = 2.5
# Nobody spoke. Longer for a click (they are gathering a thought) than for the
# window that reopens after she speaks.
NOTHING_AFTER_S = 8.0
TOO_LONG_S = 120.0
# Speech must clear the room's own noise by this factor, and never count below the floor.
_OVER_NOISE = 3.0
_MIN_SPEECH = 0.008
# A cough is not a sentence: speech starts after this much of it.
_ONSET_S = 0.06


def sounds_finished(text: str) -> bool:
    """Whether words read as a finished thought: they end a sentence, and not on a
    word that leads somewhere ("and.", "um."). A trailing ellipsis is not an end."""
    t = text.strip().rstrip("\"')\u201d\u2019")  # closing quotes and brackets
    if not t or t.endswith(("...", "\u2026")) or t[-1] not in ".?!":
        return False
    last = t.rstrip(".?!").rsplit(maxsplit=1)[-1:] or [""]
    return last[0].lower().strip("\"'(\u201c\u2018,;:") not in _GOING_ON


def level(pcm: bytes) -> float:
    """Loudness of one stretch of PCM16, 0 to 1 (root mean square)."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    return float((sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768)


class EndOfSpeech:
    def __init__(self, *, streaming: bool, patience_s: float = NOTHING_AFTER_S) -> None:
        self._streaming = streaming
        self._patience = patience_s
        self._rest = b""
        self._samples = 0
        self._noise = 0.002
        self._peak = 0.0
        self._run = 0.0
        self._spoke = False
        self._last_voice = 0.0
        self._last_words = -1.0
        self._last_final = -1.0
        self._pending_words = False
        self._done = False

    @property
    def now(self) -> float:
        return self._samples / SAMPLE_RATE

    @property
    def spoke(self) -> bool:
        return self._spoke

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def quiet_s(self) -> float:
        """How long since the voice was last heard, by the audio's own clock. 0 before
        anyone spoke. Taken from the verdict's time, this is when they stopped talking."""
        if not self._spoke:
            return 0.0
        # The voice itself, when it was loud enough to hear. A recognizer reports words
        # after they were said (half a second late is common), so its timing would
        # start the turn too late. Only for a speaker too quiet to clear the bar is
        # it the best there is.
        last = self._last_voice if self._last_voice > 0 else self._last_words
        return max(0.0, self.now - last)

    def heard(self, *, final: bool, has_text: bool, done: bool = False) -> None:
        """The recognizer reported something, at this point in the audio. `done`: it
        heard the speaker pause, and everything said so far sounds finished."""
        if has_text:
            # New words undo an earlier "done": the speaker went on. An empty final, the
            # recognizer's report of silence, changes nothing.
            self._done = done and final
        if has_text:
            self._spoke = True
            self._last_words = self.now
            self._pending_words = not final
        if final and has_text:
            self._last_final = self.now

    def audio(self, pcm: bytes) -> Verdict | None:
        data = self._rest + pcm
        step = _WINDOW * 2
        cut = len(data) - (len(data) % step)
        self._rest = data[cut:]
        for i in range(0, cut, step):
            verdict = self._window(level(data[i : i + step]))
            if verdict is not None:
                return verdict
        return None

    def _window(self, loud: float) -> Verdict | None:
        self._samples += _WINDOW
        now = self.now
        self._peak = max(self._peak, loud)
        if loud > max(_MIN_SPEECH, self._noise * _OVER_NOISE):
            self._run += _WINDOW / SAMPLE_RATE
            if self._run >= _ONSET_S:
                self._spoke = True
                self._last_voice = now
        else:
            self._run = 0.0
            # The room's own level, learned only from the quiet parts.
            self._noise += (loud - self._noise) * 0.05

        if now >= TOO_LONG_S:
            return "too_long"
        if not self._spoke:
            if now >= DEAD_AFTER_S and self._peak < DEAD_LEVEL:
                return "inaudible"
            return "nothing" if now >= self._patience else None

        quiet = now - self._last_voice
        if self._streaming and self._done and not self._pending_words and quiet >= DONE_S:
            return "end"
        if self._streaming:
            # Words are still arriving: the voice the meter lost is not finished.
            if now - self._last_words < RECENT_WORDS_S and self._pending_words:
                return None
            if self._last_final >= 0 and not self._pending_words:
                return "end" if min(quiet, now - self._last_final) >= FAST_S else None
        return "end" if quiet >= (SLOW_S if self._streaming else BATCH_S) else None
