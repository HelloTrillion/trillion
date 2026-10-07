"""Anthropic, through the official SDK (BRN-2). The default and recommended provider.

Three rules of the current API shape this adapter:

- **Assistant turns go back exactly as they came.** A reply can carry signed
  reasoning blocks that are valid only if returned unchanged, in a conversation
  whose system prompt, tools and earlier messages are byte-identical. The full
  content is kept as `ProviderRaw` and replayed; `prefix_key` says what it was
  produced under, and a turn produced under anything else is replayed as plain
  text and tool calls instead.
- **Nothing is forced or tuned.** No `thinking`, effort or sampling parameters
  are sent, so the request is valid on every current model and each model runs
  at its own default.
- **A refusal is a normal response.** It is reported as a stop reason, never
  read as an empty success.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from trillion.brain.types import (
    ContextBlock,
    Done,
    ModelChoice,
    ModelEvent,
    Msg,
    Pricing,
    ProviderError,
    ProviderRaw,
    StopReason,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUse,
    ToolUseBlock,
    Usage,
)

NAME = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"
# Streaming, so a long reply cannot hit an HTTP timeout; reasoning counts toward this.
MAX_OUTPUT = 64_000
_CACHED = {"type": "ephemeral"}
_STOP: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}
# US dollars per million tokens, from Anthropic's published first-party rates as of
# 2026-09-25. A model missing here has no known price, and its cost is reported as
# unknown rather than guessed. Cache writes bill at 1.25x input.
_PRICES: dict[str, tuple[float, float, float]] = {  # input, output, cache read
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}


def prefix_key(model: str, system: Sequence[SystemBlock], tools: Sequence[ToolSchema]) -> str:
    """What a reply's reasoning is bound to, besides the messages before it."""
    material = json.dumps(
        {
            "model": model,
            "system": [s.text for s in system],
            "tools": sorted(
                (t.name, t.description, json.dumps(dict(t.input_schema), sort_keys=True))
                for t in tools
            ),
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _neutral(m: Msg) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in m.blocks:
        if isinstance(b, TextBlock | ContextBlock):
            out.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolUseBlock):
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.args)})
        elif isinstance(b, ToolResultBlock):
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content,
                    "is_error": b.is_error,
                }
            )
    return out


def wire_messages(messages: Sequence[Msg], key: str) -> list[dict[str, Any]]:
    """The conversation as sent. Raw turns are replayed only as an unbroken run up to
    the newest message: reasoning may be dropped from the front of a history, never
    from the middle."""
    start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role != "assistant":
            continue
        if m.raw is None or m.raw.provider != NAME or m.raw.prefix_key != key:
            break
        start = i
    return [
        {
            "role": m.role,
            "content": [dict(b) for b in m.raw.content]
            if i >= start and m.role == "assistant" and m.raw is not None
            else _neutral(m),
        }
        for i, m in enumerate(messages)
    ]


def _fail(e: anthropic.APIError) -> ProviderError:
    """A kind that is safe to show. Never the message: it can echo the request."""
    if isinstance(e, anthropic.APITimeoutError):
        return ProviderError("timeout", type(e).__name__)
    if isinstance(e, anthropic.APIConnectionError):
        return ProviderError("unreachable", type(e).__name__)
    if isinstance(e, anthropic.AuthenticationError):
        return ProviderError("unauthorized", type(e).__name__)
    if isinstance(e, anthropic.PermissionDeniedError):
        return ProviderError("forbidden", type(e).__name__)
    if isinstance(e, anthropic.NotFoundError):
        return ProviderError("not_found", type(e).__name__)
    if isinstance(e, anthropic.RateLimitError):
        return ProviderError("rate_limited", type(e).__name__)
    if isinstance(e, anthropic.APIStatusError):
        kind = "provider_unavailable" if e.status_code >= 500 else "bad_request"
        return ProviderError(kind, f"HTTP {e.status_code}")
    return ProviderError("provider_error", type(e).__name__)


def _client(api_key: str, http_client: Any = None) -> AsyncAnthropic:
    # One retry: a person is waiting, and the loop reports a failure plainly.
    return AsyncAnthropic(api_key=api_key, max_retries=1, http_client=http_client)


class AnthropicProvider:
    name = NAME

    def __init__(
        self,
        *,
        api_key: Callable[[], str],
        model: str = DEFAULT_MODEL,
        max_output: int | None = None,
        http_client: Any = None,
    ) -> None:
        # The key is fetched for each request and never held (SEC-6).
        self._api_key = api_key
        self.model = model
        self._max_output = min(MAX_OUTPUT, max_output or MAX_OUTPUT)
        self._http_client = http_client

    def pricing(self) -> Pricing | None:
        rates = _PRICES.get(self.model)
        if rates is None:
            return None
        return Pricing(
            input=rates[0], output=rates[1], cache_read=rates[2], cache_write=rates[0] * 1.25
        )

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        key = prefix_key(self.model, system, tools)
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_output,
            "system": [
                {"type": "text", "text": s.text} | ({"cache_control": _CACHED} if s.cache else {})
                for s in system
            ],
            "messages": wire_messages(messages, key),
        }
        if tools:
            request["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
                for t in tools
            ]
        try:
            client = _client(self._api_key(), self._http_client)
            async with client.messages.stream(**request) as stream:
                async for event in stream:
                    if event.type == "text" and event.text:
                        yield TextDelta(text=event.text)
                final = await stream.get_final_message()
        except anthropic.APIError as e:
            raise _fail(e) from e
        except ValueError as e:  # the SDK could not parse what was streamed
            raise ProviderError("bad_stream", type(e).__name__) from e

        content = [block.to_dict() for block in final.content]
        for block in final.content:
            if block.type == "tool_use":
                args = block.input
                ok = isinstance(args, dict)
                yield ToolUse(
                    id=block.id,
                    name=block.name,
                    args=args if isinstance(args, dict) else {},
                    invalid=None if ok else "arguments were not a JSON object",
                )
        u = final.usage
        yield Done(
            stop_reason=_STOP.get(str(final.stop_reason), "other"),
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cache_write_tokens=u.cache_creation_input_tokens or 0,
            ),
            raw=ProviderRaw(provider=NAME, prefix_key=key, content=tuple(content)),
        )


async def list_models(api_key: str, http_client: Any = None) -> list[ModelChoice]:
    """The models this key can use. Free to call, so it doubles as the key check."""
    try:
        found = [
            ModelChoice(id=m.id, label=m.display_name or m.id, max_output=m.max_tokens)
            async for m in _client(api_key, http_client).models.list(limit=100)
        ]
    except anthropic.APIError as e:
        raise _fail(e) from e
    return found
