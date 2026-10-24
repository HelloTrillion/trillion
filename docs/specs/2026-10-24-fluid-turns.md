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

**The page** (`apps/web/src/main.ts`, `barge.ts`):

- **Detecting:** the existing `BargeDetector` still decides that someone started speaking over her.
- **Hearing that judges turns:**
  - Her playback gain ramps to 0.2 over 80 ms.
  - The page sends `{type: "overlap"}` and then the held pre-roll audio, as `barge` does today.
- **Otherwise:** it sends `barge`, as today.
- **On `overlap.over`:** the gain ramps back to 1, and watching for barge-in continues.

**The session**, on `overlap`:

- It opens a listener in **overlap mode** without stopping her reply. `_listen` accepts this while a turn is running.
- It decides with `voice/overlap.py`, a pure function `decide(words, done, elapsed_s, playing) -> "wait" | "carry_on" | "interrupt"`:
  - **Echo:** words that all appear in the sentence she is playing, or the one before, count as her own voice coming back. They are ignored, except stop words ("stop", "wait", "no", "hold on" and the like), which always count as the user.
  - **Interrupt:** any word not in the acknowledgement list is an interruption, as soon as it is heard.
  - **Carry on (acknowledgement):** words that are all acknowledgements, three at most, and either `done` or 1.2 s passed.
  - **Carry on (no words):** no words by 1.5 s means a cough or a door.
  - **Wait:** anything else, until the 1.5 s limit. At the limit, words decide as above.
- **On `carry_on`:** the overlap listener is abandoned, `overlap.over` is sent and she carries on. Nothing is saved, and the turn counter does not move.
- **On `interrupt`:**
  - The reply is stopped as `_on_barge` stops it today (VOI-15c: approved work finishes and is recorded).
  - The same listener goes on as the user's turn, with normal patience. Its words so far are kept.
- **A word that leads on, alone over her** ("so"): its `done` is held, but deciding never waits for the end. It goes by the words: "so" is not an acknowledgement, so she stops at once, and no acknowledgement leads on.
- **If her reply finishes while deciding:** `carry_on` simply ends the overlap. `interrupt` is an ordinary turn, with nothing left to stop.

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
- **Privacy:** Nothing heard is logged, as today. Draft text and overlap words are never logged; traces record only whether a draft was used or dropped, and overlap decisions are logged as carry_on or interrupt.

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
- **`tests/voice/test_overlap.py`:** `decide` on acknowledgements, interruptions, echo, silence, the time limits and words in another language.
- **`tests/api/test_overlap_session.py`:**
  - carry on: the reply is untouched and nothing is saved;
  - interrupt: the reply stops and the same listener becomes the turn;
  - the reply finishing mid-decision;
  - Whisper keeps `barge`.
- **`tests/api/test_heard.py`:**
  - an interrupted spoken reply saved to the heard sentence;
  - nothing heard;
  - typed and muted replies unchanged;
  - `kept` sent to the page;
  - a reply already saved, talked over while it plays, trimmed to what was heard, and never once a draft has read it (a draft that never started leaves it trimmable).
- **Web:** ducking and restoring the gain; `overlap` vs `barge` by capability; `playback.sentence` reporting; replacing the bubble with `kept`.
- **Live check:** with the user's Deepgram key and audio stubbed so nothing plays aloud:
  - a mid-sentence pause no longer cuts the turn;
  - a draft is used on a clear ending;
  - an acknowledgement over her reply carries on;
  - an interruption is kept to the heard sentence.

## Not in this spec

- A hands-free, always-open microphone (the next spec).
- Turn detection for local Whisper, with a local smart-turn model or a streaming local recognizer.
- Preparing the first sentence's audio during a draft (option 2), or running read-only tools during one (option 3).
- Acknowledgement lists in other languages.
