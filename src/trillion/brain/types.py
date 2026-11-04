"""Provider-neutral shapes for a conversation with a model (BRN-1).

Adapters translate these to and from each provider's wire format; nothing
outside `brain/providers/` knows what a provider's JSON looks like.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Role = Literal["user", "assistant"]
StopReason = Literal["end", "tool_use", "max_tokens", "refusal", "other"]


@dataclass(frozen=True, kw_only=True)
class TextBlock:
    text: str


@dataclass(frozen=True, kw_only=True)
class ContextBlock:
    """Host-written context that rides on a user turn (the time, what is waiting).

    The model reads it as text. It is saved with the message, never rebuilt: a
    history that is edited after the fact invalidates the model's own reasoning
    (and the prompt cache), so every request replays earlier turns byte for byte."""

    text: str


@dataclass(frozen=True, kw_only=True)
class ToolUseBlock:
    id: str
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True, kw_only=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


Block = TextBlock | ContextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True, kw_only=True)
class ProviderRaw:
    """An assistant turn exactly as its provider returned it.

    Some providers return blocks that must be passed back unchanged, such as
    signed reasoning. The neutral blocks are what Trillion shows and what any
    other provider is sent; this is what the same provider is sent, for as long
    as `prefix_key` (the system prompt, tool set and model it was produced
    under) still matches."""

    provider: str
    prefix_key: str
    content: tuple[Mapping[str, Any], ...]


# Sent, never shown or spoken, before a conversation that opens with her message: a
# notice can arrive before the user has said anything, and providers want the user
# to speak first.
OPENING = "(Nothing from the user yet: the next message is yours, sent on your own.)"


@dataclass(frozen=True, kw_only=True)
class Msg:
    role: Role
    blocks: tuple[Block, ...]
    raw: ProviderRaw | None = None


@dataclass(frozen=True, kw_only=True)
class SystemBlock:
    text: str
    # The stable part of the prompt is marked cacheable; volatile context rides on
    # the user turn instead (PER-5).
    cache: bool = False


@dataclass(frozen=True, kw_only=True)
class ToolSchema:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, kw_only=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # US dollars the provider itself says this call cost (OpenRouter does), or None
    # when it does not say. It wins over the price table (see trillion.spend.prices).
    reported_cost: float | None = None

    def __add__(self, other: Usage) -> Usage:
        costs = [c for c in (self.reported_cost, other.reported_cost) if c is not None]
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reported_cost=sum(costs) if costs else None,
        )


@dataclass(frozen=True, kw_only=True)
class Pricing:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0
    long_prompt_over: int = 0
    long_prompt: Pricing | None = None

    def cost(self, u: Usage) -> float:
        # Haiku 5.5 bills the whole request at the higher rate once its prompt passes the line.
        if self.long_prompt and (
            u.input_tokens + u.cache_read_tokens + u.cache_write_tokens > self.long_prompt_over
        ):
            return self.long_prompt.cost(u)
        return (
            u.input_tokens * self.input
            + u.output_tokens * self.output
            + u.cache_read_tokens * self.cache_read
            + u.cache_write_tokens * self.cache_write
        ) / 1_000_000


@dataclass(frozen=True, kw_only=True)
class TextDelta:
    text: str


@dataclass(frozen=True, kw_only=True)
class ToolUse:
    id: str
    name: str
    args: Mapping[str, Any]
    # Set when the model's arguments were not a JSON object. The call is answered
    # with a refusal and never reaches a tool.
    invalid: str | None = None


@dataclass(frozen=True, kw_only=True)
class Done:
    stop_reason: StopReason
    usage: Usage
    raw: ProviderRaw | None = None
    # The model the provider says answered, or "" when it does not say. The persona
    # gate fails a run that was answered by a model other than the one it names (EVL-4).
    model: str = ""


ModelEvent = TextDelta | ToolUse | Done


class ProviderError(Exception):
    """A provider failed. `kind` is safe to show and to log; the message may not be."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


@dataclass(frozen=True, kw_only=True)
class ModelChoice:
    """One model a provider offers, for the settings list."""

    id: str
    label: str
    max_output: int | None = None


class ModelProvider(Protocol):
    name: str
    model: str

    def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        quick: bool = False,
    ) -> AsyncIterator[ModelEvent]:
        """One model call. `quick`: a spoken turn, where her first word matters more
        than depth. A provider may answer it with less reasoning; one with nothing
        to tune ignores it."""
        ...

    def pricing(self) -> Pricing | None:
        """None when the price of this model is not known: cost is then not reported."""
        ...
