"""Standing rules (BRN-5): keep one, or drop one. Both ask the user first.

A kept rule goes into the system block on every turn, where nothing that
shortens the conversation can reach it. That is why keeping one asks: a page or a
file she read must not be able to slip a rule in. A rule can only make her more
careful. The spine's gates are in code, and no rule loosens them.
"""

from __future__ import annotations

import asyncio

from trillion.brain.standing import MAX_RULE_CHARS, Rule, RuleError, StandingRules, clean
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

KEEP, DROP = "rule_keep", "rule_drop"
_PREVIEW = 160
_REFUSALS = {
    "empty": "there was no rule to keep",
    "too_long": f"too long for a rule: keep it under {MAX_RULE_CHARS} characters",
    "full": "there are already as many standing rules as she can hold; drop one first",
    "not_found": "no such rule",
    "disk": "the rules file could not be written",
}


def _refused(kind: str) -> ToolResult:
    return ToolResult(
        ok=False, content=_REFUSALS.get(kind, "rule failed"), effect="none", error_kind=kind
    )


def _quoted(text: str) -> str:
    line = " ".join(text.split())
    return "“" + (line if len(line) <= _PREVIEW else line[: _PREVIEW - 1] + "…") + "”"


class _RuleTool(BaseTool):
    def __init__(self) -> None:
        self._rules: StandingRules | None = None
        self._owner = ""

    def bind(self, rules: StandingRules, owner_id: str) -> None:
        """Called once at startup with the running process's rules."""
        self._rules, self._owner = rules, owner_id

    def _for(self, ctx: ToolContext) -> StandingRules | None:
        return self._rules if self._rules is not None and ctx.owner_id == self._owner else None


class RuleKeep(_RuleTool):
    definition = ToolDefinition(
        name=KEEP,
        description=(
            "Keep a standing rule: something the user told you to always or never do, such "
            "as 'never send an email without reading it to me first'. It stays in front of "
            "you on every turn of every conversation until the user drops it. One rule per "
            "call, in their words, as one sentence. " + ASKS_BY_CARD + " Not for facts or "
            "preferences: those go to memory_write."
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
        try:
            rule: Rule = await asyncio.to_thread(rules.keep, str(args["text"]))
        except RuleError as e:
            return _refused(e.kind)
        return ToolResult(
            ok=True,
            content=f"kept {rule.id}: it is in front of you from the next turn on",
            effect="confirmed",
        )


class RuleDrop(_RuleTool):
    definition = ToolDefinition(
        name=DROP,
        description=(
            "Drop a standing rule the user no longer wants, by its id from your standing "
            "rules. " + ASKS_BY_CARD
        ),
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string", "maxLength": 40}},
            "required": ["id"],
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        category="rules",
        timeout_s=10.0,
        recoverable=True,
    )

    def _rule(self, args: JSON) -> Rule | None:
        rules = self._rules
        return rules.get(str(args.get("id", ""))) if rules is not None else None

    def describe(self, args: JSON) -> ActionSummary:
        rule = self._rule(args)
        if rule is None:
            return ActionSummary(text=f"Drop standing rule {str(args.get('id', ''))[:40]}")
        return ActionSummary(text=f"Stop following the standing rule: {_quoted(rule.text)}")

    def target(self, args: JSON) -> str:
        return "rules"

    def fingerprint(self, args: JSON) -> str | None:
        return "present" if self._rule(args) is not None else "absent"

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if self._for(ctx) is None or self._rule(args) is None:
            return Precondition.failed("no such rule")
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        rules = self._for(ctx)
        if rules is None:
            return _refused("disk")
        try:
            gone = await asyncio.to_thread(rules.drop, str(args["id"]))
        except RuleError as e:
            return _refused(e.kind)
        return ToolResult(ok=True, content=f"dropped {gone.id}", effect="confirmed")
