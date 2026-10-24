# Fluid Turns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `docs/specs/2026-10-24-fluid-turns.md`: pauses don't end a turn, she drafts early, a "yeah" over her lets her carry on, and an interrupted reply is kept up to what was heard.

**Architecture:**
- **Hearing:** a hearing adapter that judges turns (`turns = True`) reports `maybe`, `resumed` and `done` on `Heard`. Only `voice/providers/deepgram.py` knows it is Deepgram Flux.
- **Drafts:** the session starts a `Draft` on `maybe`. A draft runs only the model's first call and records its events. On `done`, `run_turn` replays those events as its first call.
- **Talking over her:** the page ducks her voice and sends `overlap`. The session listens without stopping her, and a pure `decide()` says carry on or interrupt.
- **What was heard:** the page reports each sentence as it starts playing, and an interrupted spoken reply is saved up to that sentence.

**Tech stack:**
- Python 3.13 with uv, FastAPI, pytest-asyncio, ruff and strict mypy.
- TypeScript, Vite and vitest in `apps/web`.

**Repo rules (apply to every task):**
- **Commits:** every commit is signed off (`git commit -s`) and ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Names:**
  - No real people's or businesses' names, in code or tests (`scripts/containment_check.py`).
  - The agent's name is never hard-coded (`scripts/check_agent_name.py`).
  - No raw hex colours outside `packages/galaxy/tokens.css` (`scripts/check_raw_hex.py`).
- **Commenting style:** short plain-English comments that say *why*. Requirement IDs go in docstrings, e.g. `(VOI-12)`.
- **Privacy:**
  - Nothing heard, and no draft text, is ever logged.
  - Logs carry kinds and counts only.
- **Gate commands:**
  ```bash
  uv run ruff check . && uv run ruff format --check . && uv run mypy
  uv run pytest -q tests/unit tests/integration tests/brain tests/api tests/voice tests/memory tests/mcp tests/skills tests/scheduler tests/delivery tests/spine
  pnpm -r typecheck && pnpm -r test
  uv run python scripts/check_raw_hex.py && uv run python scripts/check_agent_name.py && uv run python scripts/check_one_writer.py && uv run python scripts/containment_check.py
  ```

## File map

| File | Change |
|---|---|
| `src/trillion/voice/types.py` | `Heard.turn`, `STTProvider.turns` |
| `src/trillion/voice/endpoint.py` | `EndOfSpeech(judged=...)`, `turn_over()` |
| `src/trillion/api/listening.py` | `Listener(on_turn=...)`, a `done` verdict, `describe()` adds `turns` |
| `src/trillion/voice/providers/deepgram.py` | Flux on `/v2/listen`, `TurnInfo` parsing, 80 ms chunks |
| `src/trillion/voice/providers/whisper.py`, `openai_compat.py` (STT class), `system.py` if it has an STT | `turns = False` |
| `src/trillion/voice/catalog.py` | `ListenOption.turns` |
| `src/trillion/api/runtime.py` | `think_early` preference |
| `src/trillion/api/settings.py` | `turns` and `think_early` in `describe_listening`; `PUT /api/listening/think-early` |
| `src/trillion/api/draft.py` | **new**: `Draft`, `same_words()` |
| `src/trillion/brain/loop.py` | `run_turn(first=..., heard=...)` |
| `src/trillion/voice/overlap.py` | **new**: `decide()` and the acknowledgement list |
| `src/trillion/api/speech.py` | `Speaker` remembers sentences and what was heard |
| `src/trillion/api/session.py` | input build/commit split, drafts, overlap, `playback.sentence`, `kept` |
| `apps/web/src/voice.ts` | `duck()`, `onSentence` hook, `sentence(turn, seq, text)` |
| `apps/web/src/main.ts` | `overlap` vs `barge`, `overlap.over`, `playback.sentence` |
| `apps/web/src/view.ts` | `interrupted.kept` replaces the bubble text |
| `apps/web/src/listening.ts` | the "Start thinking before I finish" switch |
| `tests/voice/fakes.py` | `FakeSTT(turns=...)`, `FakeStream.say(..., turn=...)` |
| Docs | PRD VOI-12/VOI-9 status, README voice section, `docs/architecture.md` |

---

### Task 1: Turn signals in the hearing types and the listener

**Files:**
- Modify: `src/trillion/voice/types.py` (`Heard`, `STTProvider`)
- Modify: `src/trillion/voice/endpoint.py` (`EndOfSpeech`)
- Modify: `src/trillion/api/listening.py` (`Listener`)
- Modify: `tests/voice/fakes.py`
- Test: `tests/voice/test_endpoint.py`, `tests/voice/test_listener.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/voice/test_endpoint.py`:

```python
def test_a_recognizer_that_judges_turns_decides_the_end() -> None:
    """VOI-12: with `judged`, silence never ends the turn; the recognizer's done does."""
    rules = EndOfSpeech(streaming=True, judged=True)
    assert rules.audio(tone(0.6)) is None
    rules.heard(final=False, has_text=True)
    # Three seconds of quiet: the old rules would have ended it at one.
    assert rules.audio(room(3.0)) is None
    rules.turn_over()
    assert rules.audio(room(0.04)) == "end"


def test_judged_rules_still_say_nothing_and_inaudible() -> None:
    assert EndOfSpeech(streaming=True, judged=True, patience_s=1.0).audio(room(1.2)) == "nothing"
    assert EndOfSpeech(streaming=True, judged=True).audio(bytes(16_000 * 2 * 3)) == "inaudible"
```

(`tone` and `room` come from `tests.voice.audio`. Add them to the file's imports if they aren't there.)

Append to `tests/voice/test_listener.py`:

```python
async def test_a_judging_recognizer_ends_the_turn_on_done_and_reports_maybe() -> None:
    stt = FakeSTT(turns=True)
    seen: list[tuple[str, str]] = []

    async def on_turn(kind: str, text: str) -> None:
        seen.append((kind, text))

    async def nothing(_: str) -> None:
        return None

    listener = Listener(stt, "en", on_partial=nothing, on_turn=on_turn)
    await listener.start()
    assert await listener.audio(tone(0.6)) is None
    stt.stream.say("I need to remember to", final=False, turn="maybe")
    await asyncio.sleep(0)
    stt.stream.say("I need to remember to", final=False, turn="resumed")
    await asyncio.sleep(0)
    assert await listener.audio(room(2.5)) is None  # a pause, not the end
    stt.stream.say("I need to remember to call the dentist", final=True, turn="done")
    await asyncio.sleep(0)
    assert await listener.audio(room(0.04)) == "end"
    assert seen == [("maybe", "I need to remember to"), ("resumed", "I need to remember to")]
    said = await listener.end()
    assert said.text == "I need to remember to call the dentist"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest -q tests/voice/test_endpoint.py tests/voice/test_listener.py`
Expected: FAIL. `EndOfSpeech` has no `judged` argument, and `FakeSTT` has no `turns`.

- [ ] **Step 3: Implement**

`src/trillion/voice/types.py`: add the field to `Heard`, below `pause`.

```python
    # From a recognizer that judges turns itself (VOI-12): "maybe" (probably finished),
    # "resumed" (they carried on after a maybe) or "done" (over; `text` is the whole
    # turn). None from recognizers that leave the decision to the listener.
    turn: Literal["maybe", "resumed", "done"] | None = None
```

Import `Literal` from `typing`. In `STTProvider`, under `streaming`, add:

```python
    # True when the recognizer itself decides that the user has finished (VOI-12).
    turns: bool
```

Give every STT provider class a `turns = False` class attribute, next to `streaming`. Find them with `grep -rn "streaming = " src/trillion/voice/providers`. Deepgram becomes `True` in Task 2.

`src/trillion/voice/endpoint.py`: change the constructor to

```python
    def __init__(
        self, *, streaming: bool, patience_s: float = NOTHING_AFTER_S, judged: bool = False
    ) -> None:
```

In it, set `self._judged = judged` and `self._over = False`. Add the method:

```python
    def turn_over(self) -> None:
        """The recognizer that judges turns says this one is over (VOI-12)."""
        self._over = True
```

In `_window`, right after the `if not self._spoke:` block, add:

```python
        if self._judged:
            # The recognizer judges the end by meaning; silence alone never does.
            return "end" if self._over else None
```

