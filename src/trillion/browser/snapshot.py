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

MAX_CHARS = 12_000
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


def shape(url: str, title: str, raw: str, budget: int = MAX_CHARS) -> Snapshot:
    cut = len(raw) > budget
    text = raw[:budget] + CUT if cut else raw
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
