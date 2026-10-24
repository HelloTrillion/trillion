"""Both adapters, against recorded wire formats. No network."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from trillion.brain.providers.anthropic import (
    AnthropicProvider,
    cache_history,
    prefix_key,
    wire_messages,
)
from trillion.brain.providers.anthropic import list_models as anthropic_list_models
from trillion.brain.providers.openai_compat import OpenAICompatProvider, request_body
from trillion.brain.providers.openai_compat import list_models as openai_list_models
from trillion.brain.types import (
    ContextBlock,
    Done,
    ModelChoice,
    ModelEvent,
    ModelProvider,
    Msg,
    Pricing,
    ProviderError,
    ProviderRaw,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUse,
    ToolUseBlock,
    Usage,
)

KEY = "test-key-not-a-real-one"
SYSTEM = [SystemBlock(text="soul", cache=True)]
TOOLS = [ToolSchema(name="file_read", description="Read.", input_schema={"type": "object"})]
HISTORY = [
    Msg(role="user", blocks=(TextBlock(text="read it"),)),
    Msg(role="assistant", blocks=(ToolUseBlock(id="t1", name="file_read", args={"path": "a"}),)),
    Msg(role="user", blocks=(ToolResultBlock(tool_use_id="t1", content="{}"),)),
]


def sse(*events: tuple[str, Any]) -> bytes:
    out = []
    for name, data in events:
        body = data if isinstance(data, str) else json.dumps(data)
        out.append((f"event: {name}\n" if name else "") + f"data: {body}\n\n")
    return "".join(out).encode()


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def collect(p: ModelProvider, messages: list[Msg] | None = None) -> list[ModelEvent]:
    stream = p.stream(system=SYSTEM, messages=messages or HISTORY, tools=TOOLS, max_tokens=64)
    return [ev async for ev in stream]


ANTHROPIC_STREAM = sse(
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m1", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 1,
                  "cache_read_input_tokens": 100, "cache_creation_input_tokens": 7}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
        "type": "thinking", "thinking": "", "signature": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "signature_delta", "signature": "sig-abc"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("content_block_start", {"type": "content_block_start", "index": 1,
                             "content_block": {"type": "text", "text": ""}}),
    ("ping", {"type": "ping"}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1,
                             "delta": {"type": "text_delta", "text": "On "}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1,
                             "delta": {"type": "text_delta", "text": "it."}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {
        "type": "tool_use", "id": "toolu_1", "name": "file_read", "input": {}}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 2,
                             "delta": {"type": "input_json_delta", "partial_json": '{"pa'}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 2,
                             "delta": {"type": "input_json_delta", "partial_json": 'th": "a"}'}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 2}),
    ("message_delta", {"type": "message_delta",
                       "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                       "usage": {"output_tokens": 9}}),
    ("message_stop", {"type": "message_stop"}),
)  # fmt: skip


def anthropic_provider(
    handler: Callable[[httpx2.Request], httpx2.Response], key: Callable[[], str] = lambda: KEY
) -> AnthropicProvider:
    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicProvider(api_key=key, model="m1", http_client=http)


def stream_of(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})


async def test_anthropic_request_and_stream() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return stream_of(ANTHROPIC_STREAM)

    events = await collect(anthropic_provider(handler))
    done = events[-1]
    assert events[:-1] == [
        TextDelta(text="On "),
        TextDelta(text="it."),
        ToolUse(id="toolu_1", name="file_read", args={"path": "a"}),
    ]
    assert isinstance(done, Done) and done.stop_reason == "tool_use"
    assert done.usage == Usage(
        input_tokens=12, output_tokens=9, cache_read_tokens=100, cache_write_tokens=7
    )
    # The whole turn is kept as it came, signed reasoning included, to be sent back.
    assert done.raw is not None and done.raw.provider == "anthropic"
    assert [b["type"] for b in done.raw.content] == ["thinking", "text", "tool_use"]
    assert done.raw.content[0]["signature"] == "sig-abc"

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == KEY
    body = seen["body"]
    assert body["model"] == "m1" and body["stream"] is True and body["max_tokens"] == 64000
    # Nothing is forced or tuned, so the request is valid on every current model.
    assert not {"thinking", "temperature", "tool_choice", "output_config"} & set(body)
    # The stable block is marked cacheable (PER-5).
    assert body["system"] == [
        {"type": "text", "text": "soul", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["messages"][1]["content"] == [
        {"type": "tool_use", "id": "t1", "name": "file_read", "input": {"path": "a"}}
    ]
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert body["tools"][0]["name"] == "file_read"


async def test_anthropic_replays_its_own_turn_unchanged() -> None:
    bodies: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return stream_of(ANTHROPIC_STREAM)

    p = anthropic_provider(handler)
    done = (await collect(p, HISTORY[:1]))[-1]
    assert isinstance(done, Done) and done.raw is not None
    turn = Msg(role="assistant", blocks=(TextBlock(text="On it."),), raw=done.raw)
    await collect(p, [HISTORY[0], turn, Msg(role="user", blocks=(TextBlock(text="go on"),))])
    assert bodies[1]["messages"][1]["content"] == [dict(b) for b in done.raw.content]
    assert bodies[1]["messages"][0] == bodies[0]["messages"][0]  # earlier turns untouched


def test_reasoning_is_replayed_only_while_what_it_was_bound_to_is_unchanged() -> None:
    tools = TOOLS
    key = prefix_key("m1", SYSTEM, tools)
    assert key == prefix_key("m1", SYSTEM, list(reversed(tools)))
    assert key != prefix_key("m2", SYSTEM, tools)
    assert key != prefix_key("m1", [SystemBlock(text="edited")], tools)
    assert key != prefix_key("m1", SYSTEM, [])

    def turn(k: str | None, provider: str = "anthropic") -> Msg:
        raw = None
        if k is not None:
            raw = ProviderRaw(provider=provider, prefix_key=k, content=({"type": "thinking"},))
        return Msg(role="assistant", blocks=(TextBlock(text="t"),), raw=raw)

    user = Msg(role="user", blocks=(TextBlock(text="u"),))

    def kinds(messages: list[Msg]) -> list[str]:
        return [m["content"][0]["type"] for m in wire_messages(messages, key)]

    assert kinds([user, turn(key), user, turn(key)]) == ["text", "thinking", "text", "thinking"]
    # A turn made under another system prompt, by another provider, or cut short has
    # nothing to replay. Reasoning may be dropped from the front, never the middle:
    # everything before the last such turn goes plain too.
    assert kinds([user, turn(key), user, turn("old")]) == ["text"] * 4
    assert kinds([user, turn("old"), user, turn(key)]) == ["text", "text", "text", "thinking"]
    assert kinds([user, turn(key), user, turn(None), user, turn(key)])[1::2] == [
        "text", "text", "thinking",
    ]  # fmt: skip
    assert kinds([user, turn(key, "other")]) == ["text", "text"]


CACHED = {"type": "ephemeral"}


def marks(wire: list[dict[str, Any]]) -> list[list[bool]]:
    return [["cache_control" in b for b in m["content"]] for m in wire]


async def test_the_conversation_is_cached_up_to_its_newest_message() -> None:
    """PER-5: the next request starts with this one, so this one's end is marked."""
    seen: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return stream_of(ANTHROPIC_STREAM)

    await collect(anthropic_provider(handler))
    body = seen[0]
    # The newest user message, and the one before it, each end on a breakpoint.
    assert marks(body["messages"]) == [[True], [False], [True]]
    assert body["messages"][2]["content"][0]["cache_control"] == CACHED
    # With the instructions, that is three of the four the API allows.
    assert sum(sum(m) for m in marks(body["messages"])) + len(body["system"]) <= 4