`src/trillion/api/listening.py`:
- **`describe()`** returns `{"kind": kind, "choice": choice, "streaming": provider.streaming, "turns": provider.turns}`.
- **Constructor:** `Listener.__init__` gains `on_turn: Callable[[str, str], Awaitable[None]] | None = None`, stored as `self._on_turn`. The rules become `EndOfSpeech(streaming=provider.streaming, patience_s=patience_s, judged=provider.turns)`.
- **`_read`:** replace the loop body with:

```python
            async for heard in stream.events():
                if heard.turn == "done":
                    # The whole turn, as the recognizer finally heard it.
                    self._finals = [heard.text] if heard.text else self._finals
                    self._interim = ""
                    self._rules.heard(final=True, has_text=bool(heard.text))
                    self._rules.turn_over()
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
                if heard.turn in ("maybe", "resumed") and self._on_turn is not None:
                    await self._on_turn(heard.turn, self.text())
                if self._provider.streaming and (heard.text or heard.final):
                    await self._on_partial(self.text())
```

- Add a public method:

```python
    def text_now(self) -> str:
        """What has been heard so far, for deciding while she talks (VOI-9)."""
        return self.text()
```

`tests/voice/fakes.py`:
- `FakeSTT.__init__` gains `turns: bool = False` and stores `self.turns = turns`.
- `FakeStream.say` becomes:

```python
    def say(
        self, text: str, *, final: bool = True, pause: bool = False,
        turn: Literal["maybe", "resumed", "done"] | None = None,
    ) -> None:
        self._events.put_nowait(Heard(text=text, final=final, pause=pause, turn=turn))
```

  Import `Literal`.

- [ ] **Step 4: Run the tests and the whole voice and api suites**

Run: `uv run pytest -q tests/voice tests/api`
Expected: PASS. Recognizers without `turns` behave exactly as before.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/voice src/trillion/api/listening.py tests/voice
git commit -s -m "Hearing: a recognizer can judge turns itself (VOI-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Deepgram on Flux

**Files:**
- Modify: `src/trillion/voice/providers/deepgram.py`
- Modify: `src/trillion/voice/catalog.py`
- Test: `tests/voice/test_stt_contract.py`

- [ ] **Step 1: Update the contract test's fake socket and URL test**

In `tests/voice/test_stt_contract.py`, replace `results()` with a TurnInfo builder, and make `FakeSocket` speak Flux:

```python
def turn_info(event: str, text: str) -> str:
    return json.dumps({"type": "TurnInfo", "event": event, "turn_index": 0,
                       "transcript": text, "words": [], "end_of_turn_confidence": 0.9})  # fmt: skip
```

In `FakeSocket.send`:
- The first audio frame queues `turn_info("Update", "write a note")`.
- `CloseStream` queues `json.dumps({"type": "Connected"})`, then `turn_info("EndOfTurn", SAID)`, then `None`.

Replace `test_deepgram_asks_for_what_the_page_sends`'s URL checks:

```python
    url = fake.urls[0]
    assert url.startswith("wss://api.deepgram.com/v2/listen?")
    for part in ("model=flux-general-multi", "encoding=linear16", "sample_rate=16000",
                 "eot_threshold=0.7", "eager_eot_threshold=0.5", "eot_timeout_ms=5000"):  # fmt: skip
        assert part in url
    assert "language=" not in url and "interim_results" not in url
```

Keep the rest of that test (the key in a header, the bytes sent, the final `CloseStream`, the unknown-language fallback). Then add:

```python
def test_deepgram_turn_events_become_turn_signals() -> None:
    assert deepgram.parse(turn_info("Update", "so I")) == Heard(text="so I", final=False)
    assert deepgram.parse(turn_info("EagerEndOfTurn", "so I said")) == Heard(
        text="so I said", final=False, turn="maybe")
    assert deepgram.parse(turn_info("TurnResumed", "so I said")) == Heard(
        text="so I said", final=False, turn="resumed")
    assert deepgram.parse(turn_info("EndOfTurn", "so I said no")) == Heard(
        text="so I said no", final=True, turn="done")
    assert deepgram.parse(turn_info("StartOfTurn", "")) is None
    assert deepgram.parse(json.dumps({"type": "Connected"})) is None


async def test_deepgram_sends_audio_in_80ms_chunks(fake: Fake) -> None:
    await listen(_deepgram(fake), "en")
    chunks = [s for s in fake.socket.sent if isinstance(s, bytes)]
    assert chunks and all(len(c) == 2560 for c in chunks[:-1]) and len(chunks[-1]) <= 2560
```

Import `Heard` from `trillion.voice.types`. Update `test_deepgram_unreachable_and_garbled`: change `results("", final=False)` to `turn_info("Update", "")`. That still parses to `None`, because there is no text and it isn't final.

- [ ] **Step 2: Run and confirm it fails**

Run: `uv run pytest -q tests/voice/test_stt_contract.py -k deepgram`
Expected: FAIL, because the URL is still `v1` with `nova-3`.

- [ ] **Step 3: Implement in `deepgram.py`**

```python
LISTEN_URL = "wss://api.deepgram.com/v2/listen"
# Flux: Deepgram's recognizer that judges the end of a turn by meaning (VOI-12).
MODELS = {"en": "flux-general-en", "multi": "flux-general-multi"}
# Probably finished at 0.5, finished at 0.7, and never more than five seconds of quiet.
EOT_THRESHOLD = 0.7
EAGER_EOT_THRESHOLD = 0.5
EOT_TIMEOUT_MS = 5000
# Deepgram's recommended chunk: 80 ms of 16 kHz PCM16.
CHUNK_BYTES = 2560
```

Delete `MODEL`. Change `listen_url`:

```python
def listen_url(language: str) -> str:
    query = {
        "model": MODELS.get(language, MODELS["en"]),
        "encoding": "linear16",
        "sample_rate": CAPTURE.sample_rate,
        "eot_threshold": EOT_THRESHOLD,
        "eager_eot_threshold": EAGER_EOT_THRESHOLD,
        "eot_timeout_ms": EOT_TIMEOUT_MS,
    }
    return f"{LISTEN_URL}?{urlencode(query)}"
```

The existing check that an unknown choice falls back to the first must still hold. `open()` already validates the choice against `CHOICES`; leave that as it is.

Replace `parse`:

```python
_EVENTS: dict[str, Literal["maybe", "resumed", "done"] | None] = {
    "Update": None, "StartOfTurn": None,
    "EagerEndOfTurn": "maybe", "TurnResumed": "resumed", "EndOfTurn": "done",
}  # fmt: skip


def parse(raw: bytes | str) -> Heard | None:
    """One message from Deepgram as words and a turn signal, or None when it carries none."""
    try:
        msg = json.loads(raw)
    except ValueError as e:
        raise VoiceError("bad_response", type(e).__name__) from e
    if not isinstance(msg, dict) or msg.get("type") != "TurnInfo":
        return None
    event = msg.get("event")
    if event not in _EVENTS:
        return None
    text = str(msg.get("transcript") or "").strip()
    turn = _EVENTS[event]
    if turn is None and not text:
        return None
    return Heard(text=text, final=turn == "done", turn=turn)
```

In `_Stream.send`, send `pcm` in `CHUNK_BYTES` pieces, carrying any remainder over to the next call:

```python
    async def send(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        self._rest += pcm
        try:
            while len(self._rest) >= CHUNK_BYTES:
                piece, self._rest = self._rest[:CHUNK_BYTES], self._rest[CHUNK_BYTES:]
                await self._socket.send(piece)
        except ConnectionClosed as e:
            raise VoiceError("unreachable", type(e).__name__) from e
```

Initialise `self._rest = b""` in `__init__`. In `finish()`, send any remainder before `CloseStream`:

```python
        try:
            if self._rest:
                await self._socket.send(self._rest)
                self._rest = b""
            await self._socket.send(json.dumps({"type": "CloseStream"}))
```

On the `Deepgram` class: `turns = True`. Update the module docstring to say it uses Flux, which also judges when a turn is over.

`src/trillion/voice/catalog.py`:
- `ListenOption` gains `turns: bool = False`. Put it after `install_hint`, so keyword construction still works.
- The Deepgram entry sets `turns=True`.

- [ ] **Step 4: Run the voice suite**

Run: `uv run pytest -q tests/voice tests/api`
Expected: PASS. Fix any other test that asserted `nova-3`: `grep -rn "nova-3" tests src docs`.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/voice tests/voice
git commit -s -m "Deepgram: Flux, which judges when a turn is over (VOI-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The "Start thinking before I finish" setting

