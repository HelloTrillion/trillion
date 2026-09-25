"""SPN-7: stop a loop that is busy but not making progress, or that has run too long or too dear."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

REPEAT_WARN, REPEAT_STOP = 3, 5
FAIL_WARN, FAIL_STOP = 3, 8


class Verdict(StrEnum):
    OK = "ok"
    WARN = "warn"
    STOP = "stop"


class StopReason(StrEnum):
    REPEAT = "repeat"
    FAILING_TOOL = "failing_tool"
    ROUNDS = "rounds"
    WALL_CLOCK = "wall_clock"
    COST = "cost"


@dataclass(frozen=True)
class GuardDecision:
    verdict: Verdict
    reason: StopReason | None = None


@dataclass(frozen=True)
class Limits:
    max_rounds: int = 10
    max_seconds: float = 120.0
    max_cost_usd: float = 1.0


_OK = GuardDecision(Verdict.OK)


class LoopGuard:
    """One per agent loop. Pure apart from the injected monotonic clock."""

    def __init__(
        self, limits: Limits | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limits = limits or Limits()
        self._clock = clock
        self._start = clock()
        self._rounds = 0
        self._cost = 0.0
        self._repeats: Counter[tuple[str, str]] = Counter()
        self._failures: Counter[str] = Counter()

    def next_round(self) -> GuardDecision:
        self._rounds += 1
        if self._rounds > self._limits.max_rounds:
            return GuardDecision(Verdict.STOP, StopReason.ROUNDS)
        return _OK

    def check_call(self, tool: str, args_hash: str) -> GuardDecision:
        if self._clock() - self._start > self._limits.max_seconds:
            return GuardDecision(Verdict.STOP, StopReason.WALL_CLOCK)
        key = (tool, args_hash)
        self._repeats[key] += 1
        n = self._repeats[key]
        if n >= REPEAT_STOP:
            return GuardDecision(Verdict.STOP, StopReason.REPEAT)
        if n >= REPEAT_WARN:
            return GuardDecision(Verdict.WARN, StopReason.REPEAT)
        return _OK

    def record_result(self, tool: str, *, ok: bool) -> GuardDecision:
        if ok:
            return _OK
        self._failures[tool] += 1
        n = self._failures[tool]
        if n >= FAIL_STOP:
            return GuardDecision(Verdict.STOP, StopReason.FAILING_TOOL)
        if n >= FAIL_WARN:
            return GuardDecision(Verdict.WARN, StopReason.FAILING_TOOL)
        return _OK

    def add_cost(self, usd: float) -> GuardDecision:
        self._cost += usd
        if self._cost > self._limits.max_cost_usd:
            return GuardDecision(Verdict.STOP, StopReason.COST)
        return _OK