def test_only_the_last_block_of_the_newest_two_user_messages_is_marked() -> None:
    user = Msg(role="user", blocks=(ContextBlock(text="now"), TextBlock(text="hi")))
    reply = Msg(role="assistant", blocks=(TextBlock(text="hello"),))
    wire = wire_messages([user, reply, user, reply, user], "k")
    cache_history(wire)
    assert marks(wire) == [[False, False], [False], [False, True], [False], [False, True]]


def test_her_replayed_reasoning_is_never_marked() -> None:
    key = prefix_key("m1", SYSTEM, TOOLS)
    raw = ProviderRaw(
        provider="anthropic",
        prefix_key=key,
        content=(
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": "t"},
        ),
    )
    turn = Msg(role="assistant", blocks=(TextBlock(text="t"),), raw=raw)
    user = Msg(role="user", blocks=(TextBlock(text="u"),))
    wire = wire_messages([user, turn, user], key)
    cache_history(wire)
    assert wire[1]["content"] == [dict(b) for b in raw.content]
    assert raw.content[1] == {"type": "text", "text": "t"}  # the kept turn is unchanged too


def test_marking_a_short_or_empty_history_is_harmless() -> None:
    wire: list[dict[str, Any]] = []
    cache_history(wire)
    assert wire == []
    lone = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    cache_history(lone)
    assert lone[0]["content"][0]["cache_control"] == CACHED


