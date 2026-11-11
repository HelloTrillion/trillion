"""SPN-20 through the router: when the owner's own words are the yes, the action is
recorded and run exactly as a yes on a later turn would run it, and when anything
fails it is today's card."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from tests.spine.conftest import Spine
from tests.spine.fakes import MARKER
from trillion.spine import deny_floor
from trillion.spine.confirm import OWN_WORDS, Refusal, Status
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.loop_guard import LoopGuard
from trillion.spine.router import Router, RouterResponse

SAID = f"keep a rule: never book anything before nine {MARKER}"
RULE = "never book anything before nine"


class KeepWords(BaseTool):
    """A first-party-shaped tool that opts in: keeps some words, undoably."""

    definition = ToolDefinition(
        name="keep_words",
        description="Keep some words (test).",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}, "id": {"type": "string"}},
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        category="test",
        timeout_s=5.0,
        recoverable=True,
        own_words=("text",),
    )

    def __init__(self) -> None:
        self.kept: list[str] = []
        self.seen: list[ToolContext] = []
        self.prechecks: list[Precondition] = []
        self.fingerprints: list[str] = []
        self.result = ToolResult(ok=True, content="kept", effect="confirmed")

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text=f"Keep: {args.get('text', '')}")

    def target(self, args: JSON) -> str:
        return "words"

    def fingerprint(self, args: JSON) -> str | None:
        return self.fingerprints.pop(0) if self.fingerprints else "same"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        return self.prechecks.pop(0) if self.prechecks else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.kept.append(str(args.get("text")))
        self.seen.append(ctx)
        return self.result


@dataclasses.dataclass
class Words:
    spine: Spine
    router: Router
    tool: KeepWords
    on: list[bool]

    async def say(
        self,
        args: dict[str, Any] | None = None,
        guard: LoopGuard | None = None,
        name: str = "keep_words",
        **kw: Any,
    ) -> RouterResponse:
        base: dict[str, Any] = {"via": "voice", "speaker": "owner", "heard": SAID, "turn": 2}
        c = self.spine.ctx(**(base | kw))
        return await self.router.execute(
            ToolCall(name=name, args=args if args is not None else {"text": RULE}), c, guard
        )


def make(spine: Spine, setting: Callable[[], Awaitable[bool]] | None, **kw: Any) -> Router:
    return Router(
        spine.registry,
        spine.store,
        policy=DenyPolicy.default(),
        clock=spine.clock,
        own_words_on=setting,
        **kw,
    )


@pytest.fixture
def words(spine: Spine) -> Words:
    tool = KeepWords()
    spine.registry.register(tool, module="trillion.tools.keep_words")
    on = [True]

    async def setting() -> bool:
        return on[0]

    return Words(spine, make(spine, setting), tool, on)


async def test_the_owners_words_are_the_yes(words: Words) -> None:
    r = await words.say()
    assert r.status == "succeeded" and r.own_words and r.pending_id
    assert "no card is waiting" in r.detail
    assert words.tool.kept == [RULE]
    a = await words.spine.store.get_pending(r.pending_id)
    assert a is not None and a.status is Status.SUCCEEDED
    # Same record as any card: proposed, then approved by the owner's words, spoken,
    # then the claim and the outcome.
    decisions = await words.spine.store.list_decisions(r.pending_id)
    assert [d.event for d in decisions] == ["proposed", "approved", "executing", "succeeded"]
    approved = decisions[1]
    assert (approved.decided_by, approved.via) == (OWN_WORDS, "voice")
    assert decisions[0].decided_by == "s1"
    # The model is never told the flag, only the words.
    assert "own_words" not in r.to_model()


async def test_it_runs_with_its_approved_action_id(words: Words) -> None:
    # The idempotency key a tool hands its provider is the action's id (SPN-17b, OPS-1).
    r = await words.say()
    (seen,) = words.tool.seen
    assert seen.action_id == r.pending_id and seen.proposed_ms is not None


async def test_one_approval_one_execution(words: Words) -> None:
    r = await words.say()
    assert r.pending_id
    # A confirm of the same card, on the next turn, runs nothing again.
    again = await words.router.execute(
        ToolCall(name="confirm_pending_action", args={"pending_id": r.pending_id}),
        words.spine.ctx(via="voice", speaker="owner", turn=3),
    )
    assert again.reason is Refusal.ALREADY_RESOLVED
    assert words.tool.kept == [RULE]


async def test_off_is_the_card(words: Words) -> None:
    words.on[0] = False
    r = await words.say()
    assert r.status == "confirmation_required" and not r.own_words
    assert words.tool.kept == []
    assert r.pending_id
    a = await words.spine.store.get_pending(r.pending_id)
    assert a is not None and a.status is Status.PROPOSED


async def test_no_setting_wired_is_the_card(spine: Spine) -> None:
    spine.registry.register(KeepWords(), module="trillion.tools.keep_words")
    router = make(spine, None)
    r = await router.execute(
        ToolCall(name="keep_words", args={"text": RULE}),
        spine.ctx(via="voice", speaker="owner", heard=SAID, turn=2),
    )
    assert r.status == "confirmation_required"


async def test_a_setting_that_cannot_be_read_is_off(spine: Spine) -> None:
    tool = KeepWords()
    spine.registry.register(tool, module="trillion.tools.keep_words")

    async def broken() -> bool:
        raise OSError("database gone")

    router = make(spine, broken)
    r = await router.execute(
        ToolCall(name="keep_words", args={"text": RULE}),
        spine.ctx(via="voice", speaker="owner", heard=SAID, turn=2),
    )
    assert r.status == "confirmation_required" and tool.kept == []


@pytest.mark.parametrize(
    "kw",
    [
        {"via": "text"},
        {"via": "tap"},
        {"heard": None},
        {"host_turn": True},
        {"speaker": None},
        {"read_outside": True},
        {"helper_id": "h1"},
        {"heard": "keep a rule: never book anything before 9"},
    ],
)
async def test_any_check_failing_is_the_card(words: Words, kw: dict[str, Any]) -> None:
    r = await words.say(**kw)
    assert r.status == "confirmation_required" and not r.own_words
    assert words.tool.kept == []


@pytest.mark.parametrize("speaker", ["other", "replay", "unsure"])
async def test_another_voice_is_refused_before_anything(words: Words, speaker: str) -> None:
    # Default-private (PER-13) refuses before any card, as today.
    r = await words.say(speaker=speaker)
    assert r.status == "refused" and r.reason is Refusal.PRIVATE
    assert words.tool.kept == []


async def test_an_id_keeps_the_card(words: Words) -> None:
    r = await words.say({"text": RULE, "id": "r1"})
    assert r.status == "confirmation_required"


async def test_one_per_turn(words: Words) -> None:
    first = await words.say()
    assert first.status == "succeeded" and first.own_words
    # A second gated call in the same turn gets a card, words or not.
    words.tool.fingerprints = []
    second = await words.say(
        {"text": "never book anything after six"}, heard=SAID + " and never book anything after six"
    )
    assert second.status == "confirmation_required" and not second.own_words
    assert words.tool.kept == [RULE]
    # The next turn has its own.
    third = await words.say(
        {"text": "never book anything after six"},
        turn=3,
        heard="never book anything after six",
    )
    assert third.status == "succeeded" and third.own_words


async def test_one_per_turn_is_per_session(words: Words) -> None:
    assert (await words.say()).own_words
    other = await words.say(session_id="s2")
    assert other.own_words


async def test_the_deny_floor_still_refuses(words: Words, monkeypatch: pytest.MonkeyPatch) -> None:
    # Passes when proposed, refuses on the confirmed path: nothing runs.
    real = deny_floor.check
    calls = [0]

    def check(*a: Any, **kw: Any) -> deny_floor.Denial | None:
        calls[0] += 1
        if calls[0] >= 2:
            return deny_floor.Denial(arg="text", reason="protected")
        return real(*a, **kw)

    monkeypatch.setattr(deny_floor, "check", check)
    r = await words.say()
    assert r.status == "refused" and r.reason is Refusal.DENIED_BY_FLOOR and r.own_words
    assert words.tool.kept == []
    assert r.pending_id
    a = await words.spine.store.get_pending(r.pending_id)
    assert a is not None and a.status is Status.FAILED


async def test_a_precondition_that_fails_on_the_confirmed_path(words: Words) -> None:
    words.tool.prechecks = [Precondition.passed(), Precondition.failed("gone")]
    r = await words.say()
    assert r.status == "refused" and r.reason is Refusal.PRECONDITION_CHANGED
    assert words.tool.kept == []
    assert r.pending_id
    a = await words.spine.store.get_pending(r.pending_id)
    assert a is not None and a.status is Status.INVALIDATED


async def test_a_target_that_changed_meanwhile(words: Words) -> None:
    words.tool.fingerprints = ["before", "after"]
    r = await words.say()
    assert r.reason is Refusal.PRECONDITION_CHANGED and words.tool.kept == []


async def test_a_precheck_that_fails_first_is_refused_as_today(words: Words) -> None:
    words.tool.prechecks = [Precondition.failed("which one?")]
    r = await words.say()
    assert r.status == "refused" and r.reason is Refusal.PRECONDITION_FAILED
    assert not r.own_words and r.pending_id is None


async def test_a_failure_counts_against_the_loop_guard(words: Words) -> None:
    words.tool.result = ToolResult(ok=False, content="no", effect="none")
    guard = LoopGuard()
    r = await words.say(guard=guard)
    assert r.status == "failed" and r.own_words
    assert guard._failures["keep_words"] == 1  # as a failed confirm counts (SPN-7)


async def test_it_needs_no_screen(spine: Spine) -> None:
    # The running app needs a card on screen before a yes (#4). The owner's words say
    # what it does, so there is no card to have seen; that is the point of SPN-20.
    tool = KeepWords()
    spine.registry.register(tool, module="trillion.tools.keep_words")

    async def on() -> bool:
        return True

    router = make(spine, on, require_delivery=True)
    r = await router.execute(
        ToolCall(name="keep_words", args={"text": RULE}),
        spine.ctx(via="voice", speaker="owner", heard=SAID, turn=2),
    )
    assert r.status == "succeeded" and tool.kept == [RULE]


async def test_spend_and_unrecoverable_never(spine: Spine) -> None:
    # Ineligible tools cannot even be registered with own_words; one without it, said
    # word for word, is the card or the tap as always.
    async def on() -> bool:
        return True

    router = make(spine, on)
    c = spine.ctx(via="voice", speaker="owner", heard="spend twelve dollars on lunch", turn=2)
    r = await router.execute(ToolCall(name="spend_money", args={"amount_cents": 1200}), c)
    assert r.status == "confirmation_required"
    assert spine.spend.calls == []


async def test_the_audit_has_the_proposal_and_the_resolution(words: Words) -> None:
    r = await words.say()
    rows = [x for x in await words.spine.store.list_audit() if x.tool == "keep_words"]
    assert [x.decision for x in rows] == ["proposed", "resolve"]
    assert rows[-1].outcome == r.status
    assert all(MARKER not in x.args_hash for x in rows)
