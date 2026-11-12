"""Standing rules (BRN-5): keep one, list them, or drop one.

A kept rule goes into the system block on every turn, where nothing that
shortens the conversation can reach it. That is why keeping one asks: a page or a
file she read must not be able to slip a rule in. A rule can only make her more
careful. The spine's gates are in code, and no rule loosens them.

One kind of rule is kept at once, without a card: one the user has just said, in
their own words, that only stops her doing something ("never use emojis"). It can
only narrow what she does, so a yes on a card would add nothing. `narrows` decides
which rules those are, and it fails safe: a rule that might loosen a check, change
what reaches the user, or touch what only a card decides is kept on a card, as
before. So is any rule said in a turn that has read something from outside.
Dropping a rule always asks: without it she may do more than before.

Every rule kept or dropped is written to the permanent record (`rule_events`), so
"did that ever get approved?" has an answer after the chat is cleared.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Literal, Protocol

from trillion.brain.standing import (
    MAX_RULE_CHARS,
    Rule,
    RuleError,
    RuleEvent,
    StandingRules,
    clean,
    narrows,
    read_back,
)
from trillion.spine.contract import (
    ASKS_BY_CARD,
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

logger = logging.getLogger(__name__)

KEEP, DROP, NEVER, LIST_RULES = "rule_keep", "rule_drop", "rule_never", "rule_list"
_PREVIEW = 160
_CHOICE_PREVIEW = 50
_REFUSALS = {
    "empty": "there was no rule to keep",
    "too_long": f"too long for a rule: keep it under {MAX_RULE_CHARS} characters",
    "full": "there are already as many standing rules as she can hold; drop one first",
    "not_found": "no such rule",
    "disk": "the rules file could not be written",
}
# Said to the model when a rule is not one she may keep at once. The sentence in
# quotes is the one she says, so the user always learns why a card came up.
_TO_CARD = (
    "not kept: this rule could change what you may do or what reaches the user, so it "
    "asks first. Call rule_keep with the same words, and tell the user in one plain "
    "sentence: 'That could change what reaches you, so I've put it on a card.'"
)
_TO_CARD_OUTSIDE = (
    "not kept: something was read from outside this turn, so a rule now must ask first. "
    "Call rule_keep with the same words, and tell the user in one plain sentence that "
    "you've put it on a card."
)


class RuleHistory(Protocol):
    async def append_rule_event(self, owner_id: str, e: RuleEvent) -> None: ...


def _refused(kind: str) -> ToolResult:
    return ToolResult(
        ok=False, content=_REFUSALS.get(kind, "rule failed"), effect="none", error_kind=kind
    )


def _quoted(text: str, limit: int = _PREVIEW) -> str:
    line = " ".join(text.split())
    return "“" + (line if len(line) <= limit else line[: limit - 1] + "…") + "”"


class _RuleTool(BaseTool):
    def __init__(self) -> None:
        self._rules: StandingRules | None = None
        self._owner = ""
        self._history: RuleHistory | None = None
        self._clock: Callable[[], int] | None = None

    def bind(
        self,
        rules: StandingRules,
        owner_id: str,
        history: RuleHistory | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        """Called once at startup with the running process's rules and record."""
        self._rules, self._owner = rules, owner_id
        self._history, self._clock = history, clock

    def _for(self, ctx: ToolContext) -> StandingRules | None:
        return self._rules if self._rules is not None and ctx.owner_id == self._owner else None

    async def _recorded(
        self,
        rule: Rule,
        event: Literal["kept", "dropped"],
        how: Literal["said", "card"],
        ctx: ToolContext,
    ) -> bool:
        """Write the change to the permanent record. False if it could not be written."""
        if self._history is None or self._clock is None:
            return True  # not wired to a record (a unit test): nothing to write
        e = RuleEvent(
            rule_id=rule.id,
            text=rule.text,
            event=event,
            how=how,
            session_id=ctx.session_id,
            at=self._clock(),
        )
        try:
            await self._history.append_rule_event(ctx.owner_id, e)
        except Exception as error:
            logger.warning("recording a rule %s raised %s", event, type(error).__name__)
            return False
        return True

    async def _kept(
        self, rules: StandingRules, text: str, how: Literal["said", "card"], ctx: ToolContext
    ) -> tuple[Rule, bool] | ToolResult:
        """Keep the rule and record it, or neither: (the rule, whether it is new)."""
        before = len(rules.all())
        try:
            rule: Rule = await asyncio.to_thread(rules.keep, text)
        except RuleError as e:
            return _refused(e.kind)
        new = len(rules.all()) > before
        if new and not await self._recorded(rule, "kept", how, ctx):
            # A rule with no record is not kept: the record is how it is answered for.
            try:
                await asyncio.to_thread(rules.drop, rule.id)
            except RuleError:
                logger.warning("a rule kept without its record could not be taken back")
            return _refused("disk")
        return rule, new