async def test_anthropic_reads_its_key_on_every_request() -> None:
    keys = iter(["first", "second"])
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["x-api-key"])
        return stream_of(ANTHROPIC_STREAM)

    p = anthropic_provider(handler, key=lambda: next(keys))
    await collect(p)
    await collect(p)
    assert seen == ["first", "second"]  # replacing a key needs no restart (SEC-6)


async def test_a_refusal_is_a_stop_reason_not_an_empty_success() -> None:
    refused = ANTHROPIC_STREAM.replace(b'"stop_reason": "tool_use"', b'"stop_reason": "refusal"')
    done = (await collect(anthropic_provider(lambda r: stream_of(refused))))[-1]
    assert isinstance(done, Done) and done.stop_reason == "refusal"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "unauthorized"), (403, "forbidden"), (404, "not_found"), (400, "bad_request")],
)
async def test_http_errors_become_kinds_and_never_carry_the_body(status: int, kind: str) -> None:
    body = json.dumps({"type": "error", "error": {"type": "x", "message": f"you sent {KEY}"}})
    p = anthropic_provider(lambda r: httpx2.Response(status, content=body.encode()))
    with pytest.raises(ProviderError) as e:
        await collect(p)
    assert e.value.kind == kind
    assert KEY not in str(e.value)


def test_known_models_have_a_price_and_unknown_ones_do_not() -> None:
    assert AnthropicProvider(api_key=lambda: KEY, model="claude-opus-5-5").pricing() == Pricing(
        input=4.0, output=20.0, cache_read=0.20, cache_write=5.0
    )
    assert AnthropicProvider(api_key=lambda: KEY, model="something-new").pricing() is None


_PRICED = [
    "claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
    "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5", "claude-sonnet-5-5",
    "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-5-5", "claude-haiku-4-5",
]  # fmt: skip


def _price(model: str) -> Pricing | None:
    return AnthropicProvider(api_key=lambda: KEY, model=model).pricing()


@pytest.mark.parametrize("model", _PRICED)
def test_every_listed_model_has_a_price(model: str) -> None:
    assert _price(model) is not None


def test_sonnet_5_5_cache_read_is_a_tenth() -> None:
    p = _price("claude-sonnet-5-5")
    assert p is not None and p.cache_read == 0.10


def test_dated_ids_price_like_their_undated_ids() -> None:
    assert _price("claude-haiku-4-5-20251001") == _price("claude-haiku-4-5") is not None
    assert _price("claude-opus-4-5-20251101") == _price("claude-opus-4-5") is not None
    assert _price("something-new-20260101") is None


def test_haiku_5_5_long_prompt_tier() -> None:
    p = _price("claude-haiku-5-5")
    assert p is not None
    at_line = Usage(input_tokens=100_000, output_tokens=1_000)
    assert p.cost(at_line) == pytest.approx(0.0105)
    # Cache reads count toward the prompt, and then every rate is the higher one.
    over = Usage(input_tokens=90_000, cache_read_tokens=10_001, output_tokens=1_000)
    assert p.cost(over) == pytest.approx(0.04800005)


def test_pricing_without_a_tier_ignores_prompt_length() -> None:
    flat = Pricing(input=1.0, output=1.0)
    assert flat.cost(Usage(input_tokens=1_000_000, output_tokens=0)) == pytest.approx(1.0)


async def test_anthropic_lists_models() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/models" and request.headers["x-api-key"] == KEY
        row = {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
               "created_at": "2026-01-01T00:00:00Z", "max_tokens": 128000}  # fmt: skip
        return httpx2.Response(
            200, json={"data": [row], "has_more": False, "first_id": None, "last_id": None}
        )

    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    assert await anthropic_list_models(KEY, http) == [
        ModelChoice(id="claude-opus-5-5", label="Claude Opus 5.5", max_output=128000)
    ]


