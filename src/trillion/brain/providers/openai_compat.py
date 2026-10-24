"""Any OpenAI-compatible chat endpoint, streamed (BRN-2).

Covers OpenRouter, Ollama, LM Studio and vLLM, which is what makes rung 1 of
the capability ladder free: a local model and no key at all.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx

from trillion import net
from trillion.brain import sse
from trillion.brain.types import (
    OPENING,
    ContextBlock,
    Done,
    ModelChoice,
    ModelEvent,
    Msg,
    Pricing,
    ProviderError,
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

_STOP: dict[str, StopReason] = {
    "stop": "end",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
}


def _messages(system: Sequence[SystemBlock], messages: Sequence[Msg]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": "\n\n".join(s.text for s in system)})
    for m in messages:
        text = "\n\n".join(b.text for b in m.blocks if isinstance(b, TextBlock | ContextBlock))
        if m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(dict(b.args))},
                }
                for b in m.blocks
                if isinstance(b, ToolUseBlock)
            ]
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        # Tool results first: this API wants them directly after the assistant's calls.
        out.extend(
            {"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content}
            for b in m.blocks
            if isinstance(b, ToolResultBlock)
        )
        if text:
            out.append({"role": "user", "content": text})
    return _alternating(out)


def _alternating(out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Notices are her messages saved outside a turn, so two of hers can sit side by
    side, and one can open a conversation; many local chat templates refuse both.
    Hers that touch go as one message, and a conversation she opened starts with a
    short placeholder from the user, which nobody sees."""
    merged: list[dict[str, Any]] = []
    for m in out:
        last = merged[-1] if merged else None
        if last is not None and m["role"] == "assistant" == last["role"]:
            text = "\n\n".join(t for t in (last["content"], m["content"]) if t)
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            calls = [*last.get("tool_calls", []), *m.get("tool_calls", [])]
            if calls:
                entry["tool_calls"] = calls
            merged[-1] = entry
        else:
            merged.append(m)
    first = 1 if merged and merged[0]["role"] == "system" else 0
    if len(merged) > first and merged[first]["role"] == "assistant":
        merged.insert(first, {"role": "user", "content": OPENING})
    return merged


def request_body(
    *,
    model: str,
    system: Sequence[SystemBlock],
    messages: Sequence[Msg],
    tools: Sequence[ToolSchema],
    max_tokens: int | None = None,
    cap_field: str = "max_tokens",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": _messages(system, messages),
    }
    if max_tokens is not None:
        body[cap_field] = max_tokens
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": dict(t.input_schema),
                },
            }
            for t in tools
        ]
    return body


class OpenAICompatProvider:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: Callable[[], str | None] = lambda: None,
        cap_field: str = "max_tokens",
        max_output: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        # No cap unless one is known for this model. On these services the cap also
        # counts a reasoning model's thinking, so a small fixed number can be spent
        # before the first word of the reply; and a number above a model's own limit
        # is refused outright. Left out, each server applies the model's own limit.
        self._max_output = max_output
        # OpenAI's own API renamed the output cap; local servers still use the old name.
        self._cap_field = cap_field
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = client or httpx.AsyncClient(timeout=sse.TIMEOUT, limits=net.KEEP_ALIVE)

    async def warm(self) -> None:
        """Open the connection before the next request needs it. Sends nothing."""
        await net.warm(self._client, self._url)

    def pricing(self) -> Pricing | None:
        return None

    async def stream(
        self,
        *,
        system: Sequence[SystemBlock],
        messages: Sequence[Msg],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        headers = {"content-type": "application/json"}
        key = self._api_key()
        if key:
            headers["authorization"] = f"Bearer {key}"
        body = request_body(
            model=self.model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=self._max_output,
            cap_field=self._cap_field,
        )
        usage = Usage()
        stop: StopReason = "other"
        # index -> [id, name, accumulated JSON text]
        calls: dict[int, list[str]] = {}
        async for _, data in sse.events(self._client, self._url, headers=headers, body=body):
            if data.strip() == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except ValueError as e:
                raise ProviderError("bad_stream", "event was not JSON") from e
            if isinstance(ev.get("error"), dict):
                raise ProviderError(str(ev["error"].get("type") or "provider_error"))
            if isinstance(ev.get("usage"), dict):
                usage = _usage(ev["usage"])
            for choice in ev.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield TextDelta(text=str(delta["content"]))
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(int(tc.get("index", 0)), ["", "", ""])
                    fn = tc.get("function") or {}
                    slot[0] = slot[0] or str(tc.get("id") or "")
                    slot[1] = slot[1] or str(fn.get("name") or "")
                    slot[2] += str(fn.get("arguments") or "")
                if choice.get("finish_reason"):
                    stop = _STOP.get(str(choice["finish_reason"]), "other")
        for index in sorted(calls):
            call_id, name, raw = calls[index]
            args = sse.parse_object(raw)
            yield ToolUse(
                # Some local servers omit ids. A made-up one must still be unique across
                # the whole conversation: ids are saved and replayed on later turns.
                id=call_id or f"call_{uuid.uuid4().hex[:16]}",
                name=name,
                args=args or {},
                invalid=None if args is not None else "arguments were not a JSON object",
            )
        # A reply cut off at the length limit stays "max_tokens" even with tool calls
        # in it: the last one may be half-written, and the loop runs none of them.
        if calls and stop != "max_tokens":
            stop = "tool_use"
        yield Done(stop_reason=stop, usage=usage)


def _usage(raw: dict[str, Any]) -> Usage:
    def n(key: str) -> int:
        value = raw.get(key)
        return value if isinstance(value, int) else 0

    details = raw.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    cached = cached if isinstance(cached, int) else 0
    return Usage(
        input_tokens=max(0, n("prompt_tokens") - cached),
        output_tokens=n("completion_tokens"),
        cache_read_tokens=cached,
    )


async def list_models(
    base_url: str, api_key: str | None = None, client: httpx.AsyncClient | None = None
) -> list[ModelChoice]:
    """The models this endpoint offers. Free to call, so it doubles as the key check."""
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    http = client or httpx.AsyncClient(timeout=sse.TIMEOUT)
    try:
        r = await http.get(base_url.rstrip("/") + "/models", headers=headers)
    except httpx.TimeoutException as e:
        raise ProviderError("timeout", type(e).__name__) from e
    except httpx.HTTPError as e:
        raise ProviderError("unreachable", type(e).__name__) from e
    except httpx.InvalidURL as e:
        raise ProviderError("bad_url", type(e).__name__) from e
    finally:
        if client is None:
            await http.aclose()
    if r.status_code != 200:
        raise ProviderError(sse.status_kind(r.status_code), f"HTTP {r.status_code}")
    try:
        rows = r.json().get("data") or []
        ids = sorted(str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id"))
    except (ValueError, AttributeError) as e:
        raise ProviderError("bad_response", type(e).__name__) from e
    return [ModelChoice(id=i, label=i) for i in ids]
