# Fluid turns: pauses, early thinking and talking over her

**Date:** 2026-10-24 · **Requirements:** VOI-12 (semantic turn detection), VOI-9 (barge-in), VOI-3 · **Respects:** VOI-11 (the core never names a vendor), VOI-15a, VOI-15b, VOI-15c, VOI-16, VOI-17, SPN-2, MEM-5 · **Leads into:** a hands-free open microphone (its own spec)

Today a pause of about a second ends the user's turn for good, and any word
spoken over her stops her. This makes the conversation feel like taking turns on
a radio. This spec makes three changes, Deepgram first:

1. **Pauses don't end the turn.** Whether the user has finished is judged by
   meaning, not silence. She starts thinking when they have *probably* finished
   and throws the draft away if they carry on.
2. **Talking over her is understood.** "Yeah" or "mm-hm" while she talks lets her
   carry on. A real interruption stops her.
3. **The record holds what was heard.** An interrupted reply is saved up to
   where the user stopped hearing it, not up to where her writing had got to.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Deepgram first.** Deepgram moves from Nova-3 to Flux, its model that judges the end of a turn by meaning. "English" uses `flux-general-en`; "Many languages" uses `flux-general-multi`. Local Whisper and OpenAI hearing keep today's silence rules. | What the user chose. Flux reports *probably done*, *kept going* and *done*, which is exactly what early thinking needs. |
| D2 | **The core never names a vendor (VOI-11).** A hearing adapter that can judge turns says so (`turns = True`) and reports three generic signals. Only `deepgram.py` knows the word Flux. | Any later recognizer with turn detection plugs in the same way. |
| D3 | **Early thinking drafts words only, and holds them.** On *probably done* the turn starts with its output held: nothing is spoken, shown or saved, the turn counter does not move, and no tool runs. A draft that reaches a tool call waits there. | What the user chose (option 1). Safety does not change: a draft is invisible until the turn is real. |
| D4 | **Early thinking is on by default, with a switch** in Settings → Hearing: "Start thinking before I finish". Shown only when the chosen hearing can judge turns. | What the user chose. Deepgram warns that early thinking adds 50 to 70% more model calls, because some drafts are thrown away. |
| D5 | **Talking over her: duck, then decide.** Her voice drops to 20% at once while about a second of the user's words is heard. Acknowledgements bring her back; anything else stops her. | What the user chose. |
| D6 | **Acknowledgements are a short English list.** In any other language every word counts as an interruption. | Wrong in the safe direction: the user is heard. |
| D7 | **Hearing that cannot give words mid-speech keeps today's barge-in.** With local Whisper or OpenAI, a voice over hers stops her at once, as now. | There are no words to judge with. |
| D8 | **An interrupted spoken reply is kept up to the sentence being heard,** then "…" and the existing interrupted marker. Text she wrote but never said is dropped from the conversation, the screen and her memory. | The record matches what happened. |
| D9 | **With hearing that judges turns, the microphone streams to it while she speaks, and her echo is told from the user by the words** (*as built after the second live test*). The page's loudness detector is used only for hearing that cannot judge turns. | What the user chose. Over speakers, the detector missed the user or lost their first words (see §3). |

## What the user sees

- **Pausing to think.**
  - With Deepgram, a pause in the middle of a thought no longer ends the turn. "I need to remember to… call the dentist" arrives as one message.
  - Silence still ends a turn eventually: after 5 seconds at most, even mid-thought.
  - If the hearing calls the turn over on a word that leads on ("I went to the shop and…", "um"), she keeps listening: what they say next joins the same message, and 2.5 seconds of quiet ends it (still 5 seconds at most).
  - When the words do sound finished, she answers sooner than today, because she started thinking during the pause.
- **Talking over her.**
  - The moment the user speaks, she gets quieter.
  - "Yeah", "mm-hm", "right", "okay", "sure", "got it": she comes back to full volume and carries on. Nothing is added to the conversation.
  - "Wait", "no", "stop", or any other words: she stops, and what the user says becomes their turn. Their first words are not lost.
  - A cough, a door or nothing at all: she comes back to full volume and carries on.