async def test_a_bad_key_fails_the_model_list() -> None:
    http = anthropic.DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(401, json={"error": {}}))
    )
    with pytest.raises(ProviderError) as e:
        await anthropic_list_models("wrong", http)
    assert e.value.kind == "unauthorized"


async def test_an_unreachable_provider_is_a_kind_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = OpenAICompatProvider(base_url="http://localhost:1/v1", model="m", client=client(handler))
    with pytest.raises(ProviderError) as e:
        await collect(p)
    assert e.value.kind == "unreachable"


OPENAI_STREAM = sse(
    ("", {"choices": [{"delta": {"role": "assistant", "content": "On "}}]}),
    ("", {"choices": [{"delta": {"content": "it."}}]}),
    ("", {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "function": {"name": "file_read", "arguments": '{"pa'}}]}}]}),
    ("", {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": 'th": "a"}'}}]}}]}),
    ("", {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ("", {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 9,
                                   "prompt_tokens_details": {"cached_tokens": 20}}}),
    ("", "[DONE]"),
)  # fmt: skip


async def test_openai_compatible_request_and_stream() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=OPENAI_STREAM)

    p = OpenAICompatProvider(
        base_url="http://localhost:11434/v1/", model="local", client=client(handler)
    )
    events = await collect(p)
    assert events == [
        TextDelta(text="On "),
        TextDelta(text="it."),
        ToolUse(id="call_1", name="file_read", args={"path": "a"}),
        Done(
            stop_reason="tool_use",
            usage=Usage(input_tokens=10, output_tokens=9, cache_read_tokens=20),
        ),
    ]
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["auth"] is None  # a local model needs no key
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert seen["body"]["messages"][2]["tool_calls"][0]["function"] == {
        "name": "file_read",
        "arguments": '{"path": "a"}',
    }
    assert seen["body"]["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "{}"}
    assert seen["body"]["tools"][0]["function"]["name"] == "file_read"


async def test_openai_compatible_sends_a_key_when_there_is_one() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, content=sse(("", "[DONE]")))

    p = OpenAICompatProvider(
        base_url="http://x/v1", model="m", api_key=lambda: KEY, client=client(handler)
    )
    events = await collect(p)
    assert seen == [f"Bearer {KEY}"]
    assert events == [Done(stop_reason="other", usage=Usage())]


async def test_openai_compatible_tool_result_and_text_in_one_user_turn() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(("", "[DONE]")))

    mixed = [
        *HISTORY[:2],
        Msg(
            role="user",
            blocks=(TextBlock(text="and?"), ToolResultBlock(tool_use_id="t1", content="{}")),
        ),
    ]
    p = OpenAICompatProvider(base_url="http://x/v1", model="m", client=client(handler))
    await collect(p, mixed)
    # The tool's answer must directly follow the assistant's call.
    assert [m["role"] for m in seen["body"]["messages"]][-2:] == ["tool", "user"]


async def test_openai_compatible_sends_context_as_text_and_names_the_cap_as_asked() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(("", "[DONE]")))

    said = Msg(
        role="user", blocks=(ContextBlock(text="<context>now</context>"), TextBlock(text="hi"))
    )
    p = OpenAICompatProvider(
        base_url="http://x/v1",
        model="m",
        cap_field="max_completion_tokens",
        max_output=900,
        client=client(handler),
    )
    await collect(p, [said])
    assert seen["body"]["messages"][-1]["content"] == "<context>now</context>\n\nhi"
    assert seen["body"]["max_completion_tokens"] == 900 and "max_tokens" not in seen["body"]

    # With no limit known for the model, none is sent: a fixed one would be spent by a
    # reasoning model's thinking, or refused by a model whose own limit is lower.
    await collect(OpenAICompatProvider(base_url="http://x/v1", model="m", client=client(handler)))
    assert not {"max_tokens", "max_completion_tokens"} & seen["body"].keys()


async def test_openai_compatible_lists_models_and_reports_a_bad_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401)
        return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {"nope": 1}]})

    http = client(handler)
    assert await openai_list_models("http://x/v1/", KEY, http) == [
        ModelChoice(id="a", label="a"),
        ModelChoice(id="b", label="b"),
    ]
    with pytest.raises(ProviderError) as e:
        await openai_list_models("http://x/v1", "wrong", http)
    assert e.value.kind == "unauthorized"


