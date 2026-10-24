# Echo Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `docs/specs/2026-10-24-echo-timing.md` (HelloTrillion/trillion#102). Each word heard while she speaks is judged hers or the user's by when it was said, what she was saying then, and how loud the microphone was. This replaces the text-matching echo rules.

**Architecture:**
- **Word times:** a vendor-neutral `Word` (text, start, end) is carried on `Heard`.
- **Her playback:** `voice/timeline.py` (pure) places her sentences on the recognizer's clock, by stamping each `playback.sentence` report with how much audio the listener has sent. It also holds her loudness per 20 ms, from `Speaker`.
- **The judgement:** `voice/whose.py` (pure) judges each word.
- **The session's watch** splits the recognizer's turn into hers and theirs, and feeds only theirs into the existing `confirm` and `decide` steps.

**Tech stack:** Python 3.13/uv, pytest-asyncio, ruff, strict mypy. Web: TypeScript/vitest (expect no web changes).

**Repo rules (every task):**
- **Commits:** signed off (`git commit -s`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Vendors:** only `src/trillion/voice/providers/deepgram.py` knows Flux (VOI-11).
- **Privacy:** never log heard words, reply text or levels.
- **Comments:** short and plain English.
- **Tests:** test-first.
- **Gate:**
  ```bash
  uv run ruff check . && uv run ruff format --check . && uv run mypy
  uv run pytest -q tests/unit tests/integration tests/brain tests/api tests/voice tests/memory tests/mcp tests/skills tests/scheduler tests/delivery tests/spine
  pnpm -r typecheck && pnpm -r test
  uv run python scripts/check_raw_hex.py && uv run python scripts/check_agent_name.py && uv run python scripts/check_one_writer.py && uv run python scripts/containment_check.py
  ```
  macOS has no `timeout` command, and pytest must not be piped into `tail`.

## File map

| File | Change |
|---|---|
| `src/trillion/voice/types.py` | `Word`, `Heard.words`, `STTProvider.word_times` |
| `src/trillion/voice/providers/deepgram.py` | fill `words` from `TurnInfo.words`; `word_times = True` |
| `src/trillion/voice/providers/whisper.py` | `word_times = False` |
| `tests/voice/fakes.py` | `FakeSTT(word_times=...)`, `FakeStream.say(..., words=...)` |
| `src/trillion/voice/timeline.py` | **new**, `Timeline` |
| `src/trillion/api/speech.py` | `Speaker` reports each sentence's levels and spoken text through `on_audio` |
| `src/trillion/api/listening.py` | `sent_s`, mic levels, the current turn's timed words, room level; remove `Onsets`/`_placed`/`voice_since`/`words_by`/`mark_cut_in`/forget bookkeeping |
| `src/trillion/voice/whose.py` | **new**, `whose()`, `EchoShare` |
| `src/trillion/voice/overlap.py` | keep `decide`, `confirm`, acks, fillers, stops, numbers; remove `judge_watch`, `share_hers`, `echo_through`, `judged_prefix`, `echo_of`, `late_echo`, `is_said`, `ECHO_SHARE`, `_MOST_STRAYS`; export the matching helpers `whose` needs |
| `src/trillion/api/session.py` | the timeline per spoken reply; per-word judgement in the watch, promotion, follow-up and `_heard`; remove `_her_end*`, grace branches, `echo_of`/`late_echo`/`strip_hers` uses |
| Tests | new `tests/voice/test_timeline.py`, `tests/voice/test_whose.py`; update `tests/voice/test_stt_contract.py`, `tests/api/test_overlap_watch.py`, `tests/api/test_overlap_session.py`, `tests/voice/test_overlap.py`, `tests/voice/test_listener.py` |
| Docs | spec as built; fluid-turns spec §3 pointer; PRD VOI-9; TESTING 2.2a |

---

### Task 1: Word times on `Heard`

**Files:**
- Modify: `src/trillion/voice/types.py`, `src/trillion/voice/providers/deepgram.py`, `src/trillion/voice/providers/whisper.py`, `tests/voice/fakes.py`
- Test: `tests/voice/test_stt_contract.py`

- [ ] **Step 1: Failing test.** Add to `tests/voice/test_stt_contract.py`. `turn_info` already exists there; add a `words` parameter to it.

```python
def test_deepgram_reports_when_each_word_was_said() -> None:
    raw = json.dumps({"type": "TurnInfo", "event": "Update", "turn_index": 0,
                      "transcript": "wait stop",
                      "words": [{"word": "wait", "start": 2.0, "end": 2.4, "confidence": 0.9},
                                {"word": "stop", "start": 2.4, "end": 2.88, "confidence": 0.9}]})  # fmt: skip
    heard = deepgram.parse(raw)
    assert heard is not None
    assert heard.words == (Word(text="wait", start_s=2.0, end_s=2.4), Word(text="stop", start_s=2.4, end_s=2.88))
    assert Deepgram(api_key=lambda: "k").word_times is True
```

  Import `Word` from `trillion.voice.types`.

- [ ] **Step 2: Run it.** Run `uv run pytest -q tests/voice/test_stt_contract.py -k word`. Expected: FAIL, because there is no `Word`.

- [ ] **Step 3: Implement.**

  `voice/types.py`:

```python
@dataclass(frozen=True, kw_only=True)
class Word:
    """One word and when it was said, in seconds of audio since the stream opened."""

    text: str
    start_s: float
    end_s: float

    @property
    def mid_s(self) -> float:
        return (self.start_s + self.end_s) / 2
```

  - Add to `Heard`: `words: tuple[Word, ...] = ()`. The comment says these are the current turn's words with times, for recognizers that report them (#102).
  - Add to `STTProvider`: `word_times: bool`. The comment says it is true when `Heard.words` carries times.
  - In `deepgram.py` `parse`: build `words` from `msg.get("words") or []`. Each item must be a dict with a str `word` and number `start`/`end`; skip malformed items. Pass `words=tuple(...)` to `Heard`, and set `word_times = True` on the class.
  - `whisper.py` and any other STT class: `word_times = False`.
  - `tests/voice/fakes.py`:
    - `FakeSTT.__init__(..., word_times: bool = False)`;
    - `FakeStream.say(..., words: tuple[Word, ...] = ())`, passed through to `Heard`.

- [ ] **Step 4: Run.** Run `uv run pytest -q tests/voice tests/api` and `uv run mypy`. Expected: PASS.

- [ ] **Step 5: Commit.** Message: `Hearing: when each word was said (#102)`.

---

### Task 2: `Timeline`: her playback on the recognizer's clock

**Files:**
- Create: `src/trillion/voice/timeline.py`
- Modify: `src/trillion/api/speech.py` (`Speaker`)
- Test: `tests/voice/test_timeline.py`, `tests/voice/test_speaker.py`

- [ ] **Step 1: Failing tests.**

```python
"""Her playback placed on the recognizer's clock (#102)."""

from trillion.voice.timeline import LEAD_S, TAIL_S, WINDOW_S, Timeline


def _tl() -> Timeline:
    tl = Timeline()
    tl.sentence(1, written="We leave at six.", spoken="We leave at six.", levels=[0.2] * 50)  # 1.0 s
    tl.sentence(2, written="Bring your ticket.", spoken="Bring your ticket.", levels=[0.1] * 25)  # 0.5 s
    return tl


def test_sentences_sit_where_they_were_reported_played() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.start(2, at_s=11.2)
    assert tl.at(10.5).seq == 1
    assert tl.at(11.1) is None or tl.at(11.1).seq == 1  # inside 1's tail margin
    assert tl.at(11.4).seq == 2
    assert tl.at(11.7 + TAIL_S + 0.01) is None
    assert tl.at(10.0 - LEAD_S - 0.01) is None


def test_her_level_over_a_span() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    assert abs(tl.her_level(10.2, 10.4) - 0.2) < 1e-9
    assert tl.her_level(20.0, 20.5) == 0.0


def test_a_sentence_not_reported_yet_follows_the_one_before() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    # Sentence 2 sent but its report hasn't arrived: assumed right after sentence 1.
    assert tl.at(11.2).seq == 2


def test_her_first_sentence_before_any_report_starts_at_the_watch() -> None:
    tl = _tl()
    tl.begin(at_s=4.0)
    assert tl.at(4.3).seq == 1


def test_her_end() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.start(2, at_s=11.0)
    assert tl.ended_at() is None
    tl.finish()
    assert abs(tl.ended_at() - (11.5 + TAIL_S)) < 1e-9


def test_a_sentence_never_played_has_no_span_once_she_is_stopped() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.stop()
    assert tl.at(11.2) is None
```

- [ ] **Step 2: Run them.** Expected: FAIL, because there is no module.

- [ ] **Step 3: Implement `src/trillion/voice/timeline.py`.**

```python
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
        """Her audio for one sentence, as it was sent to the page."""
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
        out: list[tuple[Spoken, float]] = []
        prev_end = self.watch_s
        for seq in sorted(self.sentences):
            s = self.sentences[seq]
            start = s.start_s
            if start is None:
                if self.stopped or prev_end is None:
                    break
                start = prev_end  # not reported yet: assumed right after the last
            out.append((s, start))
            prev_end = start + s.length_s
        return out

    def at(self, t: float) -> Spoken | None:
        """The sentence playing at `t` on the recognizer's clock, margins included."""
        for s, start in self._spans():
            if start - LEAD_S <= t <= start + s.length_s + TAIL_S:
                return s
        return None

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
```

  Adjust the first test's expectation if your margin logic says 11.1 belongs to sentence 1's tail. The intent is that a word at 11.1 is inside her playback. Keep that assert simple, e.g. `assert tl.at(11.1) is not None`.

- [ ] **Step 4: `Speaker` reports each sentence's levels.**
  - `Speaker.__init__` gains `on_audio: Callable[[int, str, str, list[float]], None] | None = None`, called with seq, written, spoken and levels.
  - In `_synthesize`, before each frame is sent, compute RMS per `WINDOW_S` of the PCM, at the voice's sample rate (`self._provider.output_format.sample_rate`, else 16000), and call `on_audio(seq, written, spoken, levels)`. Keep the written and spoken text per seq; `_said` already holds both.
  - Test in `tests/voice/test_speaker.py`: a fake TTS yielding 0.1 s of a constant tone at a known amplitude gives 5 levels of about that RMS.

- [ ] **Step 5: Run** `uv run pytest -q tests/voice` and mypy. **Commit** with message `Voice: her playback on the recognizer's clock (#102)`.

---

### Task 3: Listener: audio sent, mic levels, the turn's timed words

**Files:**
- Modify: `src/trillion/api/listening.py`
- Test: `tests/voice/test_listener.py`

- [ ] **Step 1: Failing tests.**
  - `listener.sent_s` equals the seconds of audio forwarded to the recognizer, including the backlog.
  - `listener.mic_level(t0, t1)` returns the mean RMS over 20 ms windows of forwarded audio. For example, `tone(0.5)` after `room(1.0)` makes `mic_level(1.1, 1.4)` far above `mic_level(0.2, 0.8)`.
  - `listener.turn_words()` returns the latest `Heard.words` of the current recognizer turn, and is empty after a `done` starts a new turn.
  - `listener.room_level()` returns the median level of windows with no recent words.

- [ ] **Step 2: Implement.**
  - Keep a deque of `(t_end_s, rms)` per 20 ms window. Fill it in `_feed` from the PCM actually sent, on the same clock as `sent_s`, and keep 30 s.
  - Store the latest `heard.words` when `heard.turn` is not "done"; on `done` keep them as the finished turn's words.
  - Remove `Onsets`, `_placed`, `voice_since`, `words_by`, `mark_cut_in`, `cut_in`, `began`/`began_heard`, and the forgotten-word bookkeeping (`forget`, `_skipped`, `judged`). Delete their tests. The session stops using them in Task 5.
  - If removing them breaks the session before Task 5, keep thin compatibility stubs, and say so in your report.

- [ ] **Step 3: Run** `uv run pytest -q tests/voice` and mypy. **Commit** with message `Hearing: the listener keeps its clock, levels and timed words (#102)`.

---

### Task 4: `whose()`: hers or theirs

**Files:**
- Create: `src/trillion/voice/whose.py`
- Modify: `src/trillion/voice/overlap.py` (export `words_of`, `matches`, `is_stop`; `whose` uses them)
- Test: `tests/voice/test_whose.py`

- [ ] **Step 1: Failing tests.**

```python
"""Hers or theirs, by when a word was said (#102)."""

from trillion.voice.timeline import Timeline
from trillion.voice.types import Word
from trillion.voice.whose import EchoShare, whose


def _tl() -> Timeline:
    tl = Timeline()
    tl.sentence(1, written="Give me the actual ask.", spoken="Give me the actual ask.", levels=[0.2] * 75)
    tl.start(1, at_s=10.0)  # plays 10.0 to 11.5
    tl.finish()
    return tl


def mic(level: float):  # type: ignore[no-untyped-def]
    return lambda t0, t1: level


def W(text: str, t: float) -> Word:
    return Word(text=text, start_s=t, end_s=t + 0.3)


def test_said_while_she_was_silent_is_theirs() -> None:
    tl = _tl()
    assert whose(W("yes", 12.5), tl, mic(0.05), share=0.1) == "theirs"
    assert whose(W("hello", 9.0), tl, mic(0.05), share=0.1) == "theirs"


def test_her_word_said_while_she_said_it_is_hers() -> None:
    assert whose(W("actual", 10.8), _tl(), mic(0.02), share=0.1) == "hers"


def test_her_last_word_misheard_at_echo_level_is_hers() -> None:
    # "ask" heard as "ass" during her playback, as loud as her echo.
    assert whose(W("ass", 11.2), _tl(), mic(0.02), share=0.1) == "hers"


def test_a_loud_word_over_her_is_theirs() -> None:
    assert whose(W("tuesday", 10.6), _tl(), mic(0.2), share=0.1) == "theirs"


def test_a_stop_word_not_in_her_sentence_is_theirs_if_loud_enough() -> None:
    assert whose(W("stop", 10.6), _tl(), mic(0.2), share=0.1) == "theirs"


def test_echo_share_is_learned_from_her_words() -> None:
    share = EchoShare()
    for _ in range(5):
        share.learn(mic_level=0.02, her_level=0.2)
    assert abs(share.value - 0.1) < 1e-9
```

- [ ] **Step 2: Implement `src/trillion/voice/whose.py`.**

```python
"""Is a word hers (her echo) or theirs (the user)? Judged by when it was said (#102).

Outside her playback: theirs. Inside and matching what she was saying then: hers.
Inside and not matching: a misheard echo unless the microphone was clearly louder
than her echo would be. Pure.
"""

from __future__ import annotations

from collections.abc import Callable
from statistics import median
from typing import Literal

from trillion.voice.overlap import is_stop, matches, words_of
from trillion.voice.timeline import Timeline
from trillion.voice.types import Word

Whose = Literal["hers", "theirs"]
OVER_ECHO = 2.5
OVER_ROOM = 4.0
ROOM = 0.002
_SHARE_START = 0.5
_SHARE_MIN, _SHARE_MAX = 0.02, 1.0

Level = Callable[[float, float], float]


def whose(word: Word, timeline: Timeline, mic: Level, *, share: float, room: float = ROOM) -> Whose:
    s = timeline.at(word.mid_s)
    if s is None:
        return "theirs"
    hers = words_of(f"{s.written} {s.spoken}")
    w = (words_of(word.text) or [""])[0]
    if w and matches(w, hers) and not (is_stop(w) and not matches(w, hers, stop=True)):
        return "hers"
    expected = timeline.her_level(word.start_s, word.end_s) * share
    loud = mic(word.start_s, word.end_s)
    return "theirs" if loud >= max(expected * OVER_ECHO, room * OVER_ROOM) else "hers"


class EchoShare:
    """How much of her comes back through the microphone, learned from her words."""

    def __init__(self) -> None:
        self._seen: list[float] = []

    def learn(self, *, mic_level: float, her_level: float) -> None:
        if her_level > 0.01:
            self._seen.append(min(_SHARE_MAX, max(_SHARE_MIN, mic_level / her_level)))
            self._seen = self._seen[-50:]

    @property
    def value(self) -> float:
        return median(self._seen) if self._seen else _SHARE_START
```

  In `overlap.py`:
  - expose `words_of` (rename `_words`, keeping an alias if it's used internally);
  - expose `matches(w, hers_words, *, stop=False)`, built from `_mine`/`_stop`;
  - expose `is_stop(w)`.

  Adjust to the real helpers and keep them pure.

- [ ] **Step 3: Run** `uv run pytest -q tests/voice` and mypy. **Commit** with message `Voice: hers or theirs, by when a word was said (#102)`.

---

### Task 5: The session judges every word by time

**Files:**
- Modify: `src/trillion/api/session.py`, `src/trillion/voice/overlap.py` (remove dead functions)
- Test: `tests/api/test_overlap_watch.py`, `tests/api/test_overlap_session.py`, `tests/voice/test_overlap.py`

- [ ] **Step 1: Failing session tests.** Use `FakeSTT(turns=True, word_times=True)` and a fake core voice whose sentences have known levels.
  - The four live failures:
    - **"ass."** Her reply ends "…give me the actual ask."; the recognizer then reports `ass.` with a time inside her last sentence. Expect no user turn.
    - **"three hundred".** "skip that please" said loud over her, then a late report of "three hundred" timed inside her sentence. Expect the turn to be exactly "skip that please".
    - **"Standing by."** Her last sentence "Standing by." is echoed with times inside it and reported 0.7 s after she ends. Expect no turn.
    - **"What about Tuesday?"** Loud, over her, with misheard echo words before and after it, timed inside her sentences at echo level. Expect the turn to be exactly "What about Tuesday?".
  - Must still pass:
    - "Yes." timed 0.5 s after `ended_at` is turn 2;
    - "Six?" after "…leave at six?", timed after `ended_at`, is turn 2;
    - "Wait. Stop." over her interrupts;
    - "yeah" over her carries on;
    - her own "No, the train…" echo (inside, matching) never stops her;
    - echo-only for a whole reply gives no duck and no turn.

- [ ] **Step 2: Implement.**
  - **A `Timeline` per spoken reply.**
    - Create it when the watch opens, or with the turn's `Speaker`, whichever comes first. `begin(at_s=listener.sent_s)` runs when the watch starts.
    - The `Speaker`'s `on_audio` feeds `timeline.sentence(...)`.
    - `_sentence_played` (the `playback.sentence` handler) calls `timeline.start(seq, at_s=<watch listener>.sent_s)`.
    - `speaker.finish()` done → `timeline.finish()`.
    - She is stopped (interrupt, barge, Stop) → `timeline.stop()`.
    - Keep the last reply's timeline until the next reply begins, so her late echo is still judged after she ends.
  - **An `EchoShare` per session.** Each word judged hers by a text match learns from `listener.mic_level` and `timeline.her_level`.
  - **The watch** (`_watch_once`, `_leave_watch`, `_become_turn`, `_follow_up_from`, `listen.start` during a watch, `barge`).
    - Split `listener.turn_words()` into hers and theirs with `whose(...)`.
    - The candidate is the theirs words, in order. Feed their text to `confirm`/`decide` exactly as today: `overlap.duck`, `CONFIRM_S`, acknowledgements and fillers, stop words, the 2-word and 3-word rules.
    - On interrupt or promotion, the user's turn text is the theirs words, in order, from this point on. Give the listener a way to report only theirs words, e.g. a filter callable on the promoted listener that `text()` applies to timed words. Untimed words fall back to report time minus 0.4 s.
  - **Follow-up.** When `timeline.ended_at()` has passed on the recognizer's clock (`listener.sent_s >= ended_at`), the watch becomes the follow-up window, keeping only theirs words. Remove the 2 s grace and `ECHO_GRACE_S`.
  - **`_heard` safety net.** Keep only the turn's theirs words, judged against the last reply's timeline. If none are left, end quietly with no notice.
  - **Drafts** start only from theirs words.
  - **Remove** `_her_end*`, `strip_hers`/`echo_of`/`late_echo`/`echo_through`/`judged_prefix`/`judge_watch`/`share_hers`/`is_said` uses, and the functions themselves from `overlap.py`, with their tests.

- [ ] **Step 3: Run** the full gate, and the four overlap/draft/listener files 10 times. **Commit** with message `Voice: the session judges every word by when it was said (#102)`.

---

### Task 6: Docs

- [ ] Spec `docs/specs/2026-10-24-echo-timing.md`: as built (record any deviations).
- [ ] `docs/specs/2026-10-24-fluid-turns.md` §3: replace the echo-rule bullets with a pointer to the echo-timing spec.
- [ ] `docs/PRD.md` VOI-9: one sentence saying her echo is told apart by when each word was said.
- [ ] `docs/TESTING.md` 2.2a: add the four named live cases.
- [ ] Commit with message `Echo timing: docs (#102)`.

### Task 7: Live check (controller)

- [ ] Run fake-microphone runs at 8% and 35% echo: echo only, "Wait, stop", "Skip that", "What about Tuesday?", and "Yes" after she ends. A false turn or a lost first word is a failure.
