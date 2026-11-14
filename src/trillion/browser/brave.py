"""Search through the Brave Search API, with the user's own key (Settings → Her browser).

Google, DuckDuckGo and Brave's own results page serve her browser a CAPTCHA or a block,
and she never tries to get past one (the her browser spec, decision 9). Brave's API
answers a program with a key, so this engine is called from Trillion's own process,
never from her browser: the key never enters a page.

- **One fixed address.** Only `URL` is ever called, never one the model chose, and it
  is sent only the query, which holds only words from the user's request.
- **Only when chosen.** Nothing here runs unless the user picked this engine and
  saved a key: no check, no warm-up, no phone-home.
- **The key** lives in the operating system's keychain under `SECRET`, read when a
  search is made and never kept. It is never logged, shown, returned to the model, or
  put in an error: a failure carries only one of the kinds below, with fixed words.
- **A failure is a fallback, never a retry.** No key, a key Brave refuses (401, 403),
  the plan's limit (429), or no answer: the search tool falls back once to the next
  engine and logs why, as it does when an engine refuses.
"""

from __future__ import annotations

import html
import re
from typing import Any, Literal

import httpx

from trillion.browser.snapshot import Result

ENGINE = "brave_api"
ORIGIN = "https://api.search.brave.com"
URL = f"{ORIGIN}/res/v1/web/search"
# How the engine is named among the others' results URLs: never opened in her browser.
MARKER = f"{URL}?q={{q}}"
SECRET = "brave_search/api_key"  # noqa: S105 (the keychain entry's name, not a key)
KEY_LINK = "https://api-dashboard.search.brave.com/"
COUNT = 10
TIMEOUT_S = 15.0
TEXT_MAX = 400
KEY_MAX = 512
# Brave's free plan answers this one word as any query; checking a key costs one.
CHECK_QUERY = "trillion"

Kind = Literal["no_key", "key_refused", "limit_reached", "unreachable", "unavailable"]

# What each failure says, to the model and the user. Never the key, never Brave's text.
WORDS: dict[str, str] = {
    "no_key": "no Brave Search API key is saved",
    "key_refused": "Brave didn't accept the saved key",
    "limit_reached": "Brave's monthly limit is reached",
    "unreachable": "Brave Search couldn't be reached",
    "unavailable": "Brave Search didn't answer as expected",
}

_TAG = re.compile(r"<[^>]*>")


class SearchFailed(Exception):
    """Why the API gave no results. The message is fixed words, never a value."""

    def __init__(self, kind: Kind) -> None:
        super().__init__(WORDS[kind])
        self.kind: Kind = kind


def is_api(url: str) -> bool:
    return url == MARKER


def plain(text: Any) -> str:
    """A title or description as text: Brave marks matches with `<strong>`."""
    if not isinstance(text, str):
        return ""
    return " ".join(html.unescape(_TAG.sub("", text)).split())


def results_of(data: Any) -> list[Result]:
    """`web.results[]` as results: each with a web address, its tags stripped."""
    web = data.get("web") if isinstance(data, dict) else None
    items = web.get("results") if isinstance(web, dict) else None
    out: list[Result] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        link = item.get("url")
        if not isinstance(link, str) or not link.startswith(("http://", "https://")):
            continue
        out.append(
            Result(
                title=plain(item.get("title")),
                link=link,
                text=plain(item.get("description"))[:TEXT_MAX],
            )
        )
    return out[:COUNT]


async def _get(query: str, key: str, count: int, transport: httpx.AsyncBaseTransport | None) -> Any:
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_S, follow_redirects=False
        ) as client:
            r = await client.get(URL, params={"q": query, "count": count}, headers=headers)
    except httpx.HTTPError:
        # `from None`: nothing of the request travels on with the error.
        raise SearchFailed("unreachable") from None
    if r.status_code in (401, 403, 422):  # 422: Brave's answer to an invalid token
        raise SearchFailed("key_refused")
    if r.status_code == 429:
        raise SearchFailed("limit_reached")
    if r.status_code != 200:
        raise SearchFailed("unavailable")
    try:
        return r.json()
    except ValueError:
        raise SearchFailed("unavailable") from None


async def search(
    query: str, key: str | None, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[Result]:
    """The results for `query`, or `SearchFailed`. One request, never retried."""
    if not key:
        raise SearchFailed("no_key")
    return results_of(await _get(query, key, COUNT, transport))


async def check(key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> Kind | None:
    """None when Brave takes the key; else why not. One request, of one result.

    A key at its plan's limit is still a key Brave knows: the caller saves it and says so."""
    try:
        await _get(CHECK_QUERY, key, 1, transport)
    except SearchFailed as e:
        return e.kind
    return None