- **After an interruption.** Her message in the conversation ends where the user stopped hearing it: "We could leave at six, or if traffic's bad…" and the interrupted marker.
- **Settings → Hearing.** With Deepgram chosen, a switch reads "Start thinking before I finish", with the note "Faster replies. Some drafts are thrown away, which uses more of your model." It is on by default.

## How it works

### 1. Hearing that judges turns (VOI-12)

`voice/types.py`:

- `STTProvider` gains `turns: bool`. It is true when the recognizer itself decides when the user has finished.
- `Heard` gains `turn: Literal["maybe", "resumed", "done"] | None`:
  - `maybe`: probably finished, and words may still follow.
  - `resumed`: they carried on after a `maybe`.
  - `done`: the turn is over. `text` is the whole turn and `final` is true.

  Recognizers without turn detection never set it.

`voice/providers/deepgram.py` (the only file that knows Flux):

- **Connecting:** `wss://api.deepgram.com/v2/listen` with `model=flux-general-en` or `flux-general-multi`, `encoding=linear16` and `sample_rate=16000`. Also `eot_threshold=0.7`, `eager_eot_threshold=0.5` and `eot_timeout_ms=5000`; the eager threshold must not exceed the end threshold.
- **Its signals in our words:**
  - `TurnInfo` with `Update` or `StartOfTurn` becomes interim words.
  - `EagerEndOfTurn` becomes `maybe`.
  - `TurnResumed` becomes `resumed`.
  - `EndOfTurn` becomes `done`, with the turn's transcript as final text.
- **Audio:** sent in 80 ms chunks (2,560 bytes), as Deepgram recommends.
- **Closing:** `CloseStream`, as now.
- **Unchanged:** choosing a language, checking the key and listing choices.
- **Nova-3 is removed.** Both choices now use Flux.

`api/listening.py`, the `Listener`:

- **When the recognizer judges turns,** `done` is the verdict "end" when the words do not lead on. The local rules in `voice/endpoint.py` still decide `nothing`, `inaudible`, `too_long` and a dead microphone. The `FAST_S`, `SLOW_S`, `DONE_S` and `sounds_finished` paths are not used.
- **A `done` on words that lead on is held.** Flux was seen ending a turn 0.7 s into a pause after "I went to the shop and". When the whole message so far ends on a word like "and", "to" or "um", an ellipsis, a comma or a dash (`leads_on`), she keeps listening: the words are kept, the session is told `maybe` (a draft may start), and the recognizer's next turn joins the same message, with `resumed` on its first words. A later `done` that does not lead on ends it. So does `HELD_QUIET_S` (2.5 s) of quiet since the voice stopped, by the audio's clock: an early `done` waits about 2.5 s of silence in all, and one the recognizer forced after 5 s of silence ends at once. A held turn ends `HELD_MAX_S` (5 s) after the `done` at most, and once it ends, words that follow belong to the next turn. A missing full stop alone does not hold a turn, because the recognizer may not punctuate; a question or an exclamation ("So?") never does.
- **`maybe` and `resumed`** are passed to a new `on_turn` callback with the words so far.
- **Otherwise,** everything works exactly as today.

### 2. Early thinking (draft and hold)

A **draft** is a real turn started early, with a closed **gate**.

- **On `maybe`:** if the switch is on, a model is set, no turn is running and the history is not due to be compacted, the session starts a draft from the words so far (`api/draft.py`, `Draft`).
  - **Building it:** the user's message is built exactly as a confirmed turn would build it (the context block, the recalled memories, the words), but not saved. `_save_input` is split into building the message and committing it, so a used draft saves that same message byte for byte.
  - **What runs:** the draft runs only the model's **first call**, with the same system prompt, history, tools and token limit a confirmed turn would send. It records each event (text, tool calls, the end) as it arrives and does nothing else.
  - **Holding:** nothing reaches the page, the speaker, the history or a tool, because none of those is in the draft. A first call that ends in a tool call simply ends there; the call is not run.
  - **Where it lives:** `Session._draft`, separate from `_task`. The microphone is still open, so the door already counts the session as busy (VOI-17). Nothing about notices changes.
