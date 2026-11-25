"""A turn's clock for a model of the user's own (the live Ollama job's finding).

The loop guard stops a turn that has run too long (SPN-7). On a computer with no GPU a
local model takes minutes to read her first request, so with a hosted model's two
minutes every tool she asked for was refused before it could run: "remember this"
saved nothing and "create a file" raised no card. A model on her own network gets as
long as the page waits for her.
"""

from __future__ import annotations

from trillion.brain import config
from trillion.brain.config import ModelSettings
from trillion.brain.types import ModelProvider
from trillion.secrets.store import MemoryStore
from trillion.spine.loop_guard import Limits, LoopGuard, StopReason, Verdict


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def provider(url: str) -> ModelProvider:
    return config.build(ModelSettings(kind="custom", model="m", base_url=url), MemoryStore())


def first_call_after(seconds: float, limits: Limits) -> Verdict:
    clock = Clock()
    guard = LoopGuard(limits, clock=clock)
    clock.now = seconds  # the model's first reply, then her first tool call
    return guard.check_call("memory_write", "h").verdict


def test_a_hosted_models_turn_keeps_the_usual_clock() -> None:
    limits = config.turn_limits(provider("https://api.example.com/v1"))
    assert limits == Limits()
    assert first_call_after(200, limits) is Verdict.STOP


def test_a_local_model_has_time_to_read_her_before_her_first_tool() -> None:
    limits = config.turn_limits(provider("http://localhost:11434/v1"))
    assert limits.max_seconds == config.LOCAL_TURN_S
    # What CI saw: about four minutes before her first call, on four CPUs.
    assert first_call_after(240, limits) is Verdict.OK
    # Everything else about the guard is as before: only the clock is longer.
    assert (limits.max_rounds, limits.max_cost_usd) == (Limits().max_rounds, Limits().max_cost_usd)


def test_a_model_on_her_network_counts_as_her_own() -> None:
    limits = config.turn_limits(provider("http://192.168.1.20:11434/v1"))
    assert limits.max_seconds == config.LOCAL_TURN_S


def test_a_stuck_local_turn_still_stops() -> None:
    limits = config.turn_limits(provider("http://localhost:11434/v1"))
    guard = LoopGuard(limits, clock=(clock := Clock()))
    clock.now = config.LOCAL_TURN_S + 1
    assert guard.check_call("memory_write", "h").reason is StopReason.WALL_CLOCK


def test_something_that_is_not_a_model_with_an_address_keeps_the_usual_clock() -> None:
    assert config.turn_limits(object()) == Limits()