**Files:**
- Modify: `src/trillion/api/runtime.py`
- Modify: `src/trillion/api/settings.py`
- Modify: `apps/web/src/listening.ts`
- Test: `tests/api/test_settings_listening.py`. If listening settings tests live elsewhere, find them with `grep -rln "/api/listening" tests/api` and add there.
- Test: `apps/web/test/listening.test.ts` if it builds the panel. Otherwise the server test is enough.

- [ ] **Step 1: Failing API test**

```python
async def test_thinking_early_is_on_by_default_and_can_be_turned_off(client, rt) -> None:
    got = (await client.get("/api/listening")).json()
    assert got["think_early"] is True
    assert {o["kind"]: o["turns"] for o in got["options"]}["deepgram"] is True
    r = await client.put("/api/listening/think-early", json={"on": False})
    assert r.status_code == 200 and r.json()["think_early"] is False
    assert rt.think_early is False
    assert (await client.put("/api/listening/think-early", json={"on": "yes"})).status_code == 422
```

Use the same `client` and `rt` fixtures that the file's other listening tests use.

- [ ] **Step 2: Run it, expect FAIL** (no `think_early`).

- [ ] **Step 3: Implement**

`runtime.py`:
- Add `THINK_EARLY_PREF = "think_early"` beside `LISTEN_PREF`.
- Add a `Runtime` field `think_early: bool = True`.
- In `open_runtime`, read it: `think_early=await preferences.get(OWNER, THINK_EARLY_PREF) is not False`.

`settings.py`:

```python
class ThinkEarly(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: StrictBool
```

(import `StrictBool` from pydantic). In `describe_listening`, add `"turns": o.turns` to each option and `"think_early": rt.think_early` to the result. Add the route:

```python
    @router.put("/listening/think-early")
    async def save_think_early(body: ThinkEarly) -> Any:
        """Whether she starts a draft while the user may still be talking (VOI-12)."""
        rt.think_early = body.on
        await rt.preferences.put(OWNER, THINK_EARLY_PREF, body.on, rt.clock())
        return describe_listening()
```

`apps/web/src/listening.ts`:
- `Option` gains `readonly turns: boolean`, and `State` gains `readonly think_early: boolean`.
- After `pickRow`, build the row:

```ts
  const earlyRow = el('label', 'settings-check');
  const early = el('input');
  early.type = 'checkbox';
  early.id = 'listening-early';
  earlyRow.append(early, el('span', '', 'Start thinking before I finish'));
  const earlyHint = el('p', 'settings-hint', 'Faster replies. Some drafts are thrown away, which uses more of your model.');
  early.addEventListener('change', async () => {
    const res = await api('PUT', '/api/listening/think-early', { on: early.checked });
    if (res.ok) state = (await res.json()) as State;
    else early.checked = !early.checked;
  });
```

- Insert `earlyRow, earlyHint` into `form.append(...)` after `pickRow`.
- In the function that shows and hides rows for the chosen kind, set `early.checked = state?.think_early ?? true` and `earlyRow.hidden = earlyHint.hidden = !option()?.turns`. Search the file for where `keyRow.hidden` is set, and do it there.

The class `settings-check` may not exist yet: `grep -n "settings-check" apps/web/src/app.css`. If it doesn't, reuse the class the barge-in checkbox in `microphone.ts` uses.

`api` is the existing `Api` helper. Copy the exact call shape from the file's save code, which may be `api.put(...)` or `api('PUT', ...)`.

- [ ] **Step 4: Run** `uv run pytest -q tests/api` and `pnpm -r typecheck && pnpm -r test`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/api apps/web/src tests/api
git commit -s -m "Hearing: a switch for thinking before the user finishes (VOI-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Building a user message apart from saving it

A pure refactor: the draft in Task 6 needs the exact message a confirmed turn would save, without saving it.

**Files:**
- Modify: `src/trillion/api/session.py` (`_save_input`, around line 860)
- Test: `tests/api/test_session.py` (existing tests must still pass) plus one new test

- [ ] **Step 1: Failing test** (append to `tests/api/test_session.py`):

```python
async def test_a_built_message_is_saved_byte_for_byte(rt: Runtime) -> None:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    built = await session._build_input("user", "hello there")
    assert session._history == []  # building saves nothing
    await session._commit_input("user", built)
    assert session._history[-1] == built.msg
    await session.close()
```

- [ ] **Step 2: Run, expect FAIL** (`_build_input` missing).

- [ ] **Step 3: Implement**

Add near the top of `session.py`:

```python
@dataclass(frozen=True, kw_only=True)
class BuiltInput:
    """A user input made ready to save: the message, and what committing it records."""

    msg: Msg
    text: str
    brought: frozenset[str]
```

Replace `_save_input` and `_remembered`:

```python
    async def _build_input(self, kind: Kind, text: str) -> BuiltInput:
        """The message a user input is saved as, built but not saved. A draft (VOI-12)
        uses this, so a used draft saves exactly what it was given."""
        pending = [
            PendingNote(pending_id=a.id, summary=a.summary) for a in await self._open_actions()
        ]
        spoken = kind == "user" and self._via == "voice"
        # The user's own clock, so "at four" means four where they are (AGT-9).
        zone = self._rt.zone()
        recalled, brought = self._remembered(text) if kind == "user" else ("", frozenset())
        context = volatile_context(
            datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone(),
            pending,
            recalled,
            # Said aloud: she is told it is a transcript, and that she will be heard (#40).
            spoken=self._rt.identity if spoken else None,
            zone=zone or "",
        )
        blocks = (ContextBlock(text=context), TextBlock(text=text))
        return BuiltInput(msg=Msg(role="user", blocks=blocks), text=text, brought=brought)

    async def _commit_input(self, kind: Kind, built: BuiltInput) -> None:
        """Save a built input. History is only ever appended to."""
        self._brought.update(built.brought)
        if kind == "user" and self._rt.web is not None:
            # Only what the user said themselves: never a tap's note, never a page.
            self._rt.web.ledger.said(self.id, built.text)
        await self._save(kind, built.msg)

    async def _save_input(self, kind: Kind, text: str) -> None:
        """A user input, with the context it was said in. Both are saved together and
        replayed unchanged on every later turn: history is only ever appended to."""
        await self._commit_input(kind, await self._build_input(kind, text))

    def _remembered(self, said: str) -> tuple[str, frozenset[str]]:
        """What she has saved that bears on this message, as untrusted text (MEM-5),
        and the ids it used. Nothing is marked as brought until the message is saved."""
        rt = self._rt
        fresh = [m for m in rt.memory.recall(said) if m.id not in self._brought]
        text, used = present.alongside(fresh, rt.clock())
        return (wrap("memory_recall", text) if used else "", frozenset(m.id for m in used))
```

Keep the original docstring text on `_remembered` about byte-for-byte replay (MEM-6b) at the end of the new docstring. Check whether `m.id` is a `str`: `grep -n "class Memory\b\|id:" src/trillion/memory/*.py`. Adjust the `frozenset` type if it isn't. Make sure `dataclass` is imported.

- [ ] **Step 4: Run** `uv run pytest -q tests/api tests/memory && uv run mypy`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/api/session.py tests/api/test_session.py
git commit -s -m "Session: a user message can be built before it is saved (VOI-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `run_turn` can take its first call from elsewhere

**Files:**
- Modify: `src/trillion/brain/loop.py` (`run_turn`)
- Test: `tests/brain/test_loop.py`. Find the loop tests with `grep -ln "run_turn" tests/brain`.

- [ ] **Step 1: Failing test**

```python
async def test_a_first_call_given_to_the_turn_is_used_instead_of_calling_the_model() -> None:
    provider = ScriptedProvider(say("From the model."))

    async def drafted() -> AsyncIterator[ModelEvent]:
        for ev in use("note_read", {}, text="Let me look. "):
            yield ev

    # Same helpers the file's other run_turn tests use for router/registry/ctx/emit/save.
    events, saved = [], []
    done = await run_turn(
        provider=provider, router=ROUTER, registry=REGISTRY, system=[], history=[USER_MSG],
        ctx=CTX, emit=lambda ev: _append(events, ev), save=lambda m: _append(saved, m),
        first=drafted(),
    )
    # The drafted first call was used: one model call, for the round after the tool.
    assert len(provider.calls) == 1
    assert done.stop == "end"
    assert [type(e).__name__ for e in events][:1] == ["ReplyDelta"]
```