- **On `resumed`:** the draft is cancelled and listening carries on.
- **Its cost:** a draft's usage is recorded once the provider reports it, at the end of the call. A draft cut short before then (the usual case on `resumed`) isn't counted, though the provider may still bill its input.
- **On `done`**, the draft is used when the words match (compared ignoring case, spacing and punctuation), and nothing has changed since it started (the same history length and summary, and no tap's note waiting):
  1. The draft's message is committed and the turn counter moves, exactly as today (VOI-15a).
  2. The turn runs as usual, except that `run_turn` takes the draft's recorded events, and then its live ones, as its first call (`brain/loop.py`: `run_turn(..., first=...)`) instead of calling the model again.
  3. So the held text reaches the speaker and page in order, and a tool call it asked for is dispatched by the confirmed turn, with that turn's number and context (SPN-2, MEM-5).
- **The words differ, or something changed:** the draft is cancelled and the turn starts normally.
- **The system prompt changed** (a rule or skill turned on or off while it ran): the turn, which reads the prompt anyway, compares it with the one the draft sent and lets the draft go.
- **If the draft failed** (a provider error before it was used), it is dropped and the confirmed turn starts normally. A draft's failure is never shown.
- **In the trace,** a turn records `draft: used | dropped | none`. A dropped draft's text is never kept anywhere.

### 3. Talking over her (VOI-9)

*As built after the second live test (D9).* The first build had the page decide, by
loudness, that the user had started talking over her, and only then send audio. With
a real microphone and speakers, cutting in barely worked and the first three or four
words were lost:

- the user had to be about twice as loud as her learned echo, which over speakers is
  often louder than normal speech into a desk microphone;
- while the user talked below that bar, the detector learned their voice as her echo,
  and the bar rose against them;
- only 600 ms before the detection was kept, so earlier words were gone.

So, with hearing that judges turns, the microphone now streams to the recognizer for as
long as she speaks, and the core decides by the words.

**The page** (`apps/web/src/main.ts`, `barge.ts`):

- **Hearing that judges turns, her voice played on this page, barge-in on:** when the
  microphone is held for her reply (right after the user's spoken turn) or watching
  starts at her first sound, the page sends `{type: "overlap.watch"}` once and then
  every microphone frame. It does no loudness detection and no ducking of its own.
  - `overlap.duck`: her playback gain ramps to 0.2 over 80 ms.
  - `overlap.over`: the gain ramps back to 1, and the microphone goes on streaming.
  - `overlap.over` with `end: true`, or `mic.stop`: the watch is over; the microphone
    closes unless it is already the user's turn.
  - `interrupted` / `audio.stop`: she stops, as before; the microphone stays as it is,
    because the core is making it the user's turn (`mic.start` follows) or ends it.
    The page sets no timer of its own then: if the core sends neither, frames go on
    arriving and the core drops them (no watch or listener is open to take them).
  - The page's own 1.5 s close after her end does not apply: the core keeps hearing
    her last words come back for a moment, then opens the window or ends the watch.
  - Asked to stop while the core is deciding (the switch turned off, its hold over),
    the page stops once the core answers `overlap.over` (`afterOver` in `barge.ts`).
  - `mic.start`: the stream simply carries on as the user's turn.
  - When the page stops watching itself (its 15 s hold without a sound from her, the
    switch turned off, the microphone failing), it sends `{type: "overlap.unwatch"}`.
  - The Speak button shows "Listening" ("Listening for you to cut in."), and Settings →
    Hearing says that while she talks the microphone goes to the hearing service so it
    can tell when the user cuts in.
- **Otherwise** (local Whisper, OpenAI): the `BargeDetector` decides that someone
  started speaking over her, and the page sends `barge` with the held pre-roll, as
  before (D7). Two fixes from the live test: after the half-second warm-up, a level
  well above the room only creeps the learned echo and room level up (at most 20% at a
  time, at 0.4% per frame), so the user talking under the bar no longer raises it; and
  the pre-roll keeps two seconds (33 frames), not 600 ms.
- **One microphone, not two** (*as built after the first live test*): after a spoken
  turn, `mic.stop{reason: end}` does not close the microphone when barge-in could watch
  her reply (`holdForReply` in `barge.ts`). It switches to watching and stays open,
  rather than opening again at her first sound: opening an echo-cancelled microphone
  then reconfigured the audio device on a Mac and glitched her first words. Not after
  `too_long`: the user is often still talking. The held microphone closes when the
  turn ends without a sound from her, or after 15 s of waiting.

**The session**, on `overlap.watch`:

- **When:** a reply is coming or being heard: a running turn (including the moment
  right after the user's turn, before her reply starts), her last spoken reply still
  playing (`_tail`), or her sound not yet reported played. Hearing must judge turns,
  a model and a voice must be set, her voice must not be off, and no listening may be
  open. Otherwise it answers `overlap.over` with `end: true`.
- **Watching:** a `Listener` in watch mode streams every frame and never ends by
  itself; the recognizer's turns come and go. Nothing heard is shown, and no draft
  starts. `Listener.turn_text()` is the words of the recognizer's current turn;
  `Listener.forget()` lets go of words already judged (the recognizer repeats the
  whole turn in each report, so they are skipped, not deleted).
- **Echo by words** (`voice/overlap.py`, `judge_watch(words, playing)` →
  `"nothing" | "echo" | "candidate"`, pure): words are her echo when at least 60% of
  them are in what she is saying now: the sentence being heard, the one before **and
  the next one** (her audio runs ahead of its reports), each as she wrote it and as it
  was sent to be said. Numbers and money are spelled out on both sides ("$5" and "five
  dollars", "10:30" and "ten thirty", "2026" and "twenty twenty-six"). Stop words get no say here, so her own "No, the
  train…" coming back does not stop her. Otherwise a candidate takes two words that are
  not hers (fillers aside) or one stop word: each report adds about a word, and one
  misheard word of hers ("noon" as "new") is not the user.
- **What is weighed** (*after review*): the words not yet judged, with up to four judged
  words before them (`judged_prefix`), so a single new word never stands alone. Echo
  is judged up to its last word of hers (`echo_through`); a word of theirs after it
  waits for more. Words the recognizer has revised are judged again from the start.
- **A candidate: duck, then confirm** (*as built after the third live run*). Her
  "Alright, properly this time." came back as "or writes properly this": two words not
  hers, so a candidate, and deciding stopped her at once on her own echo. Now a
  candidate only ducks her: the page is sent `overlap.duck`, the echo judged before it
  is forgotten, and the clock starts. `confirm(candidate, after, …)` (pure) decides:
  - **Interrupt at once:** a stop word ("stop", "wait", "no", "hold on" and the like)
    that is not in what she is saying now.
  - **Interrupt on three:** three words that are not hers (acknowledgements and
    fillers aside), before and after the duck together ("what about" then "Tuesday").
    A misheard fragment of her is a word or two.
  - **Interrupt on an ended two:** two such words that the recognizer has ended a turn
    on ("skip that", "go back"), in words less than half hers. The live fragment "or
    writes properly this" is half hers, so it does not.
  - **Carry on (acknowledgement):** words not hers that are all acknowledgements or
    fillers ("um", "uh", "hmm", "erm"), three at most, and either `done` or 1.2 s.
  - **Carry on (not confirmed):** nothing more by `CONFIRM_S` (1 s) after the duck: her
    echo, misheard. Two words of theirs that the recognizer has not ended in that
    second are not heard either, and are said again; that is the margin over her own
    misheard echo.
  - **Wait:** anything else.
  - Her own stop word ("No, the train…") never stops her.
- The old `overlap` (the page ducked her itself) still goes to `decide` as before.
- **On `carry_on`:** `overlap.over` is sent, the words are forgotten and the listener
  goes back to watching. Nothing is saved, and the turn counter does not move.
- **On `interrupt`:** the reply is stopped as `_on_barge` stops it (VOI-15c: approved
  work finishes and is recorded), her saved reply is trimmed to what was heard (§4),
  and the same listener goes on as the user's turn with normal patience
  (`mic.start{follow_up: false}`). The candidate's words and the words that confirmed
  them are kept; her echo before them is not, and her words at the front of the turn
  are taken off as below.
- **Her reply heard to the end** (`playback.ended`) while only watching, after a spoken
  turn: the watch listener becomes the follow-up window (`FOLLOW_UP_S`,
  `mic.start{follow_up: true}`) on the same recognizer connection, but not at once
  (*as built after the talking-to-herself loop*). The recognizer reports her last
  sentence up to a second after `playback.ended`; promoted at once, that echo became
  the user's next turn, and she answered herself in a loop. So the watch goes on
  judging, with her last two sentences added to what she is saying, for
  `ECHO_GRACE_S` (2 s) and until any recognizer turn that began over her has ended.
  Any word of theirs meanwhile (one is enough once she has ended: "Yes.", "Thursday.")
  is the user's turn at once (no duck, she has finished), with her echo judged while
  she spoke left out. Words that are all hers ("Six?" after "…leave at six?") are kept
  too, and left to the turn's own check below. Otherwise the window opens when the
  grace is over, keeping what was heard since she ended. Heard to the end while
  deciding: carrying on does the same.
- **Her last words, after she stops** (every path): when she is heard to the end, or
  stopped by a cut-in, the session keeps what she was last saying (`_her_end`). A
  spoken turn whose first words were said within `ECHO_AFTER_S` (3 s) of that is
  checked by `echo_of`, only on evidence: three or more of her words in her order are
  her echo (in front, taken off; a turn mostly her, all of it); one or two words are
  hers only when they are exactly her final words, said as she stopped (below).
  Anything with a stop word in it is always the user's. A reply that reuses her words
  ("Six?", "Tea." after "Coffee or tea?") is theirs. When a turn is dropped, the usual
  "unheard" notice says so.
- **When words were said** (*after the third review*): a recognizer reports words
  0.5 to 1 s after they were said, so the report time is not enough. Each listener
  notes where voices start in the audio (`Onsets`, by loudness, timed by when the frame
  arrived). Words were said at the last voice start at least the recognizer's lag
  (0.4 s) before they were first reported: the last, not the first, so a click or
  reverb just after her end does not make the user's "Six." look like her echo. Said
  within 0.3 s of her end is "as she stopped". When no start was heard (her echo can
  be too quiet to clear the bar), words first reported within 1.2 s of her end are.
  While a watch is in its grace, words that are all hers and said as she stopped are
  marked as her echo and let go with it. That covers a
  window opened without a watch, a watch promoted by Speak or `barge`, and an
  interruption. No draft starts in that time, and the words shown while listening
  leave her words out too.
- **While the user's words are still being saved** (the recognizer handing over its
  last words after their turn): the watch does not judge. A cut-in then would cancel
  the turn collecting them and lose them; once they are saved, what the user went on
  saying is judged, and stops her as usual.
- **The watch ends** (`overlap.over` with `end: true`) when her turn ends with nothing
  of hers left to hear (a reply only written, an error), when nothing of hers has
  started playing or been sent for 15 s (`WATCH_HOLD_S`), when no audio arrives for 3 s, when the
  recognizer fails, on `overlap.unwatch`, a typed message or new settings (watching or
  deciding); and quietly
  on Stop or a closed session.
- **Other messages while watching:** a `barge` stops her now, and the watch becomes
  the turn; `listen.start` (Speak) over her finished reply does the same, with nothing
  heard before it kept; the page's own `overlap` starts deciding at once.
- **Facts:** listening is true while watching, so notices wait (VOI-17). No draft
  starts while a watch or a decision is open.
- **Logs:** only the phase by name (watching, deciding, carry_on, interrupt,
  follow_up, watch_over), never words.
- **Cost and privacy:** with hearing that judges turns, audio goes to the hearing
  service for as long as she speaks after a spoken turn, not only while the user
  talks. Deepgram bills that audio by the minute. The Settings note says so, and the
  switch turns it off.

**The old `overlap`** (the page ducked her itself) is still accepted, as before: it
opens a listener in overlap mode and decides with `decide`.

- **A word that leads on, alone over her** ("so"): its `done` is held, but deciding
  never waits for the end. "So" is not an acknowledgement, so she stops at once.
- **If her reply finishes while deciding:** `carry_on` simply ends the overlap, or for
  a watch, opens the follow-up window. `interrupt` is an ordinary turn, with nothing
  left to stop.

The acknowledgement list is "yeah", "yes", "yep", "mm-hm", "mhm", "uh-huh", "right", "okay", "ok", "sure", "got it", "i see", "exactly", "totally" and "true". It is matched on whole, normalized words.

### 4. Keeping what was heard (D8)

- **The page reports what was heard.** Each `reply.sentence` already carries its `turn` and `seq`. When a sentence's audio starts playing, the page sends `{type: "playback.sentence", turn_id, seq}`.
- **The speaker** (`api/speech.py`, `Speaker`) remembers the sentences it sent, in order. `heard_text()` returns the sentences whose playback has started.
- **`run_turn` gains `heard: Callable[[str], str] | None`.**
  - When a spoken turn is cancelled, the text it keeps is `heard(text)`: the heard sentences, then "…" and `INTERRUPTED`.
  - With no sentence heard, only the marker is kept, so the history still shows she began.
- **Typed turns and muted voice** keep the full text, as today.
- **Earlier rounds of the same turn** (text before a tool call) are saved as they are now.
- **The page** receives `interrupted` with `kept`, the round's kept text, and replaces the bubble's text with it. With nothing kept, it shows the marker, as a reload does.

**Talked over after it was written:**

- Her last spoken reply, already saved, is trimmed to what was heard when the user cuts in (a barge, an overlap that interrupts, or Stop) while it still plays.
- Only while the model has never been sent it: anything saved after it, or a draft that read it, blocks the trim.
- **Cut in during her last sentence** (*as built after live testing*): every sentence having started playing is not the reply heard to the end. The last one is still playing, so it is kept and marked like any other ("…" and `INTERRUPTED`). Only `playback.ended` means she was heard to the end, and it lets the reply go. A live reply of one short sentence and one long one was otherwise saved whole once its second sentence started.
- The store amends only the conversation's last assistant row (`amend_tail`), the documented exception to append-only (`scripts/check_one_writer.py`).
- The provider's raw turn is dropped with it, so the next request misses the prompt cache once.

## Errors and edge cases

- **The recognizer never says `done`:** `eot_timeout_ms` forces one after 5 s of silence. `too_long` still ends a turn at 120 s.
- **An acknowledgement over her** ("yeah", unpunctuated) does not lead on, so its `done` is not held and she carries on at once.
- **Noise during a held turn:** a breath, a keyboard or a television restarts the 2.5 s of quiet, so a held turn ends 5 s after the recognizer's `done` at most. New words cancel the hold, and the recognizer judges the end again.
- **The Deepgram connection drops mid-turn:** as today, the words heard so far are kept and a draft is cancelled (VOI-14).
- **A draft is running when a notice arrives:** the microphone is open, so the door holds the notice (VOI-17). Nothing changes.
- **Overlap while her reply is only text** (voice muted): there is nothing to duck, and the page does not watch for barge-in (as today).
- **A tap on an approval card during a draft:** taps belong to confirmed turns. A draft holds no card, because cards come from tools and a draft stops before tools.
- **Privacy:** Nothing heard is logged, as today. Draft text and overlap words are never logged; traces record only whether a draft was used or dropped, and overlap phases are logged by name only. With hearing that judges turns and barge-in on, the microphone goes to the hearing service while she speaks after a spoken turn (D9); Settings → Hearing says so.
- **Talking right after a turn ends, before her reply starts:** while the recognizer is still handing over the user's last words, nothing is judged; once they are saved, words that are not hers are a candidate (nothing of hers is playing yet) and stop her reply as usual, becoming the next turn. A barge or Speak in that moment still cancels the turn being collected, as before.
- **Her own words come back after she stops:** for 3 s, three or more of her words in her order are taken off the front of the user's turn, and a turn that is only, or mostly, such a run is dropped with the "unheard" notice. A user who repeats three or more of her words in her order straight after her is not heard, which is the price of never answering herself; short replies that reuse her words are heard.

## Testing

- **`tests/voice/test_stt_contract.py` (Deepgram):** the v2 URL and query; parsing each `TurnInfo` event into `Heard` with the right `turn`; 80 ms chunking. All with a fake socket.
- **`tests/voice/test_listener.py`:**
  - with `turns`, `done` ends listening and pauses don't;
  - `maybe` and `resumed` reach `on_turn`;
  - recognizers without turns behave as before.
- **`tests/api/test_drafts.py`:**
  - a draft used, and its held text reaching the speaker in order;
  - a draft dropped on `resumed`, with nothing saved, sent or spoken;
  - a draft dropped when the final words differ;
  - a draft held at a tool call, dispatched only after the turn is confirmed, with the confirmed turn's number;
  - a failed draft;
  - the switch turned off;
  - usage recorded for a dropped draft that had finished;
  - a draft dropped when the system prompt changed.
- **`tests/voice/test_overlap.py`:** `decide` on acknowledgements, interruptions, echo, silence, the time limits and words in another language; `judge_watch` on her echo (misheard too), her own "No, …", the user's words amid her echo, acknowledgements; `judged_prefix`.
- **`tests/voice/test_listener.py`:** watch mode streams and never ends, one recognizer turn at a time; `forget`; `hear` (a turn already done is done); back to `watch`; a watch as a follow-up window.
- **`tests/api/test_overlap_session.py`:**
  - carry on: the reply is untouched and nothing is saved;
  - interrupt: the reply stops and the same listener becomes the turn;
  - the reply finishing mid-decision;
  - Whisper keeps `barge`.
- **`tests/api/test_overlap_watch.py`:** echo alone changes nothing; an acknowledgement ducks then carries on, reply untouched; the user's words after her echo interrupt, with their first words kept and the echo left out; her reply ending while watching becomes the follow-up window on the same listener; watching with a running turn, with a tail, and before her first sound; a reply that never sounds; the 15 s and no-audio bounds; `overlap.unwatch`; Stop, close, a typed message, new settings; `barge`, `listen.start` and `overlap` while watching; no draft; refusals.
- **`tests/api/test_heard.py`:**
  - an interrupted spoken reply saved to the heard sentence;
  - nothing heard;
  - typed and muted replies unchanged;
  - `kept` sent to the page;
  - a reply already saved, talked over while it plays, trimmed to what was heard, and never once a draft has read it (a draft that never started leaves it trimmable).
- **Web:** ducking and restoring the gain; `overlap` vs `barge` by capability; `playback.sentence` reporting; replacing the bubble with `kept`; watching by streaming or locally (`watchMode`, `watchTitle`); the detector not learning the user's voice under the bar; a two-second pre-roll; the Settings note (`bargeHint`).
- **Live check:** with the user's Deepgram key and audio stubbed so nothing plays aloud:
  - a mid-sentence pause no longer cuts the turn;
  - a draft is used on a clear ending;
  - an acknowledgement over her reply carries on;
  - an interruption is kept to the heard sentence;
  - over speakers, her echo does not stop her, and cutting in keeps the user's first words.

## Not in this spec

- A hands-free, always-open microphone (the next spec).
- Turn detection for local Whisper, with a local smart-turn model or a streaming local recognizer.
- Preparing the first sentence's audio during a draft (option 2), or running read-only tools during one (option 3).
- Acknowledgement lists in other languages.
