"""Any OpenAI-compatible chat endpoint, streamed (BRN-2).

Covers OpenRouter, Ollama, LM Studio and vLLM, which is what makes rung 1 of
the capability ladder free: a local model and no key at all.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx

from trillion.brain import sse
from trillion.brain.types import (
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
    return out


def request_body(
    *,
    model: str,
    system: Sequence[SystemBlock],
    messages: Sequence[Msg],
    tools: Sequence[ToolSchema],
    max_tokens: int,
    cap_field: str = "max_tokens",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        cap_field: max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": _messages(system, messages),
    }
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
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        # OpenAI's own API renamed the output cap; local servers still use the old name.
        self._cap_field = cap_field
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = client or httpx.AsyncClient(timeout=sse.TIMEOUT)

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
            max_tokens=max_tokens,
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
                # Some local servers omit ids; the pairing only has to be unique per reply.
                id=call_id or f"call_{index}",
                name=name,
                args=args or {},
                invalid=None if args is not None else "arguments were not a JSON object",
            )
        if calls:
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