class RuleKeep(_RuleTool):
    definition = ToolDefinition(
        name=KEEP,
        description=(
            "Keep a standing rule: something the user told you to always or never do, such "
            "as 'never send an email without reading it to me first'. It stays in front of "
            "you on every turn of every conversation until the user drops it. One rule per "
            "call, in their words, as one sentence. " + ASKS_BY_CARD + " Not for facts or "
            "preferences: those go to memory_write. A plain 'never' that only stops you "
            "doing something goes to rule_never instead, which keeps it at once."
        ),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": MAX_RULE_CHARS}},
            "required": ["text"],
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        category="rules",
        timeout_s=10.0,
        # Undone by dropping it again: a yes is enough.
        recoverable=True,
        # The rule is kept in the user's words: when they said it exactly, on the owner's
        # turn, their words are the yes (SPN-20).
        own_words=("text",),
    )

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text=f"Keep as a standing rule: {_quoted(str(args.get('text', '')))}")

    def target(self, args: JSON) -> str:
        return "rules"

    def fingerprint(self, args: JSON) -> str | None:
        rules = self._rules
        found = rules.find(str(args.get("text", ""))) if rules is not None else None
        return f"{len(rules.all()) if rules else 0}:{found.id if found else 'new'}"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if self._for(ctx) is None:
            return Precondition.failed("rules are not available")
        try:
            clean(str(args.get("text", "")))
        except RuleError as e:
            return Precondition.failed(_REFUSALS[e.kind])
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        rules = self._for(ctx)
        if rules is None:
            return _refused("disk")
        kept = await self._kept(rules, str(args["text"]), "card", ctx)
        if isinstance(kept, ToolResult):
            return kept
        rule, _ = kept
        return ToolResult(
            ok=True,
            content=f"kept {rule.id}: it is in front of you from the next turn on",
            effect="confirmed",
        )


class RuleNever(_RuleTool):
    definition = ToolDefinition(
        name=NEVER,
        description=(
            "Keep a standing rule the user has just said that only stops you doing "
            "something, such as 'never use emojis' or 'don't book anything on Fridays'. It is "
            "kept at once, with no card, and stays in front of you on every turn until the "
            "user drops it. One rule per call, in their words. If the rule could let you do "
            "more, change what reaches the user, or touch deleting, money, sending, secrets "
            "or safety, it is not kept here: the result tells you to call rule_keep instead."
        ),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": MAX_RULE_CHARS}},
            "required": ["text"],
            "additionalProperties": False,
        },
        # It can only narrow what she does (`narrows`), so it needs no card. Undone by
        # dropping it, which does ask.
        risk=Risk.WRITE,
        category="rules",
        timeout_s=10.0,
        recoverable=True,
    )

    def target(self, args: JSON) -> str:
        return "rules"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if self._for(ctx) is None:
            return Precondition.failed("rules are not available")
        try:
            said = clean(str(args.get("text", "")))
        except RuleError as e:
            return Precondition.failed(_REFUSALS[e.kind])
        if ctx.read_outside:
            return Precondition.failed(_TO_CARD_OUTSIDE)
        if not narrows(said):
            return Precondition.failed(_TO_CARD)
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        rules = self._for(ctx)
        if rules is None:
            return _refused("disk")
        text = str(args["text"])
        # Asked again here, so nothing between the check and the write can widen it.
        if ctx.read_outside or not narrows(text):
            return ToolResult(ok=False, content=_TO_CARD, effect="none", error_kind="asks_first")
        kept = await self._kept(rules, text, "said", ctx)
        if isinstance(kept, ToolResult):
            return kept
        rule, new = kept
        already = "" if new else "it was already a standing rule. "
        return ToolResult(
            ok=True,
            content=(
                f"kept {rule.id}: {already}it is in front of you from the next turn on. "
                f"Read it back in one sentence: {read_back(rule.text)}"
            ),
            effect="confirmed",
        )