async def test_a_reply_cut_off_at_the_length_limit_says_so_even_with_tool_calls() -> None:
    """Otherwise the loop's "cut off mid tool call, run nothing" rule could never fire here."""
    stream = sse(
        ("", {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "x1",
             "function": {"name": "file_read", "arguments": '{"path": "a"}'}},
            {"index": 1, "id": "x2",
             "function": {"name": "file_create", "arguments": '{"path": "b", "con'}},
        ]}}]}),
        ("", {"choices": [{"delta": {}, "finish_reason": "length"}]}),
        ("", "[DONE]"),
    )  # fmt: skip
    p = OpenAICompatProvider(
        base_url="http://x/v1",
        model="m",
        client=client(lambda r: httpx.Response(200, content=stream)),
    )
    done = (await collect(p))[-1]
    assert isinstance(done, Done) and done.stop_reason == "max_tokens"


async def test_made_up_tool_call_ids_are_unique_across_replies() -> None:
    stream = sse(
        ("", {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "file_read", "arguments": "{}"}}]}}]}),
        ("", "[DONE]"),
    )  # fmt: skip
    p = OpenAICompatProvider(
        base_url="http://x/v1",
        model="m",
        client=client(lambda r: httpx.Response(200, content=stream)),
    )
    first, second = (await collect(p))[0], (await collect(p))[0]
    assert isinstance(first, ToolUse) and isinstance(second, ToolUse)
    assert first.id and second.id and first.id != second.id


def test_another_providers_tool_ids_are_made_acceptable_and_still_pair() -> None:
    odd = "call:0/abc def"
    history = [
        Msg(role="user", blocks=(TextBlock(text="go"),)),
        Msg(role="assistant", blocks=(ToolUseBlock(id=odd, name="file_read", args={}),)),
        Msg(role="user", blocks=(ToolResultBlock(tool_use_id=odd, content="{}"),)),
    ]
    wire = wire_messages(history, "k")
    sent_id = wire[1]["content"][0]["id"]
    assert sent_id == wire[2]["content"][0]["tool_use_id"] == "call_0_abc_def"


# A notice is saved as her message outside a turn: two of hers can sit side by side,
# and one can open a conversation before the user has said anything.
NOTICE = Msg(role="assistant", blocks=(TextBlock(text="Time to stretch."),))
USER = Msg(role="user", blocks=(TextBlock(text="thanks"),))
REPLY = Msg(role="assistant", blocks=(TextBlock(text="Here you go."),))


def test_anthropic_notices_beside_her_reply_go_as_one_message_of_hers() -> None:
    wire = wire_messages([USER, REPLY, NOTICE, NOTICE, USER], "k")
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert [b["text"] for b in wire[1]["content"]] == [
        "Here you go.", "Time to stretch.", "Time to stretch.",
    ]  # fmt: skip


def test_anthropic_her_replayed_turn_stays_whole_when_a_notice_follows_it() -> None:
    key = prefix_key("m1", SYSTEM, TOOLS)
    raw = ProviderRaw(
        provider="anthropic",
        prefix_key=key,
        content=(
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": "t"},
        ),
    )
    turn = Msg(role="assistant", blocks=(TextBlock(text="t"),), raw=raw)
    wire = wire_messages([USER, turn, NOTICE, USER], key)
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert raw.content[0]["type"] == "thinking" and len(raw.content) == 2  # never changed


def test_anthropic_a_conversation_a_notice_opened_starts_with_the_user() -> None:
    wire = wire_messages([NOTICE, USER], "k")
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert wire[1]["content"] == [{"type": "text", "text": "Time to stretch."}]
    assert wire[0]["content"][0]["text"]  # a placeholder, never shown or spoken


def test_openai_compatible_notices_beside_her_reply_go_as_one_message_of_hers() -> None:
    body = request_body(model="m", system=SYSTEM, messages=[USER, REPLY, NOTICE, USER], tools=[])
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["messages"][2]["content"] == "Here you go.\n\nTime to stretch."


def test_openai_compatible_a_conversation_a_notice_opened_starts_with_the_user() -> None:
    # Many local chat templates refuse anything but user, assistant, user, ...
    body = request_body(model="m", system=SYSTEM, messages=[NOTICE, NOTICE, USER], tools=[])
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["messages"][2]["content"] == "Time to stretch.\n\nTime to stretch."
    no_system = request_body(model="m", system=[], messages=[NOTICE, USER], tools=[])
    assert [m["role"] for m in no_system["messages"]] == ["user", "assistant", "user"]
