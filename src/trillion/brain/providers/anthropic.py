"""Anthropic, through the official SDK (BRN-2). The default and recommended provider.

Three rules of the current API shape this adapter:

- **Assistant turns go back exactly as they came.** A reply can carry signed
  reasoning blocks that are valid only if returned unchanged, in a conversation
  whose system prompt, tools and earlier messages are byte-identical. The full
  content is kept as `ProviderRaw` and replayed; `prefix_key` says what it was
  produced under, and a turn produced under anything else is replayed as plain
  text and tool calls instead.
- **Nothing is forced or tuned, but for a spoken conversation.** No sampling
  parameters are sent, and with her voice off no `thinking` or effort either, so
  each model runs at its own default. While her replies are spoken (`quick`, set
  for the whole session, typed turns included, so the cached prefix survives a
  switch between typing and talking), she asks for the least reasoning that is
  still recommended (`_QUICK`). A model not listed there gets its defaults, since
  a guess could be refused. Earlier turns' signed reasoning is still replayed
  unchanged: the API reads or drops it as the setting needs, and the setting
  never changes within a turn.
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
from trillion.spend import prices

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
# What a session whose replies are spoken adds to the request, by model, so her first
# word comes sooner. The lowest effort, with adaptive thinking left on, as Anthropic
# recommends: with thinking off, Claude Opus 5 can write a tool call as visible text
# or leak internal tags, which would be read aloud, and Claude Sonnet 5.5 and Haiku
# 5.5 are documented to do better on low effort than with thinking off. Claude Sonnet
# 5 alone has thinking turned off ("disabled", valid at its default effort): it has
# no such failure documented, only fewer tool calls. Models that do not think unless
# asked are not listed: they already answer without it.
_OFF: dict[str, Any] = {"thinking": {"type": "disabled"}}
_LOW: dict[str, Any] = {"output_config": {"effort": "low"}}
_QUICK: dict[str, dict[str, Any]] = {
    "claude-sonnet-5": _OFF,
    "claude-sonnet-5-5": _LOW,
    "claude-opus-5": _LOW,
    "claude-haiku-5-5": _LOW,
    "claude-opus-5-5": _LOW,
    "claude-fable-5": _LOW,
    "claude-fable-5-1": _LOW,
}


def quick_params(model: str) -> dict[str, Any]:
    """What a spoken turn sends on top of the request for this model, if anything."""
    return _QUICK.get(re.sub(r"-\d{8}$", "", model), {})


def prefix_key(
    model: str,
    system: Sequence[SystemBlock],
    tools: Sequence[ToolSchema],
    first: Msg | None = None,
) -> str:
    """What a reply's reasoning is bound to: the model, the instructions, the tools,
    and the messages before it. History is only appended to, except at a compaction,
    which drops the oldest messages and puts the summary in the first kept one; so
    the first message sent stands for the rest. A turn kept through a compaction no
    longer matches and goes out as plain text and tool calls (BRN-5)."""
    material = json.dumps(
        {
            "model": model,
            "first": _neutral(first) if first is not None else None,
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


def _usage(u: Any) -> Usage:
    return Usage(
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read_tokens=u.cache_read_input_tokens or 0,
        cache_write_tokens=u.cache_creation_input_tokens or 0,
    )


def _client(api_key: str, http_client: Any = None) -> AsyncAnthropic:
    # One retry: a person is waiting, and the loop reports a failure plainly.
    return AsyncAnthropic(api_key=api_key, max_retries=1, http_client=http_client)


class AnthropicProvider:
    name = NAME
    # `stream` takes `on_usage`, told the usage so far as the reply goes: the prompt's
    # tokens when it starts, so a reply stopped before its end is still counted.
    reports_usage_so_far = True

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
        # US dollars per million tokens, from the one price table. A model missing
        # there has no known price, and its cost is reported as unknown, not guessed.
        model = re.sub(r"-\d{8}$", "", self.model)  # a dated id prices like its alias
        rate = prices.TABLE.get(("anthropic", model))
        return rate.pricing() if rate else None

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        quick: bool = False,
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[ModelEvent]:
        key = prefix_key(self.model, system, tools, messages[0] if messages else None)
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
        if quick:
            request.update(quick_params(self.model))
        if tools:
            request["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
                for t in tools
            ]
        try:
            client = _client(self._api_key(), self._http_client)
            async with client.messages.stream(**request) as stream:
                async for event in stream:
                    if on_usage is not None and event.type in ("message_start", "message_delta"):
                        on_usage(_usage(stream.current_message_snapshot.usage))
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
        yield Done(
            stop_reason=_STOP.get(str(final.stop_reason), "other"),
            usage=_usage(final.usage),
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