Adapt `ROUTER`, `REGISTRY`, `CTX`, `USER_MSG` and `_append` to the fixtures the file already uses for a turn with one tool call. Copy the setup of the existing test whose name mentions a tool round trip, so `note_read` (or whatever READ tool it registers) is known.

- [ ] **Step 2: Run, expect FAIL** (`unexpected keyword argument 'first'`).

- [ ] **Step 3: Implement**

Add the parameter `first: AsyncIterator[ModelEvent] | None = None` to `run_turn`, with this docstring line: "`first`, when given, stands in for the first model call: a draft started while the user was finishing (VOI-12)." In the loop, replace the stream source:

```python
        source = first if first is not None else provider.stream(
            system=system, messages=messages, tools=tools, max_tokens=MAX_TOKENS
        )
        first = None
        try:
            async for ev in source:
```

Import `ModelEvent` and `AsyncIterator` if they aren't already.

- [ ] **Step 4: Run** `uv run pytest -q tests/brain && uv run mypy`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/brain/loop.py tests/brain
git commit -s -m "Brain: a turn's first call can be one already under way (VOI-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The `Draft`

**Files:**
- Create: `src/trillion/api/draft.py`
- Test: `tests/api/test_draft.py`

- [ ] **Step 1: Failing tests**

```python
"""A draft (VOI-12): the model's first call, started before the user has finished."""

from __future__ import annotations

import asyncio

from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.draft import Draft, same_words
from trillion.brain.types import Done, Msg, TextBlock, TextDelta, ToolUse


def _msg(text: str) -> Msg:
    return Msg(role="user", blocks=(TextBlock(text=text),))


def test_same_words_ignores_case_spacing_and_punctuation() -> None:
    assert same_words("Call the dentist.", "call  the dentist")
    assert not same_words("call the dentist", "call the dentist tomorrow")


async def _draft(provider: ScriptedProvider) -> Draft:
    return Draft.start(provider=provider, system=[], messages=[_msg("hi")], tools=[],
                       words="hi", built=None, history_len=0, summary=None)


async def test_a_draft_replays_what_it_recorded_then_what_is_still_coming() -> None:
    d = await _draft(ScriptedProvider(say("Hello there.")))
    await asyncio.sleep(0)
    got = [ev async for ev in d.replay()]
    assert [type(e) for e in got] == [TextDelta, Done]
    assert d.usage().output_tokens > 0


async def test_a_draft_that_reaches_a_tool_call_ends_there_without_running_it() -> None:
    d = await _draft(ScriptedProvider(use("note_read", {})))
    got = [ev async for ev in d.replay()]
    assert isinstance(got[0], ToolUse) and isinstance(got[-1], Done)


async def test_a_cancelled_draft_stops_and_keeps_its_usage_so_far() -> None:
    gate = asyncio.Event()

    class Slow(ScriptedProvider):
        async def stream(self, **kw):  # type: ignore[no-untyped-def]
            yield TextDelta(text="Half")
            await gate.wait()

    d = await _draft(Slow())
    await asyncio.sleep(0)
    await d.cancel()
    assert d.cancelled


async def test_a_failed_draft_says_so() -> None:
    from trillion.brain.types import ProviderError

    d = await _draft(ScriptedProvider(ProviderError("unreachable")))
    await d.settled()
    assert d.failed
```

Check the import paths for `ProviderError`, `Done`, `Msg` and the others with `grep -n "^class ProviderError\|^class Done\|^class Msg" -r src/trillion/brain`.

- [ ] **Step 2: Run, expect FAIL** (no module).

- [ ] **Step 3: Implement `src/trillion/api/draft.py`**

```python
"""A draft: her first model call, started while the user is probably finishing (VOI-12).

It only records what the model sends. Nothing it receives reaches the page, the
speaker, the history or a tool: a used draft is replayed into a confirmed turn,
which does all of that as usual, and a dropped one is simply let go.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from trillion.brain.loop import MAX_TOKENS
from trillion.brain.types import Done, ModelEvent, ModelProvider, Msg, ProviderError, SystemBlock, ToolSchema, Usage

_NOT_WORDS = re.compile(r"[^\w\s]")


def same_words(a: str, b: str) -> bool:
    """The same words, whatever the case, spacing or punctuation."""

    def norm(s: str) -> list[str]:
        return _NOT_WORDS.sub("", s.lower()).split()

    return norm(a) == norm(b)


@dataclass(kw_only=True)
class Draft:
    words: str
    # The message the confirmed turn will save (session.BuiltInput); typed loosely here
    # so this module does not import the session.
    built: Any
    # What it was built against: if either changed, it is not used.
    history_len: int
    summary: Any
    _events: list[ModelEvent] = field(default_factory=list)
    _more: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = None
    failed: bool = False
    cancelled: bool = False
    finished: bool = False

    @classmethod
    def start(
        cls,
        *,
        provider: ModelProvider,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        words: str,
        built: Any,
        history_len: int,
        summary: Any,
    ) -> Draft:
        d = cls(words=words, built=built, history_len=history_len, summary=summary)
        d._task = asyncio.create_task(d._run(provider, system, messages, tools))
        return d

    async def _run(self, provider: ModelProvider, system: Sequence[SystemBlock],
                   messages: Sequence[Msg], tools: Sequence[ToolSchema]) -> None:
        try:
            async for ev in provider.stream(
                system=system, messages=messages, tools=tools, max_tokens=MAX_TOKENS
            ):
                self._events.append(ev)
                self._more.set()
        except ProviderError:
            self.failed = True
        finally:
            self.finished = True
            self._more.set()

    async def replay(self) -> AsyncIterator[ModelEvent]:
        """Everything recorded, then the rest as it arrives. For the confirmed turn."""
        i = 0
        while True:
            while i < len(self._events):
                yield self._events[i]
                i += 1
            if self.finished:
                if self.failed:
                    raise ProviderError("provider_error")
                return
            self._more.clear()
            if i < len(self._events) or self.finished:
                continue
            await self._more.wait()

    async def settled(self) -> None:
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def cancel(self) -> None:
        self.cancelled = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        await self.settled()

    def usage(self) -> Usage:
        """What the model has reported spending so far."""
        total = Usage()
        for ev in self._events:
            if isinstance(ev, Done):
                total = total + ev.usage
        return total
```

Adjust the imports to where these names actually live: `MAX_TOKENS` is in `brain/loop.py`, and the types may be split between `brain/types.py` and `brain/providers`. `ProviderError`'s constructor takes `(kind, detail?)`; match it. Keep lines within ruff's length limit; wrap the long import.

- [ ] **Step 4: Run** `uv run pytest -q tests/api/test_draft.py && uv run mypy`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/api/draft.py tests/api/test_draft.py
git commit -s -m "Drafts: her first call, recorded and held until the turn is real (VOI-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Drafts in the session

**Files:**
- Modify: `src/trillion/api/session.py` (`_listen`, `_heard`, `_run`, `_one_turn`, `close`, `interrupt`)
- Modify: `src/trillion/api/trace.py` if `Trace` needs a `draft` field (`grep -n "class Trace" -A30 src/trillion`)
- Test: `tests/api/test_drafts.py`

- [ ] **Step 1: Failing tests** (`tests/api/test_drafts.py`):

