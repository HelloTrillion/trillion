# Telling her echo from the user by when each word was said

**Date:** 2026-10-24 · **Issue:** HelloTrillion/trillion#102 · **Requirements:** VOI-9 (barge-in), VOI-12 · **Builds on:** `docs/specs/2026-10-24-fluid-turns.md` §3-4 · **Respects:** VOI-11 (the core never names a vendor), VOI-15a, VOI-15b, VOI-17, privacy (nothing heard is logged)

While she speaks, with hearing that judges turns, the microphone streams to the
recognizer, and the core has to tell her own voice coming back (echo) from the user.
Today that is judged from the words alone: whether they appear in what she is
saying, plus loudness onsets and timing guesses. When the recognizer mishears her
echo, the misheard words look like the user's. Live runs showed four failures:

- her final word "ask" heard as "ass." became a turn;
- her late echo "three hundred" was added to the user's "Skip that.";
- her short last sentence "Standing by." came back as a turn;
- "What about Tuesday?" split into a turn of misheard echo and "about Tuesday?".

The recognizer reports when each word was said, and the core knows when she was
playing each sentence and how loud she was. This spec judges every word by that:
when it was said, what she was saying then, and how loud the microphone was compared
with her expected echo. It replaces the text-matching rules (decision D1 below).

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Replace the text-matching echo rules** with one per-word judgement. | What the user chose. One rule set to test and reason about. Only hearing that judges turns streams while she speaks, and Deepgram Flux reports word times, so nothing else needs the old rules. |
| D2 | **Her playback is placed on the recognizer's clock by stamping.** When the core handles `playback.sentence`, it records how much audio the watching listener has sent the recognizer so far. | The page sends its playback reports and its microphone frames on the same socket, in order, so the audio sent so far is the audio captured up to that report. About a tenth of a second of accuracy, which is enough to tell a word inside a sentence from one between sentences. No new page code. |
| D3 | **A word inside her playback that does not match what she was saying is judged by loudness:** the microphone level during the word against her expected echo at that moment. | The server makes her audio, so it knows her loudness every 20 ms. The share of her that comes back is learned from words already judged echo. A person speaking over her is clearly louder than her echo; a misheard echo is not. |
| D4 | **Word timing becomes a vendor-neutral part of `Heard`.** Only `deepgram.py` reads Flux's fields. A recognizer that judges turns but gives no word times cannot stream while she speaks: the page uses the local detector, as with Whisper. | VOI-11. |
| D5 | **The decision steps stay as they are:** duck on a candidate, then confirm, acknowledgements, stop words and fillers (fluid-turns §3). They now act only on words judged the user's. | They work. What failed was deciding whose words they were. |

## How it works

### 1. Word times from the recognizer

`voice/types.py`:

- `Heard` gains `words: tuple[Word, ...] = ()`.
- `Word(text: str, start_s: float, end_s: float)` holds seconds of audio since the stream opened.
- `STTProvider` gains `word_times: bool`.

`voice/providers/deepgram.py` fills `words` from each `TurnInfo`'s `words` list (`word`, `start`, `end`) and sets `word_times = True`. Live check: a clip that starts 2.0 s into the stream reports its first word at `start=2.0`.

Flux re-sends the whole turn in every report, with word times refined as it goes. The listener keeps the latest list for the current turn.

### 2. Her playback timeline

New `voice/timeline.py` holds `Timeline`, which is pure and owns no I/O. It knows:

- **Each sentence's span.** `Timeline.start(seq, at_s=...)` is called when `playback.sentence` arrives, with `at_s` the watching listener's audio sent so far (`Listener.sent_s`). A sentence runs from `at_s` for its length.
- **Each sentence's length and loudness.** `Speaker` measures her audio as it sends it: RMS per 20 ms window (`WINDOW_S`), keyed by `seq`, and reports it through `on_audio(seq, written, spoken, levels)`. `Timeline.sentence(seq, written=, spoken=, levels=)` receives them (more for a known `seq` extends it), and the length is the number of windows times `WINDOW_S`. A sentence not reported yet is assumed to follow the one before (the first, the moment the watch opened: `Timeline.begin(at_s=...)`), and never to start before its audio was first sent to the page (`sentence(..., at_s=)`, the watching listener's `sent_s` then). So words the user says while she is still thinking stay theirs once her audio arrives. A watch opened mid-reply places the sentence last reported playing from how long ago that report came, and the sentences before it are over.
- **What she was saying.** Each sentence's written and spoken (speakable) text, so a word can be matched against the sentence playing at that moment.
- **Margins.** `LEAD_S = 0.15` before and `TAIL_S = 0.35` after each span, for capture, network and room delay.

