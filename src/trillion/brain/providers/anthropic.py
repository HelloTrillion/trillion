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

The conversation is cached as well as the instructions (PER-5). History is only
ever appended to and replays byte for byte, so each request begins with the one
before it: marking the end of the newest message lets the next request read all of
it from the cache instead of paying for it again.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import anthropic
import httpx2
from anthropic import AsyncAnthropic

from trillion import net
from trillion.brain.types import (
    OPENING,
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
# 2026-10-22. A model missing here has no known price, and its cost is reported as
# unknown rather than guessed. Cache writes bill at 1.25x input.
_PRICES: dict[str, tuple[float, float, float]] = {  # input, output, cache read
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-opus-4-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}
# Rates for a request whose prompt (input + cache reads + cache writes) is over the
# threshold; the whole request is billed at these.
_LONG_PROMPT_OVER = 100_000
_LONG_PROMPT_PRICES: dict[str, tuple[float, float, float]] = {
    "claude-haiku-5-5": (0.50, 2.50, 0.05),
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


_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _tool_id(raw: str) -> str:
    """A tool-call id another provider issued, in the characters this API accepts.
    The same mapping is applied to the call and to its result, so they still pair."""
    return _ID_UNSAFE.sub("_", raw) or "call"


def _neutral(m: Msg) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in m.blocks:
        if isinstance(b, TextBlock | ContextBlock):
            out.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolUseBlock):
            out.append(
                {"type": "tool_use", "id": _tool_id(b.id), "name": b.name, "input": dict(b.args)}
            )
        elif isinstance(b, ToolResultBlock):
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": _tool_id(b.tool_use_id),
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
    return _alternating(
        [
            {
                "role": m.role,
                "content": [dict(b) for b in m.raw.content]
                if i >= start and m.role == "assistant" and m.raw is not None
                else _neutral(m),
            }
            for i, m in enumerate(messages)
        ]
    )


def _alternating(wire: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Notices are her messages saved outside a turn, so two of hers can sit side by
    side, and one can open a conversation. Hers that touch go as one message, in
    order, each block as it was; a conversation she opened starts with a short
    placeholder from the user, which nobody sees."""
    out: list[dict[str, Any]] = []
    for m in wire:
        if out and m["role"] == "assistant" and out[-1]["role"] == "assistant":
            out[-1] = {"role": "assistant", "content": [*out[-1]["content"], *m["content"]]}
        else:
            out.append(m)
    if out and out[0]["role"] == "assistant":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": OPENING}]})
    return out


# The API allows four cache breakpoints in all; the instructions take one.
HISTORY_BREAKPOINTS = 2


def cache_history(wire: list[dict[str, Any]]) -> None:
    """Mark the end of the newest user messages as cacheable, in place.

    The newest is what the next request will start with. The one before it is where
    the last request wrote, so a turn that added many blocks since (a run of tool
    calls) still finds it: the cache only looks a short way back from a breakpoint.
    Only user messages are marked. They are built here, never replayed from the
    provider, so her signed reasoning is never touched."""
    marked = 0
    for message in reversed(wire):
        if marked == HISTORY_BREAKPOINTS:
            return
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list) or not content:
            continue
        content[-1] = {**content[-1], "cache_control": _CACHED}
        marked += 1


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
        # The key is fetched for each request and never held (SEC-6). The connections
        # are kept: a new one for every request cost a TLS handshake every turn.
        self._api_key = api_key
        self.model = model
        self._max_output = min(MAX_OUTPUT, max_output or MAX_OUTPUT)
        self._http_client = http_client or anthropic.DefaultAsyncHttpxClient(
            limits=httpx2.Limits(
                max_connections=20, max_keepalive_connections=10, keepalive_expiry=net.IDLE_S
            )
        )

    async def warm(self) -> None:
        """Open the connection before the next request needs it. Sends nothing."""
        await net.warm(self._http_client, str(_client("", self._http_client).base_url))

    def pricing(self) -> Pricing | None:
        model = re.sub(r"-\d{8}$", "", self.model)  # a dated id prices like its alias
        if (rates := _PRICES.get(model)) is None:
            return None
        long = _LONG_PROMPT_PRICES.get(model)
        return Pricing(
            input=rates[0],
            output=rates[1],
            cache_read=rates[2],
            cache_write=rates[0] * 1.25,
            long_prompt_over=_LONG_PROMPT_OVER if long else 0,
            long_prompt=long
            and Pricing(
                input=long[0], output=long[1], cache_read=long[2], cache_write=long[0] * 1.25
            ),
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
        cache_history(request["messages"])
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
            model=str(final.model or ""),
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