```python
"""Early thinking (VOI-12): a draft started on "probably done", used or dropped."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.api.test_listening import speak
from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import say, use
from tests.voice.audio import room, tone
from tests.voice.fakes import FakeSTT
from trillion.api.runtime import ListenSettings, Runtime, open_runtime
from trillion.api.session import Session
from trillion.secrets.store import MemoryStore


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={"TZ": "UTC"}
    )
    yield runtime
    await runtime.close()


def judging(rt: Runtime, *replies) -> tuple[FakeSTT, Gated]:  # type: ignore[no-untyped-def]
    stt = FakeSTT(turns=True)
    rt.stt = stt
    rt.listening = ListenSettings(kind="deepgram", choice="en")
    provider = Gated(*replies)
    rt.provider = provider
    return stt, provider


async def start(rt: Runtime) -> tuple[Session, Page]:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    return session, page


async def test_a_draft_that_matches_is_used_and_the_model_is_called_once(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Sure, the dentist."))
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    assert session._draft is not None and len(provider.calls) == 1
    assert not page.all("reply.delta") and session._history == []  # held, unsaved
    stt.stream.say("Call the dentist.", final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)
    assert len(provider.calls) == 1  # the draft was the turn's first call
    assert page.all("reply.delta")[0]["text"] == "Sure, the dentist."
    assert [k for k, _ in await rt.conversations.history(session.id, "local")] == ["user", "assistant"]
    await session.close()


async def test_carrying_on_drops_the_draft_and_nothing_is_saved_or_sent(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Dropped."), say("The real answer."))
    session, page = await start(rt)
    stt.stream.say("I need to remember to", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    stt.stream.say("I need to remember to", final=False, turn="resumed")
    await asyncio.sleep(0.01)
    assert session._draft is None
    assert not page.all("reply.delta") and session._history == []
    stt.stream.say("I need to remember to call the dentist", final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)
    assert len(provider.calls) == 2
    assert page.all("reply.delta")[0]["text"] == "The real answer."
    await session.close()


async def test_different_final_words_drop_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Dropped."), say("Fresh."))
    session, page = await start(rt)
    stt.stream.say("call the", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    stt.stream.say("call the dentist", final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)
    assert [m["text"] for m in page.all("reply.delta")] == ["Fresh."]
    await session.close()


async def test_a_drafted_tool_call_runs_only_in_the_confirmed_turn(rt: Runtime) -> None:
    stt, provider = judging(rt, use("schedule_list", {}), say("Nothing set."))
    session, page = await start(rt)
    stt.stream.say("what reminders do I have", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    assert not page.all("tool.start")  # held, not run
    stt.stream.say("What reminders do I have?", final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)
    starts = page.all("tool.start")
    assert [s["name"] for s in starts] == ["schedule_list"]
    assert session._turn == 1
    await session.close()


async def test_with_the_switch_off_no_draft_starts(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Answer."))
    rt.think_early = False
    session, page = await start(rt)
    stt.stream.say("hello", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    assert session._draft is None and provider.calls == []
    await session.close()


async def test_a_failed_draft_is_dropped_quietly(rt: Runtime) -> None:
    from trillion.brain.types import ProviderError

    stt, provider = judging(rt, ProviderError("unreachable"), say("Recovered."))  # type: ignore[arg-type]
    session, page = await start(rt)
    stt.stream.say("hello", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    stt.stream.say("hello", final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)
    assert not page.all("error")
    assert [m["text"] for m in page.all("reply.delta")] == ["Recovered."]
    await session.close()


async def test_a_dropped_drafts_usage_is_recorded(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Dropped."), say("Kept."))
    session, page = await start(rt)
    stt.stream.say("one", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    stt.stream.say("one", final=False, turn="resumed")
    await asyncio.sleep(0.01)
    stt.stream.say("one two", final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)
    totals = await rt.conversations.usage_totals("local")  # adapt to the real usage reader
    assert totals.calls == 2  # adapt: whatever counts recorded usage rows
    await session.close()
```

`ScriptedProvider` replies may be exceptions, so `ProviderError` works as a reply. For the last test, use whatever `tests/api/test_usage*.py` uses to read recorded usage, and assert two rows or two calls.

- [ ] **Step 2: Run, expect FAIL** (`_draft` missing).

- [ ] **Step 3: Implement in `session.py`**

1. **State.** In `__init__`: `self._draft: Draft | None = None`. Import `Draft` and `same_words` from `trillion.api.draft`, and `tool_schemas` from `trillion.brain.loop`.

2. **Giving the listener the callback.** In `_listen`, pass `on_turn=self._turn_signal` to `Listener(...)`.

3. **The callback:**

```python
    async def _turn_signal(self, kind: str, text: str) -> None:
        """The recognizer thinks they have probably finished, or that they carried on
        (VOI-12). A draft starts on the first and is dropped on the second."""
        if kind == "resumed":
            await self._drop_draft()
        elif kind == "maybe":
            await self._drop_draft()
            await self._start_draft(text)

    async def _start_draft(self, words: str) -> None:
        rt = self._rt
        provider = rt.provider
        words = words[:MAX_TEXT].strip()
        if (not rt.think_early or provider is None or not words or self._task is not None
                or self._queued or self._overlap is not None):
            return
        self._via = "voice"
        built = await self._build_input("user", words)
        history = [*self._history, built.msg]
        # A history about to be summarized would change what the turn sends.
        if compact.estimate(history) > rt.compact_at:
            return
        system = await asyncio.to_thread(rt.system)
        self._draft = Draft.start(
            provider=provider,
            system=system,
            messages=compact.with_summary(self._summary, history),
            tools=tool_schemas(rt.registry),
            words=words,
            built=built,
            history_len=len(self._history),
            summary=self._summary,
        )

    async def _drop_draft(self) -> None:
        draft, self._draft = self._draft, None
        if draft is None:
            return
        await draft.cancel()
        await self._record_draft_usage(draft)

    async def _record_draft_usage(self, draft: Draft) -> None:
        provider = self._rt.provider
        usage = draft.usage()
        if provider is None or usage == Usage():
            return
        pricing = provider.pricing()
        await self._rt.conversations.record_usage(
            owner_id=OWNER, conversation_id=self.id, provider=provider.name,
            model=provider.model, usage=usage,
            cost_usd=pricing.cost(usage) if pricing is not None else None,
            now_ms=self._rt.clock(),
        )
```

`self._overlap` comes from Task 10. Until then, define `self._overlap: Overlap | None = None` with a forward stub, or simply leave that check out and add it in Task 10. **Prefer adding it in Task 10.**

4. **Using it in `_heard`.** Replace the block from `said = await listener.end()` through `answer = True`:

```python
            said = await listener.end()
            text = said.text[:MAX_TEXT].strip()
            draft, self._draft = self._draft, None
            if text:
                trace.mark("heard", self._now())
                self._arrived = trace
                await self._send("transcript.final", text=text)
                self._via = "voice"
                self._turn += 1
                self.active_ms = self._rt.clock()
                usable = (
                    draft is not None
                    and not draft.failed
                    and same_words(draft.words, text)
                    and draft.history_len == len(self._history)
                    and draft.summary is self._summary
                    and not self._queued
                )
                if usable:
                    assert draft is not None  # noqa: S101 - narrowed above
                    await self._commit_input("user", draft.built)
                    self._using = draft
                    trace.draft = "used"
                else:
                    if draft is not None:
                        await draft.cancel()
                        await self._record_draft_usage(draft)
                        trace.draft = "dropped"
                    await self._save_input("user", text)
                answer = True
            elif draft is not None:
                await draft.cancel()
                await self._record_draft_usage(draft)
```

Also add `self._using: Draft | None = None` to `__init__`. A transcript that differs only in punctuation from the draft's still saves the draft's message, whose words are the draft's. That is intended: the words are the same.

5. **Replay in `_one_turn`.** Before `done = await run_turn(...)`:

```python
            using, self._using = self._using, None
```

Pass `first=using.replay() if using is not None else None` to `run_turn`. A used draft also means `_compact_if_long` must not run first. In `_run`, call `await self._compact_if_long()` only when `self._using is None`.

6. **Usage of a used draft** is the turn's own: `run_turn` adds the `Done` usage from the replayed events, so it is not recorded twice. Don't call `_record_draft_usage` for a used draft.

7. **Cleanup.**
   - `_stop_listening`, `close` and the `interrupt` handler call `await self._drop_draft()`.
   - In `_heard`'s `except asyncio.CancelledError` branch, cancel `draft` if it was taken. The simplest way is a `finally` that cancels a draft that wasn't used: track it with a local `used` flag.

8. **Trace.** Add `draft: str = "none"` to `Trace`, and include it wherever traces are serialized. Find that with `grep -n "endpoint_s" src/trillion/api/trace.py src/trillion/storage -r` and add `draft` beside it, following the same pattern.

- [ ] **Step 4: Run** `uv run pytest -q tests/api tests/brain tests/voice && uv run mypy`. Expected: PASS, with every existing listening and barge test unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/api tests/api
git commit -s -m "Session: start a draft on probably-done, use it or drop it (VOI-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `decide()`: acknowledgement, interruption, echo or nothing

**Files:**
- Create: `src/trillion/voice/overlap.py`
- Test: `tests/voice/test_overlap.py`

- [ ] **Step 1: Failing tests**

