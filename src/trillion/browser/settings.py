"""Settings → Her browser (the her browser spec, decision 17).

Kept as one preference (`browser`). Everything that widens what she may do is off
until the user turns it on: act mode and retrying on a stronger model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PREF = "browser"
# The search engines she may use, each by its results page for a query. One is used
# at a time, the one the user chose (decision 9).
ENGINES: dict[str, tuple[str, str]] = {
    "duckduckgo": ("DuckDuckGo", "https://html.duckduckgo.com/html/?q={q}"),
    "bing": ("Bing", "https://www.bing.com/search?q={q}"),
    "brave": ("Brave Search", "https://search.brave.com/search?q={q}"),
    "google": ("Google", "https://www.google.com/search?q={q}"),
}
DEFAULT_ENGINE = "duckduckgo"


@dataclass(frozen=True, kw_only=True)
class BrowserSettings:
    act: bool = False
    stronger: bool = False
    search: str = DEFAULT_ENGINE
    live: bool = True

    @property
    def search_url(self) -> str:
        return ENGINES.get(self.search, ENGINES[DEFAULT_ENGINE])[1]

    def to_json(self) -> dict[str, Any]:
        return {
            "act": self.act,
            "stronger": self.stronger,
            "search": self.search,
            "live": self.live,
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
        )

    def changed(self, raw: Any) -> BrowserSettings:
        """These settings with what a page sent applied: known keys of the right type."""
        if not isinstance(raw, dict):
            return self
        out = self.to_json()
        for key in ("act", "stronger", "live"):
            if isinstance(raw.get(key), bool):
                out[key] = raw[key]
        if isinstance(raw.get("search"), str) and raw["search"] in ENGINES:
            out["search"] = raw["search"]
        return BrowserSettings.from_json(out)
