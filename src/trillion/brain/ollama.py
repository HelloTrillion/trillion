"""How much of her an Ollama model can hold (the Ollama context spec).

Ollama picks a model's context from the memory it can use: with no GPU, or a small
one, that is 4,096 tokens. Her first request is bigger (her instructions alone are
about 4,400 tokens; with her tools it is about 13,300), so it is cut from the start,
and she loses what she is and what she can do, or it is refused. Its OpenAI-compatible `/v1`
endpoint ignores a context size in the request, so all Trillion can do is read what
Ollama gave the model and say, plainly, how to give it more.

`served_context` asks the server she already talks to, on Ollama's own `/api/ps`,
which lists the models loaded now with their context, and only when that server is on
this computer or the user's own network. Anything else (not Ollama, the
model not loaded, an older Ollama that doesn't say) is not known, never a warning.
"""

from __future__ import annotations

from typing import Any

import httpx

from trillion.brain import config

# Her first request is about 13,300 tokens (her instructions and her tools), and a
# conversation grows from there.
NEEDED = 32_768
_TIMEOUT = httpx.Timeout(3.0)


# Ollama runs on this computer or the user's own network; a hosted service is never
# asked about Ollama's paths.
on_own_network = config.on_own_network


def _root(base_url: str) -> str:
    url = base_url.rstrip("/")
    return url[: -len("/v1")] if url.endswith("/v1") else url


def _names(model: str) -> set[str]:
    # Ollama lists a bare name as its `:latest` tag.
    return {model, f"{model}:latest"} if ":" not in model else {model}


async def served_context(
    base_url: str, model: str, *, client: httpx.AsyncClient | None = None
) -> int | None:
    """The context Ollama is running `model` with now, or None when it can't be told."""
    own = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        answer = await http.get(f"{_root(base_url)}/api/ps", timeout=_TIMEOUT)
        if answer.status_code != 200:
            return None
        body: Any = answer.json()
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if own:
            await http.aclose()
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return None
    wanted = _names(model)
    for m in models:
        if not isinstance(m, dict) or not wanted & {m.get("name"), m.get("model")}:
            continue
        size = m.get("context_length")
        return size if isinstance(size, int) and not isinstance(size, bool) else None
    return None


def too_small(size: int) -> bool:
    return size < NEEDED


def line(size: int) -> str:
    """The app's one line for it: what is wrong, and the fix."""
    return (
        f"Ollama is running this model with only {size:,} tokens of context, and she needs "
        f"about {NEEDED:,}, so her instructions get cut off. Restart Ollama with "
        f"OLLAMA_CONTEXT_LENGTH={NEEDED}, or raise Context length in the Ollama app's settings."
    )