```python
"""Talking over her (VOI-9): an acknowledgement lets her carry on, anything else stops her."""

import pytest

from trillion.voice.overlap import ACK_AFTER_S, DECIDE_BY_S, decide

PLAYING = ("We could leave at six.", "Or if traffic is bad, at half past.")


@pytest.mark.parametrize("words", ["yeah", "mm-hm", "Right.", "okay", "got it", "uh-huh yeah"])
def test_acknowledgements_carry_on_once_done_or_after_a_moment(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.3, playing=PLAYING) == "wait"
    assert decide(words, done=True, elapsed_s=0.3, playing=PLAYING) == "carry_on"
    assert decide(words, done=False, elapsed_s=ACK_AFTER_S, playing=PLAYING) == "carry_on"


@pytest.mark.parametrize("words", ["wait", "no", "stop", "yeah but", "actually", "tell me more"])
def test_anything_else_interrupts_at_once(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.1, playing=PLAYING) == "interrupt"


def test_four_acknowledgements_are_more_than_an_acknowledgement() -> None:
    assert decide("yeah yeah yeah yeah", done=True, elapsed_s=0.5, playing=PLAYING) == "interrupt"


def test_no_words_carry_on_only_at_the_limit() -> None:
    assert decide("", done=False, elapsed_s=0.5, playing=PLAYING) == "wait"
    assert decide("", done=False, elapsed_s=DECIDE_BY_S, playing=PLAYING) == "carry_on"


def test_her_own_voice_coming_back_is_ignored() -> None:
    assert decide("traffic is bad", done=False, elapsed_s=0.4, playing=PLAYING) == "wait"
    assert decide("leave at six", done=True, elapsed_s=0.4, playing=PLAYING) == "wait"
    assert decide("leave at six", done=False, elapsed_s=DECIDE_BY_S, playing=PLAYING) == "carry_on"


def test_another_language_interrupts() -> None:
    assert decide("oui", done=True, elapsed_s=0.3, playing=PLAYING) == "interrupt"
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement `src/trillion/voice/overlap.py`**

```python
"""Deciding what the user meant by talking over her (VOI-9).

Pure: words, whether the recognizer says they are done, and how long it has been,
in; one of three answers out. An acknowledgement ("yeah", "mm-hm") lets her carry
on, and so does nothing at all (a cough, a door). Any other word stops her, at
once. Words that are all in what she is saying are her own voice coming back.
The list is English; in another language every word stops her, which is the safe
way to be wrong.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

Decision = Literal["wait", "carry_on", "interrupt"]

# Acknowledgements alone carry on once the recognizer says they are done, or after this.
ACK_AFTER_S = 1.2
# By this long after they started, it is decided either way.
DECIDE_BY_S = 1.5
# More than this many acknowledgements is saying something.
_MOST_ACKS = 3
_PHRASES = ("got it", "i see")
_ACKS = frozenset(
    "yeah yes yep mm-hm mhm mm uh-huh right okay ok sure exactly totally true".split()
)
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _acks(words: list[str]) -> int | None:
    """How many acknowledgements the words are, or None if any word is something else."""
    text = " ".join(words)
    count = 0
    for phrase in _PHRASES:
        count += text.count(phrase)
        text = text.replace(phrase, " ")
    rest = text.split()
    if any(w not in _ACKS for w in rest):
        return None
    return count + len(rest)


def decide(words: str, *, done: bool, elapsed_s: float, playing: Sequence[str]) -> Decision:
    heard = _words(words)
    hers = {w for sentence in playing for w in _words(sentence)}
    if heard and all(w in hers for w in heard):
        heard = []  # her own voice, come back through the microphone
    if not heard:
        return "carry_on" if elapsed_s >= DECIDE_BY_S else "wait"
    acks = _acks(heard)
    if acks is None or acks > _MOST_ACKS:
        return "interrupt"
    return "carry_on" if done or elapsed_s >= ACK_AFTER_S else "wait"
```

Note that "okay", "right" and "yes" may appear in her own sentence. The echo rule then treats them as echo, which still ends in `carry_on`. Fine.

- [ ] **Step 4: Run** `uv run pytest -q tests/voice/test_overlap.py && uv run mypy`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/voice/overlap.py tests/voice/test_overlap.py
git commit -s -m "Voice: tell an acknowledgement from an interruption (VOI-9)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Keeping what was heard

**Files:**
- Modify: `src/trillion/api/speech.py` (`Speaker`)
- Modify: `src/trillion/brain/loop.py` (`run_turn(heard=...)`)
- Modify: `src/trillion/api/session.py` (`playback.sentence`, `_one_turn`, the `interrupted` message)
- Test: `tests/api/test_speaker.py`. If `test_speaker.py` lives in `tests/voice`, use that.
- Test: `tests/brain/test_loop.py`
- Test: `tests/api/test_heard.py`

- [ ] **Step 1: Failing tests**

Speaker (`tests/voice/test_speaker.py`, using its existing fake TTS and helpers):

```python
async def test_the_speaker_knows_which_sentences_were_heard() -> None:
    speaker = make_speaker()  # the file's existing helper for a core-voice Speaker
    await speaker.feed("We could leave at six. Or if traffic is bad, at half past. Or")
    assert speaker.heard_text() == ""
    speaker.played(1)
    assert speaker.heard_text() == "We could leave at six."
    speaker.played(2)
    assert speaker.heard_text() == "We could leave at six. Or if traffic is bad, at half past."
    assert speaker.playing() == ("We could leave at six.", "Or if traffic is bad, at half past.")


async def test_heard_text_is_only_this_round() -> None:
    speaker = make_speaker()
    await speaker.feed("Let me look. ")
    speaker.played(1)
    await speaker.pause()  # a tool runs between rounds
    await speaker.feed("Found it. It is at four.")
    speaker.played(2)
    assert speaker.heard_text() == "Found it."
```

Loop (`tests/brain/test_loop.py`):

```python
async def test_a_cancelled_spoken_turn_keeps_only_what_was_heard() -> None:
    # Reuse the file's cancellation test setup: a provider that yields text then hangs.
    saved = await cancel_mid_reply(text="One. Two. Three.", heard=lambda t: "One.")
    assert saved[-1].blocks[0].text == "One.…" + INTERRUPTED
```

Base this on the existing test for "what the user already read is kept and marked" (`grep -n "INTERRUPTED" tests/brain/*.py`). Copy its setup and pass `heard=`.

Session (`tests/api/test_heard.py`): a spoken turn with a held provider. The page reports `playback.sentence` seq 1 and then barges; the saved assistant message is `"<first sentence>…" + INTERRUPTED`, and `interrupted.kept` is `"<first sentence>…"`. A typed turn interrupted the same way keeps the full text. Use `voiced(rt)` from `tests/api/test_session_notices.py` (or the file's equivalent), plus `Gated` with `hold`. Model it on `tests/api/test_barge.py::test_talking_over_her_stops_the_reply_and_she_listens`, but say the question by voice (`hearing()`/`speak()`), so `_via == "voice"`.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

`Speaker` (`api/speech.py`):
- In `__init__`: `self._said: list[tuple[int, str]] = []` (seq and the raw sentence), `self._heard_seq = 0` and `self._round_from = 1`.
- In `_say`, after `self._count += 1`: `self._said.append((self._count, sentence))`. Store the raw `sentence`, not the speakable one: the history holds what she wrote.
- In `pause()`, after flushing: `self._round_from = self._count + 1`.
- Add:

```python
    def played(self, seq: int) -> None:
        """The page started playing sentence `seq` (VOI-15b)."""
        self._heard_seq = max(self._heard_seq, seq)

    def heard_text(self) -> str:
        """This round's sentences the user has started to hear (D8)."""
        return " ".join(
            s for n, s in self._said if self._round_from <= n <= self._heard_seq
        )

    def playing(self) -> tuple[str, ...]:
        """The sentence being heard and the one before: what an echo of her would say."""
        return tuple(s for n, s in self._said if self._heard_seq - 1 <= n <= self._heard_seq)
```

`run_turn` (`brain/loop.py`):
- Add the parameter `heard: Callable[[str], str] | None = None`, documented as: "`heard`, for a spoken turn, cuts the text to what the user heard when it is interrupted (D8)."
- In the `except asyncio.CancelledError:` branch:

```python
        except asyncio.CancelledError:
            # What the user already read, or heard, is kept and marked; a half-formed
            # tool call is dropped, because it never ran.
            said = "".join(text)
            if heard is not None:
                kept = heard(said)
                said = f"{kept}…" if kept else ""
            if said or heard is not None:
                await asyncio.shield(add(_assistant(said + INTERRUPTED, [], None)))
            raise