Queries:

- `at(t)` returns the sentence playing at time `t` (with margins), or `None`.
- `her_level(t0, t1)` returns her loudness over a span.
- `ended_at()` returns the end of her last sentence plus `TAIL_S`, once every sentence was sent (`finish()`) or she was stopped (`stop()`, from what was played). It may be an estimate.
- `ended_final(now_s=...)` returns it only once it can be relied on: her last sentence was reported played, she was stopped, or the estimate is `FINAL_CAP_S` (3 s) old. The session switches to the follow-up window on this one, so a page playing behind can't make her last sentence's echo count as the user's. It can turn final later, so callers ask again rather than keep the first answer.

When she is stopped (`stop(at_s=)`, the watching listener's `sent_s`), the sentence playing then ends there, plus `TAIL_S` for what the page had already scheduled; a sentence not yet under way never gets a span. Without a time, a sentence never reported played gets no span; later reported ones still do.

How far her words are heard from where she was placed (`align`, `offset_s()`) is measured and logged as a number when the next reply begins, but not applied.

### 3. The microphone's loudness

The watching `Listener` keeps the RMS of every 20 ms of audio it forwards, on the same clock, for the last 30 s. `mic_level(t0, t1)` gives the level over a span.

`ROOM` is the room's own level: the median of the windows outside her playback in which no word was heard, starting at 0.002. `OVER_ROOM = 4`, as in the page's detector.

### 4. Judging a word

New `voice/whose.py` holds `whose(word, timeline, mic, echo_share) -> "hers" | "theirs"`. It is pure and tested on its own.

1. **Not during her playback** (`timeline.at(word.mid)` is `None`): `theirs`. This covers before her first sentence, the gaps between sentences, and after `ended_at`.
2. **During her playback, and it matches a word of the sentence playing then:** `hers`.
   - Matching is loose: lowercase, punctuation stripped, numbers spelled out, and another form of the word in either direction. That is a prefix of 4+ letters, or 3+ letters followed by an ending (s, es, d, ed, ing, er, ers, ly, 's), with a final "e" dropped ("leave" and "leaving"). So "ask" and "asked" match, but "the" and "then", or "for" and "forget", do not.
   - A stop word is `hers` only if her sentence holds that word or an inflection of it ("stop" and "stopped").
   - A word the recognizer wrote as several ("twenty-six", "$5") matches only if all its parts do.
   - Her own words said by the user over her ("send it" over "Should I send it or wait?") are `theirs` only once `echo_share` is learned from 5+ words, and when `mic ≥ max(2 × OVER_ECHO × echo_share × her_wide, ROOM × OVER_ROOM)`. `her_wide` is her level over the word widened by 0.3 s each side, since a word's times can be a little off hers.
3. **During her playback, not matching:** loudness decides.
   - `mic` is the microphone over the first half of the word (`Listener.word_level`): a word's end often runs on into the next voice, so her word just before the user starts would otherwise take their loudness. `expected = her_level(word) × echo_share`.
   - `theirs` if `mic ≥ max(expected × OVER_ECHO, ROOM × OVER_ROOM)`, with `OVER_ECHO = 2.5`.
   - Otherwise `hers`: a misheard echo.

**Learning `echo_share`.** It is the median of `mic / her_level` over words judged `hers` by rule 2, clamped to [0.02, 1.0], over the last 50. It starts at 0.5, which is cautious: misheard echo stays hers. Once it has a sample, a word at `OVER_ECHO` times the current value or more is not learned from, because that is the user saying her word. It is learned per session and kept across her replies.

### 5. Using the judgement

`Session`'s watch (fluid-turns §3) now works per word.

- **While watching:**
  - The listener's current turn is split into words judged `hers` and words judged `theirs`.
  - The candidate is the run of `theirs` words.
  - The steps that follow are the existing `confirm`/`decide`, given only `theirs` words: ducking, the 1.0 s confirmation, acknowledgements and fillers, stop words, and the 2-word and 3-word rules.
- **When the user cuts in,** their turn is their words, in order. That always starts at their first word and never contains her echo, whether it arrives before, between or after their words, early or late.
- **When her reply is heard to the end,** the watch becomes the follow-up window as soon as `ended_at` passes, with no separate 2 s grace. Words from before `ended_at` that were judged `hers` are dropped. A one-word "Yes." said after `ended_at` is theirs.
- **The `_heard` safety net** keeps only the turn's `theirs` words. If none are left, listening ends quietly with no notice.
- **Drafts** start only from `theirs` words.

**Removed:**

- in `voice/overlap.py`: `judge_watch`, `share_hers`, `echo_through`, `judged_prefix`, `echo_of`, `late_echo`, `is_said`, `ECHO_SHARE`, `_MOST_STRAYS`;
- in `api/listening.py`: `Onsets`, `_placed`, `voice_since`, `words_by`, `mark_cut_in`, the forgotten-word bookkeeping (`forget`, `_skipped`, `judged`);
- in `api/session.py`: `_her_end`, `strip_hers`, the grace-period branches and their tests.

`decide`, `confirm`, the acknowledgement and filler lists, `_STOPS` and number spelling all stay.

### 6. When timing is missing

- **No word times** (a recognizer with `word_times = False`): the page does not stream while she speaks and uses the local detector. That is the Whisper path today.
- **A word with no time** (a report with text but an empty `words` list): it is judged as the time of the report minus 0.4 s.
- **No `playback.sentence` report yet for a sentence the core has sent:** that sentence's span is assumed to start right after the previous one ends, or at the watch's start for the first, but no earlier than its audio was sent. This covers her first words' echo arriving before the first report.
- **A follow-up window opened without a watch** (no clock of hers): her last sentence is taken to end as the window opens, so her echo in its first `TAIL_S` is still hers.

## Errors and edge cases

- **A reconnect** of the recognizer mid-watch restarts its clock. The watch ends, as it does today when hearing fails.
- **The user speaks exactly in sync with her, very quietly:** their words are judged a misheard echo and dropped. Speaking up, or saying "stop" or "wait" (which aren't in her sentence), still works.
- **Her voice played by the computer itself** (the system voice): the core has no audio, so no loudness. Rule 3 then treats every non-matching word as `theirs`. Stated as a limit; that voice already doesn't stream the watch (fluid-turns D7).
- **Privacy:** nothing heard is logged. Levels and times are kept only in memory for the session and discarded with it.

## Testing

- **`tests/voice/test_whose.py`** (pure), covering:
  - before, between and after her sentences gives theirs;
  - a matching word gives hers;
  - a misheard word at echo level gives hers;
  - a loud non-matching word gives theirs;
  - stop words, numbers and stems;
  - the margins;
  - learning `echo_share`.
- **`tests/voice/test_timeline.py`:** stamping, lengths, a sentence never reported, and the fallback for her first sentence.
- **`tests/voice/test_stt_contract.py`:** Deepgram fills `Heard.words` from `TurnInfo` and sets `word_times`.
- **Session tests** use a fake recognizer that sends word times and a fake speaker with known levels. Each of the four live failures is reproduced by name and must now pass:
  - "ass." after her "…actual ask.";
  - "three hundred" after "Skip that.";
  - "Standing by.";
  - "What about Tuesday?" over her.
  - The cases from fluid-turns that must still pass: "Yes." and "Six?" after she ends; "Wait. Stop…"; "yeah" carrying on; her own "No, the train…".
- **Live fake-microphone runs:** her output fed back at 8%, then 35%, with:
  - echo only;
  - "Wait, stop";
  - "Skip that";
  - "What about Tuesday?";
  - "Yes" after she ends.

  A false turn or a lost first word is a failure at both levels.

## Not in this spec

- Locating her voice in the microphone signal by correlation (option 3 in the design discussion). Revisit if stamping proves too coarse.
- Word timing for local Whisper.