class RuleList(_RuleTool):
    definition = ToolDefinition(
        name=LIST_RULES,
        description=(
            "List the user's standing rules, numbered, oldest first and newest last, with "
            "each one's id. Use it when the user asks what their rules are, or to find the "
            "rule they mean before dropping one. Read them back in plain words, without the "
            "ids."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category="rules",
        timeout_s=10.0,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        rules = self._for(ctx)
        if rules is None:
            return ToolResult(
                ok=False, content="rules are not available", effect="none", error_kind="disk"
            )
        kept = rules.all()
        if not kept:
            return ToolResult(
                ok=True,
                content="no standing rules. Say so in one sentence: You haven't given me any "
                "standing rules yet.",
                effect="none",
            )
        lines = [f"{i}. [{r.id}] {r.text}" for i, r in enumerate(kept, 1)]
        return ToolResult(
            ok=True,
            content=f"{len(kept)} standing rules, oldest first:\n" + "\n".join(lines),
            effect="none",
        )


class RuleDrop(_RuleTool):
    definition = ToolDefinition(
        name=DROP,
        description=(
            "Drop a standing rule the user no longer wants. Give its id, from your standing "
            "rules or the rule you just kept or listed (for 'drop that rule'), or give words "
            "from it as `about` (for 'drop the rule about X'). If more than one rule has "
            "those words, it is not dropped: ask the user which one. " + ASKS_BY_CARD
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "maxLength": 40},
                "about": {"type": "string", "minLength": 1, "maxLength": 100},
            },
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        category="rules",
        timeout_s=10.0,
        # Undone by keeping it again.
        recoverable=True,
        # "Drop the rule about X", said by the owner: their words are the yes (SPN-20).
        # Never by id: an id is not something anyone says.
        own_words=("about",),
    )

    def _matches(self, args: JSON) -> list[Rule]:
        rules = self._rules
        if rules is None:
            return []
        if "about" in args:
            return rules.about(str(args.get("about", "")))
        rule = rules.get(str(args.get("id", "")))
        return [rule] if rule is not None else []

    def _rule(self, args: JSON) -> Rule | None:
        found = self._matches(args)
        return found[0] if len(found) == 1 else None

    def describe(self, args: JSON) -> ActionSummary:
        rule = self._rule(args)
        if rule is None:
            return ActionSummary(text=f"Drop standing rule {str(args.get('id', ''))[:40]}")
        return ActionSummary(text=f"Stop following the standing rule: {_quoted(rule.text)}")

    def target(self, args: JSON) -> str:
        return "rules"

    def fingerprint(self, args: JSON) -> str | None:
        rule = self._rule(args)
        return rule.id if rule is not None else "absent"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if self._for(ctx) is None:
            return Precondition.failed("no such rule")
        if ("id" in args) == ("about" in args):
            return Precondition.failed("give the rule's id or words from it as about, not both")
        found = self._matches(args)
        if not found:
            if "about" in args:
                return Precondition.failed(
                    "no standing rule has those words: call rule_list and ask the user which"
                )
            return Precondition.failed("no such rule")
        if len(found) > 1:
            shown = "; ".join(f"[{r.id}] {_quoted(r.text, _CHOICE_PREVIEW)}" for r in found[:3])
            more = f" and {len(found) - 3} more" if len(found) > 3 else ""
            return Precondition.failed(
                f"not dropped: {len(found)} rules match, so ask the user which one: {shown}{more}"
            )
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        rules = self._for(ctx)
        rule = self._rule(args)
        if rules is None:
            return _refused("disk")
        if rule is None:
            return _refused("not_found")
        try:
            gone = await asyncio.to_thread(rules.drop, rule.id)
        except RuleError as e:
            return _refused(e.kind)
        if not await self._recorded(gone, "dropped", "card", ctx):
            # A drop with no record is not done: the rule goes back as it was.
            try:
                await asyncio.to_thread(rules.keep, gone.text)
            except RuleError:
                logger.warning("a rule dropped without its record could not be put back")
            return _refused("disk")
        return ToolResult(
            ok=True,
            content=(
                f"dropped {gone.id}. Read it back in one sentence: Done, I've dropped the rule "
                f"{_quoted(gone.text)}."
            ),
            effect="confirmed",
        )