```

  With `heard` and nothing heard, the saved text is just the marker, stripped of its leading space. `INTERRUPTED` is `" [interrupted]"`, so use `(said + INTERRUPTED).strip()`.

`session.py`:
- Keep the turn's speaker as `self._speaker` (set in `_one_turn`, cleared in its `finally`, or wherever it ends).
- Handle the new page message in `handle()`:

```python
        elif kind == "playback.sentence":
            speaker, seq = self._speaker, msg.get("seq")
            if speaker is not None and isinstance(seq, int) and not isinstance(seq, bool):
                speaker.played(seq)
```

- Pass `heard=speaker.heard_text_of if (speaker is not None and self._via == "voice") else None` to `run_turn`, where `heard_text_of = lambda _text: speaker.heard_text()`. Either add a tiny method `heard_of(self, _text: str) -> str: return self.heard_text()` on `Speaker`, or use a lambda.
- In `_one_turn`'s `except asyncio.CancelledError:` branch, compute what was kept before stopping the speaker:

```python
            kept = (speaker.heard_text() + "…") if (speaker is not None and self._via == "voice" and speaker.heard_text()) else None
            ...
            await self._send("interrupted", **({"kept": kept} if kept is not None else {}))
```

  When the voice is on and nothing was heard, send `kept=""`, so the page shows only the marker. Condition: `self._via == "voice" and speaker is not None`. Then `kept = speaker.heard_text()`, plus "…" if it isn't empty.

- [ ] **Step 4: Run** `uv run pytest -q tests/brain tests/api tests/voice && uv run mypy`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trillion tests
git commit -s -m "Voice: an interrupted reply is kept up to what was heard (VOI-9)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Talking over her, in the session

**Files:**
- Modify: `src/trillion/api/session.py`
- Test: `tests/api/test_overlap_session.py`

- [ ] **Step 1: Failing tests**

```python
"""Talking over her (VOI-9): duck, decide, then carry on or stop."""

# Fixtures: the `rt` fixture from tests/api/test_drafts.py (copy it), plus judging().

async def spoken_reply(rt, *replies):  # type: ignore[no-untyped-def]
    stt, provider = judging(rt, *replies)
    voiced(rt)  # a core voice, so the turn is spoken
    held = provider.hold(0)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("tell me the plan", final=True, turn="done")
    await speak(session, room(0.1))
    await asyncio.sleep(0.05)
    return stt, provider, held, page, session


async def test_an_acknowledgement_lets_her_carry_on(rt) -> None:  # type: ignore[no-untyped-def]
    stt, provider, held, page, session = await spoken_reply(rt, say("We leave at six. Then dinner."))
    await session.handle({"type": "overlap"})
    assert session._task is not None and session._overlap is not None
    stt.stream.say("yeah", final=True, turn="done")
    await speak(session, tone(0.3) + room(0.1))
    await asyncio.sleep(0.05)
    assert page.all("overlap.over") and not page.all("interrupted")
    assert session._overlap is None and session._task is not None
    held.set()
    await idle(session)
    assert [k for k, _ in await rt.conversations.history(session.id, "local")] == ["user", "assistant"]
    await session.close()


async def test_a_real_interruption_stops_her_and_becomes_the_turn(rt) -> None:  # type: ignore[no-untyped-def]
    stt, provider, held, page, session = await spoken_reply(
        rt, say("We leave at six. Then dinner."), say("Sure, seven then."))
    await session.handle({"type": "overlap"})
    stt.stream.say("wait, make it seven", final=False)
    await speak(session, tone(0.3))
    await asyncio.sleep(0.05)
    assert page.all("interrupted") and session._listener is not None
    held.set()
    stt.stream.say("wait, make it seven", final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)
    assert page.all("transcript.final")[-1]["text"] == "wait, make it seven"
    await session.close()


async def test_a_cough_carries_on_after_the_limit(rt) -> None:  # type: ignore[no-untyped-def]
    stt, provider, held, page, session = await spoken_reply(rt, say("A long answer."))
    await session.handle({"type": "overlap"})
    await speak(session, room(1.6))
    await asyncio.sleep(0.05)
    assert page.all("overlap.over") and not page.all("interrupted")
    await session.close()


async def test_without_turn_judging_hearing_an_overlap_is_a_barge(rt) -> None:  # type: ignore[no-untyped-def]
    # hearing() from tests/api/test_listening gives a FakeSTT without turns.
    ...
```

For the last test, assert that an `overlap` with a non-judging recognizer behaves like `barge`: `interrupted` is sent and a listener opens. The page never sends `overlap` in that case, but the core must still be safe.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

At the top of `session.py`:

```python
@dataclass(kw_only=True)
class Overlap:
    """The user started talking over her (VOI-9); deciding what they meant."""

    listener: Listener
    started: float
    done: bool = False
    watcher: asyncio.Task[None] | None = None
```

In `__init__`: `self._overlap: Overlap | None = None`. Add the `self._overlap is not None` check to `_start_draft` (deferred from Task 7).

In `handle()`: `elif kind == "overlap": await self._on_overlap()`.

```python
    async def _on_overlap(self) -> None:
        """Duck, then decide (VOI-9): her reply keeps going, quieter, while a second of
        what the user says is heard. Without a recognizer that judges turns, or with
        nothing to talk over, it is a barge."""
        rt = self._rt
        task = self._task
        if rt.stt is None or rt.listening is None or not rt.stt.turns or task is None or task.done():
            await self._on_barge()
            return
        if self._overlap is not None or self._listener is not None:
            return
        listener = Listener(rt.stt, rt.listening.choice, on_partial=self._quiet_partial)
        loop = asyncio.get_running_loop()
        ov = Overlap(listener=listener, started=loop.time())
        self._overlap = ov
        self._last_audio = loop.time()
        try:
            await listener.start()
        except VoiceError:
            # Can't hear them: she stops, as a barge would have.
            self._overlap = None
            await listener.abandon()
            await self._on_barge()
            return
        ov.watcher = asyncio.create_task(self._watch_overlap(ov))

    async def _quiet_partial(self, text: str) -> None:
        """Words heard over her are not shown until they turn out to be a turn."""
        return None

    async def _watch_overlap(self, ov: Overlap) -> None:
        loop = asyncio.get_running_loop()
        while self._overlap is ov:
            speaker = self._speaker
            verdict = decide(
                ov.listener.text_now(),
                done=ov.done,
                elapsed_s=loop.time() - ov.started,
                playing=speaker.playing() if speaker is not None else (),
            )
            if verdict == "interrupt":
                await self._overlap_interrupts(ov)
                return
            if verdict == "carry_on":
                await self._overlap_carries_on(ov)
                return
            await asyncio.sleep(0.05)

    async def _overlap_carries_on(self, ov: Overlap) -> None:
        self._overlap = None
        await ov.listener.abandon()
        await self._send("overlap.over")
        if self._task is None and self._via == "voice":
            # Her reply ended meanwhile: the follow-up window it would have opened.
            await self._on_listen(FOLLOW_UP_S, follow_up=True)

    async def _overlap_interrupts(self, ov: Overlap) -> None:
        """A real interruption: she stops as a barge stops her, and the listener that
        was deciding goes on as the user's turn, with every word kept."""
        self._overlap = None
        self._spoken = None
        async with self._taking_input():
            task = self._task
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError, Exception):
                    await asyncio.wait_for(asyncio.shield(task), BARGE_WAIT_S)
            listener = ov.listener
            listener.set_partial(self._partial)
            self._listener = listener
            self._warm()
            await self._send("mic.start", follow_up=False)
            await self._state("listening")
            self._watchdog = asyncio.create_task(self._watch(listener))
            await self._partial(listener.text_now())
            if ov.done:
                await self._conclude("end")
```

Add `Listener.set_partial(self, cb)` to `listening.py`: it replaces `self._on_partial`. Also give `Listener` an `on_turn` setter, or pass `on_turn=self._turn_signal` when it is promoted, so a promoted turn can start a draft too.

In `audio()`, route to the overlap listener first:

```python
        ov = self._overlap
        if ov is not None:
            if len(pcm) > MAX_AUDIO_FRAME:
                return
            self._last_audio = asyncio.get_running_loop().time()
            try:
                verdict = await ov.listener.audio(pcm)
            except VoiceError:
                await self._overlap_carries_on(ov)
                return
            if verdict == "end":
                ov.done = True
            elif verdict is not None and self._overlap is ov:
                # "nothing", "inaudible" or "too_long" while deciding: she carries on.
                await self._overlap_carries_on(ov)
            return
