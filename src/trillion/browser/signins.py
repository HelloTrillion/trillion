"""Saved sign-ins (the her browser spec, decision 11a; CMP-7).

"Keep me signed in here" keeps one site's cookies and storage from her browser,
never a password: the user typed it, or their password manager's page filled it, and
nothing here reads a field. Each site is a file of its own in Trillion's owner-only
data folder (`<home>/browser/signins/<site>.json`, 0600), holding only that site's
cookies and that site's origins' storage, nothing of any other site the page touched.

Using one is limited, by the caller (`browse.py`, `service.py`):

- only when the user's request names the site, after a chip;
- the task then stays on that site (`Broker.site`, enforced at the connection);
- each task gets only the sign-in it was allowed, in a fresh context.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from trillion.browser.broker import same_site
from trillion.browser.proxy import clean_host
from trillion.owner_only import restrict

_SITE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def site_of(host: str) -> str | None:
    """The site a host belongs to, as a saved sign-in is named: `www.` set aside."""
    clean = clean_host(host)
    if clean is None:
        return None
    clean = clean.removeprefix("www.")
    return clean if _SITE.match(clean) else None


def only_site(state: dict[str, Any], site: str) -> dict[str, Any]:
    """A storage state cut to one site: its cookies, and its origins' storage."""
    cookies = [
        c
        for c in state.get("cookies", [])
        if isinstance(c, dict) and same_site(str(c.get("domain", "")).lstrip("."), site)
    ]
    origins = [
        o
        for o in state.get("origins", [])
        if isinstance(o, dict)
        and same_site(urlsplit(str(o.get("origin", ""))).hostname or "", site)
    ]
    return {"cookies": cookies, "origins": origins}


class SignIns:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, site: str) -> Path:
        clean = site_of(site)
        if clean is None or clean != site:
            raise ValueError("not a site")
        return self.root / f"{site}.json"

    def sites(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json") if site_of(p.stem) == p.stem)

    def has(self, site: str) -> bool:
        try:
            return self._path(site).is_file()
        except ValueError:
            return False

    def keep(self, site: str, state: dict[str, Any]) -> int:
        """Keep the site's part of `state`; how many cookies were kept."""
        kept = only_site(state, site)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            os.chmod(self.root, 0o700)
        restrict(self.root)
        path = self._path(site)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(kept, f)
        return len(kept["cookies"])

    def load(self, site: str) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._path(site).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return only_site(raw, site) if isinstance(raw, dict) else None

    def remove(self, site: str) -> bool:
        try:
            self._path(site).unlink()
        except (OSError, ValueError):
            return False
        return True

    def named(self, hosts: frozenset[str]) -> str | None:
        """The first saved sign-in whose site the user's request named, if any."""
        for host in sorted(hosts):
            site = site_of(host)
            if site is not None and self.has(site):
                return site
        return None
