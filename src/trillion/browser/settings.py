"""Settings → Her browser (the her browser spec, decision 17).

Kept as one preference (`browser`). Everything that widens what she may do is off
until the user turns it on: act mode, retrying on a stronger model, and opening any
public site without a chip (`open_any`). Even with that on, pages on this computer and
its network stay refused, and nothing is sent without the user's tap on a card.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trillion.browser import brave

PREF = "browser"
# The search engines she may use, each by its results page for a query: the one the
# user chose, and, if it turns her browser away, the next one once (decision 9). Bing
# first, by its results feed: tested from a home connection on 2026-11-13, it was the
# one of these that served her browser results (its page sometimes came back with no
# results at all; its feed didn't). The others showed a block or a CAPTCHA page.
# Brave's API is the one that isn't a page: called from Trillion's own process with the
# user's key (`browser/brave.py`), never in her browser, and never a fallback.
ENGINES: dict[str, tuple[str, str]] = {
    "bing": ("Bing", "https://www.bing.com/search?format=rss&q={q}"),
    "duckduckgo": ("DuckDuckGo", "https://html.duckduckgo.com/html/?q={q}"),
    "brave": ("Brave Search", "https://search.brave.com/search?q={q}"),
    "google": ("Google", "https://www.google.com/search?q={q}"),
    brave.ENGINE: ("Brave Search API (your key)", brave.MARKER),
}
DEFAULT_ENGINE = "bing"


@dataclass(frozen=True, kw_only=True)
class BrowserSettings:
    act: bool = False
    stronger: bool = False
    search: str = DEFAULT_ENGINE
    live: bool = True
    open_any: bool = False

    @property
    def search_url(self) -> str:
        return ENGINES.get(self.search, ENGINES[DEFAULT_ENGINE])[1]

    @property
    def search_urls(self) -> tuple[str, ...]:
        """The chosen engine, then the one to fall back to once if it refuses."""
        chosen = self.search if self.search in ENGINES else DEFAULT_ENGINE
        # Never the API: it needs a key, and is used only when chosen.
        fallback = next(k for k in ENGINES if k not in (chosen, brave.ENGINE))
        return (ENGINES[chosen][1], ENGINES[fallback][1])

    def to_json(self) -> dict[str, Any]:
        return {
            "act": self.act,
            "stronger": self.stronger,
            "search": self.search,
            "live": self.live,
            "open_any": self.open_any,
        }

    @classmethod
    def from_json(cls, raw: Any) -> BrowserSettings:
        if not isinstance(raw, dict):
            return cls()
        search = raw.get("search")
        return cls(
            act=raw.get("act") is True,
            stronger=raw.get("stronger") is True,
            search=search if isinstance(search, str) and search in ENGINES else DEFAULT_ENGINE,
            live=raw.get("live") is not False,
            open_any=raw.get("open_any") is True,
        )

    def changed(self, raw: Any) -> BrowserSettings:
        """These settings with what a page sent applied: known keys of the right type."""
        if not isinstance(raw, dict):
            return self
        out = self.to_json()
        for key in ("act", "stronger", "live", "open_any"):
            if isinstance(raw.get(key), bool):
                out[key] = raw[key]
        if isinstance(raw.get("search"), str) and raw["search"] in ENGINES:
            out["search"] = raw["search"]
        return BrowserSettings.from_json(out)
