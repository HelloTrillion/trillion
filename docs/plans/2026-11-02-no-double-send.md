# No Double-Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An approved email, calendar change or Slack post is never sent twice, and an interrupted one is checked against the provider and, when in doubt, told to the user. See [docs/specs/2026-11-02-no-double-send.md](../specs/2026-11-02-no-double-send.md).

**Architecture:** The spine already claims an action as `executing` before it runs and never re-runs one at startup. This adds: the action id handed to the tool (`ToolContext.action_id`), a three-way check result settled by the router (landed, never went, couldn't tell), a later re-check for "couldn't tell", checks in the Gmail, Calendar and Slack tools, a notice through the one door when in doubt, and a real kill test.

**Tech Stack:** Python 3.13, asyncio, aiosqlite, httpx (fakes via `httpx.MockTransport`), pytest. Run everything with `uv run`.

**Branch:** `spine/no-double-send` (already created from main at db97a24).

**Rules for every task:**
- Commit with `git commit -s` and end the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No em or en dashes in prompts. Use generic names only (no real people or businesses); `scripts/containment_check.py` enforces it.
- Never open or print `src/trillion/connectors/google/google_client.json`.
- `tests/spine/` is the protected safety suite: add tests there as the plan says; change an existing one only where a task says so.
- The full check before each commit: `uv run pytest -q -x --ignore=tests/spine 2>&1 | tail -2 && uv run pytest tests/spine -q 2>&1 | tail -2 && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/containment_check.py`.

## File map

| File | Change |
|---|---|
| `src/trillion/spine/contract.py` | `ToolContext.action_id` |
| `src/trillion/spine/router.py` | `Settled`, `_settle`, `recover()` and `check_unchecked()` returning `list[Settled]`, the lost-answer path, `represent` across sessions after `recovered:not_sent` |
| `src/trillion/connectors/google/tools.py` | Gmail `Message-ID` tag and check; Calendar event id and the three checks |
| `src/trillion/connectors/slack/tools.py` | Slack post check |
| `src/trillion/delivery/interrupted.py` (new) | Her one line about an interrupted send, and the notice |
| `src/trillion/api/runtime.py` | Notices from `recover()`; `check_unchecked()` every 10 minutes |
| `tests/spine/test_outcomes.py` | The router's new outcomes |
| `tests/spine/test_no_double_send.py` (new) | The kill test |
| `tests/mcp/test_google.py`, `tests/mcp/test_slack_channels.py` | The tools' tags and checks |
| `tests/unit/test_interrupted.py` (new) | The line and the notice |
| `docs/PRD.md`, `docs/TESTING.md` | OPS-1 "Exists:" note; a TESTING row |

---

### Task 1: The action id reaches the tool

**Files:**
- Modify: `src/trillion/spine/contract.py` (`ToolContext`, around line 59)
- Modify: `src/trillion/spine/router.py` (`_execute_claimed`, around line 633; `recover`, around line 731)
- Modify: `tests/spine/fakes.py` (`ScriptedTool` records the context it saw)
- Test: `tests/spine/test_outcomes.py`

- [ ] **Step 1: Let `ScriptedTool` remember the contexts it was given.** In `tests/spine/fakes.py`, in `ScriptedTool.__init__` add `self.seen: list[ToolContext] = []`. At the top of its `execute` and its `reconcile` add `self.seen.append(ctx)`.

- [ ] **Step 2: Write the failing tests.** Append to `tests/spine/test_outcomes.py`:

```python
async def _to_executing(spine: Spine, pid: str) -> None:
    for old, new in ((Status.PROPOSED, Status.APPROVED), (Status.APPROVED, Status.EXECUTING)):
        await spine.store.transition(
            pid,
            old,
            new,
            now_ms=spine.clock(),
            decision=DecisionIn(event=new.value, decided_by="s1", via="tap"),
        )


async def test_the_tool_is_given_the_action_id_when_it_runs(spine: Spine) -> None:
    spine.scripted.result = ToolResult(ok=True, content="done", effect="confirmed")
    r = await spine.call("scripted", {})
    await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert spine.scripted.seen[-1].action_id == r.pending_id


async def test_the_check_is_given_the_action_id(spine: Spine) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=True, content="landed", effect="confirmed")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    await spine.router.recover((spine.granted,))
    assert spine.scripted.seen[-1].action_id == pid


async def test_a_direct_call_has_no_action_id(spine: Spine) -> None:
    ctx = spine.ctx()
    assert ctx.action_id is None
```

Also replace the two inline `for old, new in ...` loops in `test_recover_uses_the_tools_own_evidence` and `test_recover_when_reconcile_raises_is_unknown` with `await _to_executing(spine, pid)`. Move `_to_executing` above the first test that uses it.

- [ ] **Step 3: Run them and see them fail.**

Run: `uv run pytest tests/spine/test_outcomes.py -q -k "action_id"`
Expected: 3 failed (`ToolContext` has no `action_id`).

- [ ] **Step 4: Add the field.** In `ToolContext` in `src/trillion/spine/contract.py`, after `routine_target`:

```python
    # The approved action this run carries out, set by the router on the confirmed path
    # and in every check of it. A tool puts it where the provider keeps it, so a check
    # after a crash can find what was sent (OPS-1). None on the direct path.
    action_id: str | None = None
```

- [ ] **Step 5: Set it in the router.** In `_execute_claimed`, first line: `ctx = dataclasses.replace(ctx, action_id=a.id)`. In `recover`, add `action_id=a.id,` to the `ToolContext(...)` it builds.

- [ ] **Step 6: Run the tests.**

Run: `uv run pytest tests/spine/test_outcomes.py -q`
Expected: all pass.

- [ ] **Step 7: Full check, then commit.**

```bash
git add src/trillion/spine/contract.py src/trillion/spine/router.py tests/spine/fakes.py tests/spine/test_outcomes.py
git commit -s -m "Spine: hand the approved action's id to the tool that runs or checks it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: The router settles a check three ways

**Files:**
- Modify: `src/trillion/spine/router.py`
- Modify: `src/trillion/api/runtime.py:871` (`recover()`'s result type changes; keep the call working)
- Test: `tests/spine/test_outcomes.py`, `tests/spine/test_router_edges.py:317`

The rules (spec, "The router"):
- A check returning `ToolResult(effect="confirmed")` → `succeeded`, decision `recovered:succeeded`.
- `ToolResult(effect="none")` → `invalidated`, detail `"interrupted before it went out"`, decision `recovered:not_sent`.
- `ToolResult(effect="unknown")`, or the check raised → couldn't tell. If `now < a.created_at + CHECK_FOR_MS` the action stays `executing` with `effect_status="unchecked"` (no transition; use a store update of `effect_status` only, see Step 4); otherwise `unknown`, decision `recovered:unknown`.
- `reconcile` returned `None` (no check), or the tool is gone → `unknown`, decision `recovered:unknown`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/spine/test_outcomes.py` (`Settled` and `CHECK_FOR_MS` are imported from `trillion.spine.router`):

```python
async def test_a_check_that_finds_it_settles_as_sent(spine: Spine) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=True, content="in Sent", effect="confirmed")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    settled = await spine.router.recover((spine.granted,))
    assert settled == [Settled(id=pid, outcome="succeeded")]
    assert spine.scripted.executions == 0


async def test_a_check_that_proves_it_never_went_can_be_asked_again(spine: Spine) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=True, content="not in Sent", effect="none")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    assert await spine.router.recover((spine.granted,)) == [Settled(id=pid, outcome="not_sent")]
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.INVALIDATED
    assert a.outcome_detail == "interrupted before it went out"
    assert [d.event for d in await spine.store.list_decisions(pid)][-1] == "recovered:not_sent"
    assert spine.scripted.executions == 0
    # The session that raised it ended with the crash; any of the owner's may ask again.
    again = await spine.router.represent(pid, spine.ctx(session_id="s2", turn=1))
    assert again.status == "confirmation_required"


async def test_only_a_proved_not_sent_action_crosses_sessions(spine: Spine) -> None:
    r = await spine.call("slow_irreversible", {})
    pid = r.pending_id or ""
    await spine.store.transition(
        pid,
        Status.PROPOSED,
        Status.APPROVED,
        now_ms=spine.clock(),
        decision=DecisionIn(event="approved", decided_by="s1", via="tap"),
    )
    await spine.router.recover((spine.granted,))  # approved, never started: invalidated
    again = await spine.router.represent(pid, spine.ctx(session_id="s2", turn=1))
    assert again.status == "refused"


async def test_a_check_that_cannot_tell_waits_and_tries_again(spine: Spine) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=False, content="offline", effect="unknown")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    assert await spine.router.recover((spine.granted,)) == []
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.EXECUTING and a.effect_status == "unchecked"
    # Back online ten minutes later.
    spine.clock.advance(10 * 60 * 1000)
    spine.scripted.reconcile_result = ToolResult(ok=True, content="in Sent", effect="confirmed")
    assert await spine.router.check_unchecked() == [Settled(id=pid, outcome="succeeded")]


async def test_a_check_that_cannot_tell_for_a_day_is_unknown(spine: Spine) -> None:
    spine.scripted.reconcile_error = RuntimeError("offline")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    await spine.router.recover((spine.granted,))
    spine.clock.advance(CHECK_FOR_MS)
    assert await spine.router.check_unchecked() == [Settled(id=pid, outcome="unknown")]
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.UNKNOWN
    assert spine.scripted.executions == 0


async def test_check_unchecked_leaves_a_running_action_alone(spine: Spine) -> None:
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)  # executing, effect_status still None: running now
    assert await spine.router.check_unchecked() == []
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.EXECUTING


async def test_a_lost_answer_is_checked_at_once(spine: Spine) -> None:
    spine.scripted.result = ToolResult(ok=False, content="timed out", effect="unknown")
    spine.scripted.reconcile_result = ToolResult(ok=True, content="in Sent", effect="confirmed")
    r = await spine.call("scripted", {})
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.status == "ok"
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.SUCCEEDED
    assert spine.scripted.executions == 1


async def test_a_lost_answer_that_cannot_be_checked_yet_waits(spine: Spine) -> None:
    spine.scripted.result = ToolResult(ok=False, content="timed out", effect="unknown")
    spine.scripted.reconcile_result = ToolResult(ok=False, content="offline", effect="unknown")
    r = await spine.call("scripted", {})
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.status == "unknown"
    assert "checking" in res.content.lower()
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.EXECUTING and a.effect_status == "unchecked"
```

Change the existing `test_recover_when_reconcile_raises_is_unknown`: after `recover`, assert the action is `EXECUTING` with `effect_status == "unchecked"`; then `spine.clock.advance(CHECK_FOR_MS)`, `await spine.router.check_unchecked()`, and assert `UNKNOWN` as it does today. Rename it `test_recover_when_reconcile_raises_waits_then_is_unknown`.

`test_1_2_1_crash_mid_execution_is_unknown_and_never_retried` and `test_recover_without_the_tool_is_unknown` keep their assertions (a tool with no check, or no tool, is `unknown` at once). Where they compare `recover()`'s return to a list of ids, compare `[s.id for s in ...]` instead. Do the same in `test_recover_invalidates_approved_but_unstarted` (that one is `Settled(id=pid, outcome="interrupted")`; see Step 3).

If `FakeClock` has no `advance`, look at `tests/spine/conftest.py` for how tests move it (it may be `clock.now += ms` or `clock.tick(ms)`) and use that throughout.

If a router response for the waiting case has no `"unknown"` status in its literal type, use the status the router already gives `Status.UNKNOWN` results (`_result_response` with `Status.UNKNOWN`) and assert on that.

- [ ] **Step 2: Run them and see them fail.**

Run: `uv run pytest tests/spine/test_outcomes.py tests/spine/test_router_edges.py -q`
Expected: the new tests fail (`Settled` and `check_unchecked` don't exist).

- [ ] **Step 3: Add `Settled` and the constants.** Near the top of `src/trillion/spine/router.py`, after the imports:

```python
# OPS-1: a check that can't reach the provider is tried again for this long after the
# action was proposed, then the action is `unknown`.
CHECK_FOR_MS = 24 * 60 * 60 * 1000
UNCHECKED = "unchecked"
NOT_SENT = "interrupted before it went out"

Outcome = Literal["succeeded", "not_sent", "unknown", "interrupted"]


@dataclass(frozen=True)
class Settled:
    """An interrupted action that a check settled, for telling the user (OPS-1)."""

    id: str
    outcome: Outcome
```

(`interrupted` is an `approved` action that never started, invalidated by `recover` as today. The runtime doesn't tell the user about those: nothing left the computer.)

- [ ] **Step 4: A store update for `effect_status` alone.** `transition()` always changes status and writes a decision. Marking an action `unchecked` changes neither. Add to `SqliteSpineStore` in `src/trillion/storage/sqlite/spine_store.py`, and to the `SpineStore` protocol in `src/trillion/spine/store.py`:

```python
    async def mark_unchecked(self, pending_id: str, *, now_ms: int) -> bool:
        """An `executing` action whose check could not reach the provider: checked again
        later (OPS-1). The status does not change, so no decision is written."""
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE pending_actions SET effect_status = 'unchecked', updated_at = ?"
                " WHERE id = ? AND status = 'executing'",
                (now_ms, pending_id),
            )
            return cur.rowcount == 1
```

`scripts/check_one_writer.py` guards status changes. Run it; if it flags this statement because it touches `pending_actions`, add the method to its allow-list with a comment that it changes no status.

- [ ] **Step 5: `_settle`, `recover`, `check_unchecked`.** Replace the `EXECUTING` loop in `recover` and add `check_unchecked`:

```python
    async def recover(self, granted_roots: tuple[Path, ...]) -> list[Settled]:
        """At startup: settle interrupted actions. Never re-runs one (SPN-17a, OPS-1)."""
        self._granted = granted_roots
        settled: list[Settled] = []
        for a in await self._store.list_by_status(Status.APPROVED):
            await self._move(
                a.id,
                Status.APPROVED,
                Status.INVALIDATED,
                _system("interrupted"),
                detail="interrupted before it ran; ask again",
            )
            settled.append(Settled(id=a.id, outcome="interrupted"))
        for a in await self._store.list_by_status(Status.EXECUTING):
            done = await self._settle(a)
            if done is not None:
                settled.append(done)
        return settled

    async def check_unchecked(self) -> list[Settled]:
        """Check again each action whose last check could not reach the provider."""
        settled: list[Settled] = []
        for a in await self._store.list_by_status(Status.EXECUTING):
            if a.effect_status != UNCHECKED:
                continue  # running now
            done = await self._settle(a)
            if done is not None:
                settled.append(done)
        return settled

    async def _settle(self, a: PendingAction) -> Settled | None:
        """Ask the tool what happened to `a` and record it. None: still can't tell."""
        ctx = ToolContext(
            owner_id=a.owner_id,
            session_id=a.session_id,
            turn=a.created_turn,
            call_id=f"recover-{a.id}",
            via="text",
            granted_roots=self._granted,
            action_id=a.id,
        )
        tool = self._get(a.tool)
        result: ToolResult | None = None
        can_check = tool is not None
        try:
            result = await tool.reconcile(a.args, ctx) if tool is not None else None
            if result is None:
                can_check = False
        except Exception as e:  # a check is best-effort by contract
            logger.warning("reconcile of %s raised %s", a.id, type(e).__name__)
        now = self._clock()
        if result is not None and result.effect == "confirmed":
            return await self._settled(a, Status.SUCCEEDED, "succeeded", result)
        if result is not None and result.effect == "none":
            return await self._settled(a, Status.INVALIDATED, "not_sent", result, NOT_SENT)
        if can_check and now < a.created_at + CHECK_FOR_MS:
            await self._store.mark_unchecked(a.id, now_ms=now)
            return None
        return await self._settled(a, Status.UNKNOWN, "unknown", result)

    async def _settled(
        self,
        a: PendingAction,
        status: Status,
        outcome: Outcome,
        result: ToolResult | None,
        detail: str | None = None,
    ) -> Settled | None:
        won = await self._move(
            a.id,
            Status.EXECUTING,
            status,
            _system(f"recovered:{outcome}"),
            effect=result.effect if result else "unknown",
            detail=detail or (result.content[:_DETAIL_LIMIT] if result else "interrupted mid-run"),
        )
        return Settled(id=a.id, outcome=outcome) if won else None
```

`self._granted` is new: set it to `()` in `__init__`, so `check_unchecked` (which has no roots passed in) uses the roots `recover` was given.

Note `effect` for `not_sent` is `"none"` (the result's), so the record shows nothing happened.

- [ ] **Step 6: The lost-answer path.** In `_execute_claimed`, after `result = await self._run(...)` and before `status = final_status(result)`:

```python
        if result.effect == "unknown":
            # No answer from the provider: it may have gone. Ask before recording a guess.
            done = await self._settle(a)
            if done is None:
                return self._result_response(
                    tool,
                    Status.UNKNOWN,
                    "No answer came back, so it may or may not have gone. Checking with"
                    " the provider and will say when it's clear.",
                    result.error_kind,
                )
            return self._from_record(await self._reload(a))
```

Check that `_from_record` gives `ok` for a `succeeded` record and the right refusal or failure for `invalidated` and `unknown`; it is what a concurrent confirm already sees, so it should.

- [ ] **Step 7: `represent` across sessions.** In `_represent`, replace `if a.session_id != ctx.session_id: return _refused(Refusal.WRONG_SESSION)` with:

```python
        if a.session_id != ctx.session_id and not await self._proved_not_sent(a):
            return _refused(Refusal.WRONG_SESSION)
```

and add:

```python
    async def _proved_not_sent(self, a: PendingAction) -> bool:
        """A check proved it never went out (OPS-1). The session that raised it ended with
        the crash, so any of the owner's sessions may ask for a new card."""
        if a.status is not Status.INVALIDATED:
            return False
        decisions = await self._store.list_decisions(a.id)
        return bool(decisions) and decisions[-1].event == "recovered:not_sent"
```

The owner check already happened in `represent`.

- [ ] **Step 8: Keep the runtime working.** In `src/trillion/api/runtime.py:871` the result of `recover` is ignored, so nothing changes yet; Task 6 uses it.

- [ ] **Step 9: Run the spine suite.**

Run: `uv run pytest tests/spine -q`
Expected: all pass.

- [ ] **Step 10: Full check, then commit.**

```bash
git add src/trillion/spine/router.py src/trillion/spine/store.py src/trillion/storage/sqlite/spine_store.py scripts/check_one_writer.py tests/spine
git commit -s -m "Spine: settle an interrupted send three ways, and check again when the provider can't be reached

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Gmail tags each send and can find it

**Files:**
- Modify: `src/trillion/connectors/google/tools.py` (`_Compose.build`, `GmailSend`)
- Test: `tests/mcp/test_google.py` (and its `FakeGoogle`)

- [ ] **Step 1: Teach `FakeGoogle` Sent search.** In `FakeGoogle.__init__` add `self.sent: list[dict[str, Any]] = []` and `self.down = False`. At the top of `handle`, after the call is appended: `if self.down and path.startswith(("/gmail", "/calendar")): raise httpx.ConnectError("down")`. In the `/gmail/v1/users/me/messages/send` branch, decode the raw message (`message_from_bytes(base64.urlsafe_b64decode(body["raw"]))`) and append `{"id": f"m{len(self.sent)}", "message_id": msg["Message-ID"], "to": msg["To"], "subject": msg["Subject"]}` to `self.sent`. Add a branch for `GET /gmail/v1/users/me/messages` that reads `q` from the params and returns `{"messages": [{"id": m["id"]} for m in self.sent if <q matches>]}`: for `rfc822msgid:X` match `m["message_id"] == X` (with or without the angle brackets); for a `to:` and `subject:` query match both.

- [ ] **Step 2: Write the failing tests.** Follow the file's existing pattern for building a connected `GmailSend` (look at the test around line 304 that reads `fake.calls` for `/messages/send`). Add:

```python
async def test_a_sent_email_carries_the_action_id(...) -> None:
    # Run gmail_send through the confirmed path; the action's id is in the Message-ID.
    ...
    msg = message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    assert msg["Message-ID"].startswith(f"<trillion-{pending_id}@")


async def test_the_check_finds_a_sent_email(...) -> None:
    # gmail_send runs, then reconcile with the same action_id: confirmed.
    ...
    assert result.effect == "confirmed"


async def test_the_check_finds_nothing_when_it_never_went(...) -> None:
    # reconcile with an action_id that was never sent: none.
    ...
    assert result.effect == "none"


async def test_the_check_falls_back_to_recipients_and_subject(...) -> None:
    # Gmail rewrote the header: the fake stores a different Message-ID, but the same
    # To and Subject were sent after the approval: confirmed.
    ...


async def test_the_check_cannot_tell_when_google_is_down(...) -> None:
    fake.down = True
    ...
    assert result.effect == "unknown"
```

Write each body in full, using the file's fixtures (the connected Google wiring, `Spine`, the `FakeGoogle` instance); call `tool.reconcile(args, dataclasses.replace(ctx, action_id=...))` directly for the check tests.

- [ ] **Step 3: Run them and see them fail.**

Run: `uv run pytest tests/mcp/test_google.py -q -k "action_id or check"`
Expected: fail.

- [ ] **Step 4: Tag the email.** `_Compose.build` gains a keyword `action_id: str | None = None`. After `msg["Subject"] = subject`:

```python
        if action_id:
            # OPS-1: how a check after a crash finds this email in Sent.
            domain = account.rsplit("@", 1)[-1] or "trillion.local"
            msg["Message-ID"] = f"<trillion-{action_id}@{domain}>"
```

`GmailDraft.run` passes nothing (a draft is never checked). `GoogleTool.execute` passes `ctx` on: change `run(self, account, args)` to `run(self, account, args, ctx)` for every Google tool, a mechanical change, and `GmailSend.run` calls `self.build(account, args, action_id=ctx.action_id)`.

- [ ] **Step 5: The check.** Add to `GmailSend`:

```python
    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        if not ctx.action_id:
            return None
        try:
            account = self.account(args)
            domain = account.rsplit("@", 1)[-1] or "trillion.local"
            tag = f"<trillion-{ctx.action_id}@{domain}>"
            if await self._found(account, f"in:sent rfc822msgid:{tag}"):
                return ToolResult(ok=True, content="Found it in Sent.", effect="confirmed")
            to = " ".join(f"to:{a}" for a in _addresses(args.get("to"), "To"))
            subject = str(args.get("subject", "")).replace('"', "")
            if await self._found(account, f'in:sent {to} subject:"{subject}" newer_than:2d'):
                return ToolResult(ok=True, content="Found it in Sent.", effect="confirmed")
        except (_Problem, GoogleCallError) as e:
            return ToolResult(ok=False, content=str(e), effect="unknown")
        return ToolResult(ok=True, content="Not in Sent: it never went.", effect="none")

    async def _found(self, account: str, q: str) -> bool:
        found = await self.call(
            account, "GET", "/gmail/v1/users/me/messages", params={"q": q, "maxResults": 1}
        )
        return bool(found.get("messages"))
```

A network failure must come out as `GoogleCallError(answered=False)` from the client; check `client.py` and that `httpx.ConnectError` is turned into one. If not, also catch `httpx.HTTPError` here.

The fallback is looser than the tag (a second email with the same subject to the same people in two days would count). That errs toward "it went", which never sends twice. Say so in a comment.

- [ ] **Step 6: Run the Google tests.**

Run: `uv run pytest tests/mcp/test_google.py -q`
Expected: all pass.

- [ ] **Step 7: Full check, then commit.**

```bash
git add src/trillion/connectors/google/tools.py tests/mcp/test_google.py
git commit -s -m "Gmail: tag each send with its action and find it in Sent after a crash

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: Calendar changes can be checked

**Files:**
- Modify: `src/trillion/connectors/google/tools.py` (`CalendarCreate`, `CalendarUpdate`, `CalendarDelete`)
- Test: `tests/mcp/test_google.py`

- [ ] **Step 1: Teach `FakeGoogle` event ids.** In the `POST /calendar/v3/calendars/primary/events` branch: if the body has an `id` already in `self.events`, return `httpx.Response(409, json={"error": {"code": 409, "message": "The requested identifier already exists."}})`; otherwise store under that id (or a generated one). The `GET .../events/{id}` branch returns 404 for an unknown id and the event (with its `status`) otherwise. `DELETE` sets `status` to `"cancelled"`.

- [ ] **Step 2: Write the failing tests.**

```python
def test_event_ids_from_an_action_are_valid_and_stable() -> None:
    from trillion.connectors.google.tools import event_id

    a = event_id("pa_01HXYZ")
    assert a == event_id("pa_01HXYZ") and a != event_id("pa_01HXZZ")
    assert 5 <= len(a) <= 1024 and set(a) <= set("abcdefghijklmnopqrstuv0123456789")
```

plus, in the file's style with the connected wiring:
- `calendar_create` run with an `action_id` sends `id == event_id(action_id)`;
- create's check: event present → `confirmed`; absent → `none`; `fake.down` → `unknown`;
- a second create with the same `action_id` is refused by Google (409) and does not add an event (the guarantee holds even without the check);
- update's check: the event already has every field the args set → `confirmed`; an old value → `none`;
- delete's check: `cancelled` or 404 → `confirmed`; still there → `none`.

- [ ] **Step 3: Run them and see them fail.**

Run: `uv run pytest tests/mcp/test_google.py -q -k "event_id or calendar"`

- [ ] **Step 4: The id.** Module level in `tools.py`:

```python
_BASE32HEX = "0123456789abcdefghijklmnopqrstuv"


def event_id(action_id: str) -> str:
    """A Calendar event id made from the action's: Google refuses a second event with
    the same id, so an approved event can't be added twice (OPS-1). Base32hex, as
    Google requires, 52 characters."""
    digest = int.from_bytes(hashlib.sha256(action_id.encode()).digest(), "big")
    out = []
    for _ in range(52):
        digest, i = divmod(digest, 32)
        out.append(_BASE32HEX[i])
    return "".join(out)
```

`CalendarCreate.run`: `if ctx.action_id: body["id"] = event_id(ctx.action_id)`.

- [ ] **Step 5: The checks.** A shared helper on `_EventChange`:

```python
    async def _event(self, account: str, eid: str) -> JSON | None:
        """The event, or None if Google has no such event (404 or 410)."""
        try:
            return await self.call(
                account, "GET", f"/calendar/v3/calendars/primary/events/{eid}"
            )
        except GoogleCallError as e:
            if e.answered and e.kind in ("not_found", "gone"):
                return None
            raise
```

(Check `client.py` for the `kind` it gives a 404 and a 410 and use those.)

`CalendarCreate.reconcile`: no `action_id` → `None`; `_event(account, event_id(action_id))` → present and not cancelled `confirmed`, else `none`; `GoogleCallError` or `_Problem` → `unknown`.

`CalendarUpdate.reconcile`: get `args["event_id"]`; compare every key `event_body(args)` sets against the event (for `start`/`end` compare the `date` or `dateTime` after normalizing with `datetime.fromisoformat`; for `attendees` compare the email sets). All equal `confirmed`; else `none`; gone `none`.

`CalendarDelete.reconcile`: event is None or `status == "cancelled"` → `confirmed`; else `none`.

- [ ] **Step 6: Run the Google tests**, then the full check and commit:

```bash
git add src/trillion/connectors/google/tools.py tests/mcp/test_google.py
git commit -s -m "Calendar: an approved event can't be added twice, and each change can be checked after a crash

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: A Slack post can be checked

**Files:**
- Modify: `src/trillion/connectors/slack/tools.py` (`SlackPost`)
- Test: `tests/mcp/test_slack_channels.py` (and its `FakeSlack`)

- [ ] **Step 1: Make `FakeSlack` keep what was posted** (`chat.postMessage` appends `{"ts", "user", "text", "thread_ts"}` to the conversation) and return it from `conversations.history` (filtered by `oldest`) and `conversations.replies`. Add a `down` flag that raises `httpx.ConnectError`.

- [ ] **Step 2: Write the failing tests.** In the file's style:
- a post, then `reconcile` with the same args → `confirmed`;
- no post → `none`;
- the same text from someone else → `none`;
- a thread reply found through `conversations.replies` → `confirmed`;
- `down` → `unknown`.

- [ ] **Step 3: Run them and see them fail.**

- [ ] **Step 4: The check.** Add to `SlackPost`:

```python
    async def reconcile(self, args: JSON, ctx: ToolContext) -> ToolResult | None:
        """Slack has no id a client can give a message, so the evidence is the exact
        approved text from the user in that conversation since the action was proposed."""
        if not ctx.action_id:
            return None
        try:
            token = self.token()
            cid, _ = await self.find(token, str(args["channel"]))
            me = await self.me(token)
            since = str(self.proposed_s(ctx))  # see below
            if args.get("thread_ts"):
                page = await self.w.slack.call(
                    token, "conversations.replies",
                    {"channel": cid, "ts": str(args["thread_ts"]), "oldest": since},
                )
            else:
                page = await self.w.slack.call(
                    token, "conversations.history", {"channel": cid, "oldest": since}
                )
        except (_Problem, SlackError) as e:
            return ToolResult(ok=False, content=str(e), effect="unknown")
        text = str(args["text"])
        if any(m.get("user") == me and m.get("text") == text for m in page.get("messages", [])):
            return ToolResult(ok=True, content="Found it in Slack.", effect="confirmed")
        return ToolResult(ok=True, content="Not in Slack: it never went.", effect="none")
```

The check needs two things the tool may not have yet:
- **Who the user is:** if `SlackTool` has no `me()`, add one calling `auth.test` and returning `user_id`.
- **When it was proposed:** the tool doesn't know. Add `proposed_ms: int | None = None` to `ToolContext` next to `action_id`, set it to `a.created_at` wherever `action_id` is set (Task 1's two places and `_settle`), and use `ctx.proposed_ms // 1000 - 60` here (a minute's slack for clocks). With no `proposed_ms`, use the last day.

Slack may turn `<`, `>` and `&` into entities in the stored text; compare after `html.unescape` on the stored text.

- [ ] **Step 5: Run the Slack tests**, then the full check and commit:

```bash
git add src/trillion/connectors/slack/tools.py src/trillion/spine/contract.py src/trillion/spine/router.py tests/mcp/test_slack_channels.py
git commit -s -m "Slack: a post can be checked after a crash

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: She tells you when in doubt

**Files:**
- Create: `src/trillion/delivery/interrupted.py`
- Modify: `src/trillion/api/runtime.py` (around line 871, and `start_background` / `stop_background` around line 550)
- Test: `tests/unit/test_interrupted.py`

Read `src/trillion/attention/words.py` first: this follows it closely (one small model call, no tools, untrusted input wrapped, a short timeout, no line on any failure, `sayable` refuses links, emails and long digit runs). Reuse its helpers where they fit rather than copying them.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_interrupted.py`), with a fake `ModelProvider` that streams a scripted answer (look for an existing fake in `tests/unit/attention/` and reuse it) and a real `SqliteScheduleStore` on a temporary database (see how `tests/unit/attention/` builds one):

```python
async def test_not_sent_makes_one_notice_with_her_line(...) -> None:
    provider = Scripted("The email to Sam didn't go out before I stopped. Want me to send it?")
    await tell(store, provider, OWNER, [Settled(id="pa1", outcome="not_sent")], actions, now_ms=1)
    (n,) = await store.held(OWNER)
    assert n.title == "A send was interrupted"
    assert n.text and "send it" in n.text


async def test_unknown_makes_one_notice(...) -> None: ...


async def test_succeeded_and_interrupted_say_nothing(...) -> None:
    await tell(store, provider, OWNER,
               [Settled(id="a", outcome="succeeded"), Settled(id="b", outcome="interrupted")],
               actions, now_ms=1)
    assert await store.held(OWNER) == []


async def test_told_once(...) -> None:
    # tell() twice with the same Settled: one notice (keyed action:{id}).
    ...


async def test_no_model_is_shown_not_spoken(...) -> None:
    await tell(store, None, OWNER, [Settled(id="pa1", outcome="unknown")], actions, now_ms=1)
    (n,) = await store.held(OWNER)
    assert n.text is None and n.title == "A send was interrupted"


async def test_the_summary_reaches_the_model_as_untrusted(...) -> None:
    # The fake provider records the messages it got; the action's summary is inside
    # an <untrusted_tool_result> wrapper.
    ...
```

`actions` is a mapping or lookup from id to `PendingAction` (use the spine store's `get_pending`); use whatever `held` / `list` method the schedule store has for held notices.

- [ ] **Step 2: Run them and see them fail.**

- [ ] **Step 3: Write `src/trillion/delivery/interrupted.py`.**

```python
"""Telling the user about a send a crash interrupted, when in doubt (OPS-1).

A send the check found is recorded and nothing is said. One it proved never went,
or could not settle, gets one notice through the one door, once (keyed by the
action), in her words: one small model call, the action's summary wrapped as
untrusted, since it can quote an email someone else wrote. No answer is no line:
the notice is then shown by its title, never spoken with stock words (PER-11).
"""
```

with:
- `TITLE = "A send was interrupted"` (a lock screen is public: nothing from the email);
- `INSTRUCTIONS`: two short paragraphs for the model. For `not_sent`: "it did not go out before Trillion stopped; say so in one short sentence naming what it was, then ask whether to send it." For `unknown`: "you cannot tell whether it went; say so in one short sentence naming what it was, and where to look (the Sent folder, the calendar, the conversation). Do not offer to send it again." Both: in your own words, no dashes, no links, no addresses read out;
- `async def line(provider, outcome, summary) -> tuple[str | None, Usage]`, built like `words.line`;
- `async def tell(store, provider, owner_id, settled, get_pending, *, now_ms) -> Usage`: for each `Settled` with outcome `not_sent` or `unknown`, fetch the action, compose the line, and add the notice with the store's `attention_notice(owner_id, f"action:{s.id}", title=TITLE, text=line, now_ms=now_ms)`. Check that `attention_notice` makes one notice per key; if a second call with the same key adds another, check for it first.

- [ ] **Step 4: Wire it into the runtime.** At `runtime.py:871`, keep `settled = await router.recover(...)`. Once the runtime has its provider, schedule store and door (after line ~992), start `tell(...)` for `settled` as a background task on the loop, so a slow model call never holds startup. Record its usage the way `record_attention_usage` does (BRN-4).

In `start_background`, start a task that every 10 minutes runs `router.check_unchecked()` and passes what it returns to `tell`; cancel it in `stop_background`. Follow how the other loops there stop cleanly.

- [ ] **Step 5: Run the tests**, then the full check and commit:

```bash
git add src/trillion/delivery/interrupted.py src/trillion/api/runtime.py tests/unit/test_interrupted.py
git commit -s -m "She says so when a send was interrupted and she isn't sure it went

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7: The kill test

**Files:**
- Create: `tests/spine/test_no_double_send.py`
- Create: `tests/spine/kill_child.py` (the child process's script)

This is OPS-1's proof. A child process approves and runs a send; a local HTTP server stands in for the provider. The moment the server has accepted, the child dies with `os._exit(9)`, before the record is written. The parent then opens the same database, runs `recover()`, and checks.

- [ ] **Step 1: The fake provider server.** In the test file, a `ThreadingHTTPServer` on `127.0.0.1:0` in a thread:
  - `POST /send` stores the JSON body's `tag` and replies 200 (or 503 when `refuse` is set);
  - `GET /sent?tag=X` replies `{"found": true|false}`.
  It counts sends.

- [ ] **Step 2: The child script** (`tests/spine/kill_child.py`). Run with `sys.executable -I` and `TRILLION_HOME` set to the test's temporary folder. It:
  1. opens the database with `connect(paths.db_path())` and `migrate`;
  2. builds a `Router` with a `ToolRegistry` holding one tool, `HttpSend`, whose `execute` POSTs `{"tag": ctx.action_id}` to the server URL given in `argv`, then calls `os._exit(9)` if the response was 200 (the kill: after the provider accepted, before `_execute_claimed` writes `succeeded`), and returns `effect="none"` on 503;
  3. proposes the send on turn 1 and confirms it on turn 2 (`via="tap"`), exactly as `tests/spine/conftest.py` does.

  `HttpSend.reconcile` asks `GET /sent?tag=...`: found `confirmed`, not found `none`, a connection error `unknown`. Put `HttpSend` in `tests/spine/fakes.py` so the parent registers the same tool.

  Use `urllib.request` (no new dependency).

- [ ] **Step 3: The tests.**

```python
def test_a_send_killed_after_the_provider_accepted_goes_out_once(tmp_path: Path) -> None:
    server = start_server()
    child = run_child(tmp_path, server.url)
    assert child.returncode == 9  # it died between the provider and the record
    assert server.sends == 1

    settled = asyncio.run(recover_in(tmp_path, server.url))
    assert [s.outcome for s in settled] == ["succeeded"]
    assert server.sends == 1  # recovery never sends


def test_a_send_the_provider_refused_can_be_asked_for_again(tmp_path: Path) -> None:
    server = start_server(refuse=True)
    child = run_child(tmp_path, server.url, die_before_record=True)
    ...
    # recover: not_sent; represent from a new session gives a card; still 0 sends.
```

For the second case the child must die before writing the record although the provider refused: give `HttpSend` a flag (an `argv` option) to `os._exit(9)` on any answer.

`recover_in` opens the database, builds the same router (a new process's view), calls `recover((tmp_path,))`, reads the action and its decisions, and returns the `Settled` list. Assert the last decision is `recovered:succeeded` (or `recovered:not_sent`).

- [ ] **Step 4: Run it.**

Run: `uv run pytest tests/spine/test_no_double_send.py -v`
Expected: 2 passed, on macOS and Linux; on Windows `os._exit` behaves the same.

- [ ] **Step 5: Full check and commit.**

```bash
git add tests/spine/test_no_double_send.py tests/spine/kill_child.py tests/spine/fakes.py
git commit -s -m "Spine: prove a send killed between the provider and the record goes out once (OPS-1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: Docs

**Files:**
- Modify: `docs/PRD.md` (OPS-1 row, around line 587)
- Modify: `docs/TESTING.md` (after the 1.2.2 row, around line 46)

- [ ] **Step 1: PRD.** At the end of the OPS-1 requirement text, before `| P1 |`, add: ` Exists: [no double-send](specs/2026-11-02-no-double-send.md): Gmail, Calendar and Slack sends are tagged and checked after a crash; MCP tools stay unknown.` Match how other rows phrase their "Exists:" notes.

- [ ] **Step 2: TESTING.md.** A row after 1.2.2, in the table's style:

`| 1.2.4 | The process dies after the provider accepted a send and before the record is written | On restart the check finds it: recorded as sent, sent exactly once, nothing re-run (OPS-1). — [test](../tests/spine/test_no_double_send.py) |`

(Use the next free number if 1.2.4 is taken.)

- [ ] **Step 3: Full check and commit.**

```bash
git add docs/PRD.md docs/TESTING.md
git commit -s -m "Docs: OPS-1 exists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
