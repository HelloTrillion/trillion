"""How much of her an Ollama model can hold (the Ollama context spec).

With no GPU, or a small one, Ollama runs a model with a 4,096-token context, and her
first request is bigger: her instructions are cut, or the request is refused. Its
`/v1` endpoint ignores a context size in the request, so Trillion can only read what
Ollama gave the model (`/api/ps`) and say how to fix it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from trillion.brain import ollama

PS = {
    "models": [
        {"name": "gemma4:e4b", "model": "gemma4:e4b", "context_length": 4096},
        {"name": "qwen2.5:1.5b", "model": "qwen2.5:1.5b", "context_length": 32768},
    ]
}


def client(status: int = 200, body: Any = PS, seen: list[str] | None = None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def test_it_reads_what_ollama_gave_the_model_from_the_servers_own_root() -> None:
    seen: list[str] = []
    got = await ollama.served_context(
        "http://localhost:11434/v1", "gemma4:e4b", client=client(seen=seen)
    )
    assert got == 4096
    # Only the address she already talks to, on Ollama's own path.
    assert seen == ["http://localhost:11434/api/ps"]


async def test_a_model_is_found_by_its_name() -> None:
    got = await ollama.served_context("http://localhost:11434/v1", "qwen2.5:1.5b", client=client())
    assert got == 32768


async def test_a_bare_name_is_ollamas_latest() -> None:
    body = {"models": [{"name": "llama3:latest", "model": "llama3:latest", "context_length": 8192}]}
    got = await ollama.served_context(
        "http://localhost:11434/v1/", "llama3", client=client(body=body)
    )
    assert got == 8192


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"error": "not found"}),  # not Ollama: LM Studio, vLLM, a hosted service
        (200, {"models": []}),  # Ollama, with the model not loaded right now
        (200, {"models": [{"name": "gemma4:e4b", "model": "gemma4:e4b"}]}),  # an older Ollama
        (200, {"models": [{"name": "gemma4:e4b", "context_length": "big"}]}),
        (200, ["not", "a", "dict"]),
    ],
)
async def test_anything_else_is_not_known(status: int, body: Any) -> None:
    got = await ollama.served_context(
        "http://localhost:11434/v1", "gemma4:e4b", client=client(status, body)
    )
    assert got is None


async def test_a_server_that_does_not_answer_is_not_known() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    broken = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    assert await ollama.served_context("http://localhost:11434/v1", "x", client=broken) is None


def test_the_line_names_the_numbers_and_the_fix() -> None:
    assert ollama.too_small(4096) and not ollama.too_small(ollama.NEEDED)
    line = ollama.line(4096)
    assert "4,096" in line and "32,768" in line
    assert "OLLAMA_CONTEXT_LENGTH=32768" in line and "Context length" in line


@pytest.mark.parametrize(
    ("url", "own"),
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:11434/v1", True),
        ("http://192.168.1.20:11434/v1", True),
        ("https://openrouter.ai/api/v1", False),
        ("https://api.openai.com/v1", False),
    ],
)
def test_only_a_server_on_this_computer_or_its_network_is_asked(url: str, own: bool) -> None:
    assert ollama.on_own_network(url) is own