```

Other changes:
- **`facts()`:** `listening=self._listener is not None or self._overlap is not None`.
- **`playback.ended` handler:** also require `self._overlap is None` before reopening.
- **`_stop_listening`, `close`, `interrupt`:** if `self._overlap` is set, clear it, cancel its watcher and abandon its listener (`_end_overlap()`). Don't send `overlap.over` on `interrupt`, because the page stops everything anyway.
- **Imports:** `decide` from `trillion.voice.overlap`.

- [ ] **Step 4: Run** `uv run pytest -q tests/api tests/voice tests/delivery && uv run mypy`. Expected: PASS, including every existing barge test.

- [ ] **Step 5: Commit**

```bash
git add src/trillion tests/api
git commit -s -m "Session: duck, then decide when the user talks over her (VOI-9)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The page: ducking, `overlap`, sentence reports and `kept`

**Files:**
- Modify: `apps/web/src/voice.ts`
- Modify: `apps/web/src/main.ts`
- Modify: `apps/web/src/view.ts`
- Modify: `apps/web/src/types.ts` or wherever `ServerMsg` and `View.listening` are typed (`grep -n "listening:" apps/web/src/view.ts`)
- Test: `apps/web/test/view.test.ts`, `apps/web/test/voice.test.ts`

- [ ] **Step 1: Failing tests**

`view.test.ts`:

```ts
  it('trims an interrupted reply to what was heard', () => {
    const v = feed([{ type: 'reply.delta', text: 'One. Two. Three.' }, { type: 'interrupted', kept: 'One.…' }]);
    expect(v.entries[0]).toEqual({ kind: 'assistant', text: 'One.…', interrupted: true });
  });

  it('keeps the full text when nothing says what was heard', () => {
    const v = feed([{ type: 'reply.delta', text: 'Typed answer.' }, { type: 'interrupted' }]);
    expect(v.entries[0]).toEqual({ kind: 'assistant', text: 'Typed answer.', interrupted: true });
  });
```

`voice.test.ts`: if the file already fakes `AudioContext`, add a test that `duck(true)` sets the gain target to 0.2 and `duck(false)` to 1. Also test that `onSentence(turn, seq)` fires for a core frame of a new seq, once per seq. If `voice.test.ts` does not fake `AudioContext`, test only the pure part. In that case, export `const DUCKED = 0.2` and assert its value; the ramp is covered by the live check.

- [ ] **Step 2: Run** `pnpm --filter ./apps/web test`, expect FAIL.

- [ ] **Step 3: Implement**

`view.ts`, in `case 'interrupted'`:

```ts
          (e) => ({ ...e, interrupted: true, ...(typeof msg['kept'] === 'string' ? { text: msg['kept'] } : {}) }) as Entry,
```

`voice.ts`:
- `VoiceHooks` gains `onSentence(turn: number, seq: number): void`, documented as: "A sentence started playing: what the user has heard (VOI-9)."
- `Voice` gains:
  - `duck(on: boolean): void`, documented as: "Quieter while the user may be talking over her (VOI-9)."
  - `sentence(turn, seq, text)`: change the signature to take `seq`.
- Implement `duck`:

```ts
export const DUCKED = 0.2;
// ...
    duck(on) {
      if (!ctx || !gain) return;
      gain.gain.setTargetAtTime(on ? DUCKED : 1, ctx.currentTime, 0.03); // about 80 ms
    },
```

- In `stop()`, reset the gain to 1 immediately (`gain.gain.cancelScheduledValues(0); gain.gain.value = 1;`).
- In `audio()`, report each new `seq` when it actually starts sounding:

```ts
      if (frame.seq !== lastSeq || frame.turn !== lastSeqTurn) {
        lastSeq = frame.seq;
        lastSeqTurn = frame.turn;
        const { turn: t, seq } = frame;
        const inMs = Math.max(0, (nextAt - audio.currentTime) * 1000);
        reportTimers.add(setTimeout(() => { if (t !== stopped) hooks.onSentence(t, seq); }, inMs));
      }
```

  Put this before `nextAt += buffer.duration`. Declare `lastSeq = -1`, `lastSeqTurn = -1` and `reportTimers = new Set<ReturnType<typeof setTimeout>>()`. In `stop()`, clear every timer and reset `lastSeq`.
- For the computer's own voice, `sentence(t, seq, text)` calls `hooks.onSentence(t, seq)` in `utterance.onstart`.

`main.ts`:
- Pass `onSentence: (turn, seq) => send({ type: 'playback.sentence', turn_id: turn, seq })` to `createVoice`.
- Change the `reply.sentence` line to `voice.sentence(Number(msg['turn']), Number(msg['seq']), String(msg['text'] ?? ''))`.
- In `watchFrame`, on detection:

```ts
  if (detector.push(frameLevel(pcm), voice.level() ?? 0)) {
    if (view.listening?.turns) {
      // Duck, then decide (VOI-9): she goes quiet while the core hears what this is.
      voice.duck(true);
      overlapping = true;
      micMode = 'send';
      send({ type: 'overlap' });
      for (const frame of preroll.drain()) socket?.readyState === WebSocket.OPEN && socket.send(frame);
    } else {
      bargeHeld = preroll.drain();
      voice.stop();
      send({ type: 'barge' });
    }
  }
```

  Declare `let overlapping = false;`.
- On `overlap.over`:

```ts
    if (msg.type === 'overlap.over') {
      overlapping = false;
      voice.duck(false);
      micMode = 'watch';
      detector.reset();
      preroll.drain();
    }
```

- On `interrupted` or `audio.stop`: also set `overlapping = false`. `voice.stop()` already restores the gain.
- `stopWatching()` must not close the microphone while `overlapping`. Add `|| overlapping` to its early return.
- `view.listening` must carry `turns`. The core already sends it from `describe()` (Task 1). Add `readonly turns?: boolean` to the listening type in `view.ts`.

- [ ] **Step 4: Run** `pnpm -r typecheck && pnpm -r test && pnpm -r build`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web
git commit -s -m "Page: duck her voice, report what was heard, and trim to it (VOI-9)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Docs

**Files:**
- Modify: `docs/PRD.md`
  - In the VOI-12 row, change P2 to P1 and note it is built for hearing that judges turns (Deepgram Flux), with early drafts.
  - In VOI-3, add a sentence: "With a recognizer that judges turns (VOI-12), its end-of-turn decides, and the local rules only catch silence, an inaudible microphone and overlong turns."
  - In VOI-9, add: "With such a recognizer, a voice over hers first ducks her, and an acknowledgement lets her carry on; an interrupted spoken reply is saved up to the sentence the user was hearing."
- Modify: `README.md` voice section. Find it with `grep -n "Deepgram" README.md` and mention that Deepgram listening now lets you pause mid-thought and say "yeah" over her.
- Modify: `docs/architecture.md`. Add `api/draft.py` and `voice/overlap.py` to the module table.
- Modify: `docs/TESTING.md`, if it has a voice checklist. Add the live checks from the spec's Testing section.

- [ ] **Step 1: Make the edits.**
- [ ] **Step 2: Run** `uv run python scripts/containment_check.py && uv run python scripts/check_agent_name.py`.
- [ ] **Step 3: Commit**

```bash
git add docs README.md
git commit -s -m "Fluid turns: docs (VOI-12, VOI-9)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Final gate and live check

- [ ] **Step 1: Run the full gate** (the commands at the top). Everything passes.
- [ ] **Step 2: Live check.** This needs the user's Deepgram key in the keychain.
  - Start the `trillion` preview (port 7790).
  - Stub audio playback (`HTMLMediaElement.prototype.play` and `AudioContext` output) or turn Voice off, so nothing plays aloud.
  - Choose Deepgram in Settings → Hearing, and check that the "Start thinking before I finish" switch shows.
  - Drive a spoken turn with a recorded WAV played into the mic, or confirm the URL and events with a short real utterance from the user.
  - Check `/api/trace` for `draft: used` on a clean ending and `dropped` after a resumed pause.
  - If live audio can't be driven from the browser pane, say so plainly and leave the live check to the user, with steps.
- [ ] **Step 3: Confirm every commit is signed off:** `git log --format='%(trailers:key=Signed-off-by)' main..HEAD | grep -c Signed-off-by` equals `git rev-list --count main..HEAD`.
