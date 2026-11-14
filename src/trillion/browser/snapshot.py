"""The page as text (the her browser spec, decision 4; CMP-2).

The model reads the page's accessibility snapshot (Playwright's aria snapshot, in
its "ai" form), which gives each element a short reference (`e12`) the tools act on.
It is cut to a budget, and the router wraps it as untrusted like every page (SPN-5).
No screenshot is ever sent to the model.

The links a page shows are read out of the snapshot too, so a link the model copies
into `browser_open` counts as one she followed, not one she made up (decision 9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

# Small enough that a local model reads a page in seconds, not minutes; the rest is
# found with browser_find.
MAX_CHARS = 6_000
CUT = "\n[the rest of the page was cut; use browser_find to look for something in it]"
_REF = re.compile(r"\[ref=([a-z0-9]+)\]")
_URL = re.compile(r"^\s*-\s*/url:\s*(\S+)\s*$")


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    url: str
    title: str
    text: str
    refs: frozenset[str]
    links: tuple[str, ...]
    cut: bool


def links_in(text: str, base: str) -> tuple[str, ...]:
    out: list[str] = []
    for line in text.splitlines():
        m = _URL.match(line)
        if m:
            out.append(urljoin(base, m.group(1).strip("\"'")))
    return tuple(dict.fromkeys(out))


# A line that is only a box around other lines: no words, nothing to press.
_BARE = re.compile(
    r"^\s*- (generic|group|list|listitem|none|presentation|table|rowgroup|row|cell|superscript"
    r"|note|paragraph)( \[ref=[a-z0-9]+\])?:?$"
)


_MAIN = re.compile(r"^(\s*)- main\b")
# Menus and page furniture: read last, after what the page is about.
_ASIDE = re.compile(r"^(\s*)- (navigation|banner|contentinfo|complementary|search)\b")


def _subtree(lines: list[str], i: int) -> int:
    """Where the subtree that starts at line `i` ends."""
    depth = len(lines[i]) - len(lines[i].lstrip())
    end = i + 1
    while end < len(lines) and len(lines[end]) - len(lines[end].lstrip()) > depth:
        end += 1
    return end


def compact(raw: str) -> str:
    """The snapshot without the lines that are only boxes round other lines; the page's
    main part first when it marks one; and its menus and furniture last, so a small
    budget is spent on what the page is about."""
    lines = [
        line.replace(" [cursor=pointer]", "") for line in raw.splitlines() if not _BARE.match(line)
    ]
    for i, line in enumerate(lines):
        if _MAIN.match(line):
            end = _subtree(lines, i)
            lines = lines[i:end] + lines[:i] + lines[end:]
            break
    first: list[str] = []
    later: list[str] = []
    i = 0
    while i < len(lines):
        if _ASIDE.match(lines[i]):
            end = _subtree(lines, i)
            later += lines[i:end]
            i = end
        else:
            first.append(lines[i])
            i += 1
    return "\n".join(first + later)


def shape(url: str, title: str, raw: str, budget: int = MAX_CHARS) -> Snapshot:
    text = compact(raw)
    cut = len(text) > budget
    text = text[:budget] + CUT if cut else text
    return Snapshot(
        url=url,
        title=" ".join(title.split()),
        text=text,
        refs=frozenset(_REF.findall(raw)),
        links=links_in(raw, url),
        cut=cut,
    )


async def take(page: Any, budget: int = MAX_CHARS) -> Snapshot:
    raw = await page.aria_snapshot(mode="ai", timeout=10_000)
    try:
        title = await page.title()
    except Exception:
        title = ""
    return shape(str(page.url), str(title), str(raw), budget)


def shown(snap: Snapshot) -> str:
    head = f"Page: {snap.url}" + (f"\nTitle: {snap.title}" if snap.title else "")
    return f"{head}\n\n{snap.text or '(nothing readable on the page)'}"


def find(snap_text: str, text: str, limit: int = 30) -> list[str]:
    """The snapshot's lines that hold `text`, case set aside, each with its reference."""
    needle = text.casefold().strip()
    if not needle:
        return []
    found = [line.strip() for line in snap_text.splitlines() if needle in line.casefold()]
    return found[:limit]


# A search engine's results as a feed (RSS): what each result is called, where it
# leads and what it says, read from the feed itself in the page.
_FEED = """() => {
  if (!(document instanceof XMLDocument) && !document.querySelector('rss, feed')) return null;
  return [...document.querySelectorAll('item')].slice(0, 12).map((i) => ({
    title: (i.querySelector('title')?.textContent || '').trim(),
    link: (i.querySelector('link')?.textContent || '').trim(),
    text: (i.querySelector('description')?.textContent || '').trim().slice(0, 400),
  }));
}"""


@dataclass(frozen=True, kw_only=True)
class Result:
    title: str
    link: str
    text: str


async def feed_results(page: Any) -> list[Result] | None:
    """The results, when the page is a search feed; None when it isn't one."""
    try:
        found = await page.evaluate(_FEED)
    except Exception:
        return None
    if not isinstance(found, list):
        return None
    out = []
    for item in found:
        if isinstance(item, dict) and str(item.get("link", "")).startswith(("http://", "https://")):
            out.append(
                Result(
                    title=" ".join(str(item.get("title", "")).split()),
                    link=str(item["link"]),
                    text=" ".join(str(item.get("text", "")).split()),
                )
            )
    return out


def shown_results(query: str, results: list[Result]) -> str:
    lines = [f"Search results for: {query}", ""]
    for n, r in enumerate(results, 1):
        lines.append(f"{n}. {r.title or r.link}")
        lines.append(f"   {r.link}")
        if r.text:
            lines.append(f"   {r.text}")
    lines.append("")
    lines.append("Open a result with browser_open and its address exactly as shown.")
    return "\n".join(lines)
