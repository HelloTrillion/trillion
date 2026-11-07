"""The devices she knows (SEC-11, the security alerts spec D1, D2, D7).

A device is the page's own random id, kept in its `localStorage` and sent in its
`hello`, never an address or a network. Only a hash of it is kept, so this file can't
be used to pass for a known device. A `hello` with no usable id counts as one device
per host family: leaving the id out never hides a sign-in.

The book is a small JSON file in the data folder, written whole and atomically, mode
600. Forgetting is soft: the device is hidden and unknown again until it next signs in.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Where a page runs, as its hello names it (`api.session.HOSTS`, not imported: this
# package sits below the server).
HOSTS = frozenset({"desktop", "chrome", "edge", "firefox", "safari", "other"})
CLIENTS = frozenset({"web", "cli", "other"})
LABELS = {
    "desktop": "Desktop app",
    "chrome": "Chrome",
    "edge": "Edge",
    "firefox": "Firefox",
    "safari": "Safari",
    "other": "A browser",
}
_RAW = re.compile(r"[A-Za-z0-9_-]{16,128}")
# Last seen moves on at most this often: the file is not written on every hello.
SEEN_EVERY_MS = 60_000
MAX_DEVICES = 200

Sighting = Literal["first", "new", "known"]


@dataclass(frozen=True, kw_only=True)
class Device:
    # The hash of the page's id (or of "no id" and its host family): 32 hex characters.
    id: str
    host: str
    client: str
    first_seen_ms: int
    last_seen_ms: int
    # Whether the page sent an id of its own.
    has_id: bool = True
    forgotten_ms: int | None = None

    @property
    def label(self) -> str:
        name = LABELS.get(self.host, LABELS["other"])
        return name if self.has_id else f"{name} (no device id)"


def device_id(raw: object, host: str) -> tuple[str, bool]:
    """The kept id for a page's raw id, and whether it had a usable one."""
    if isinstance(raw, str) and _RAW.fullmatch(raw):
        return hashlib.sha256(raw.encode()).hexdigest()[:32], True
    return hashlib.sha256(f"no-id:{host}".encode()).hexdigest()[:32], False


def host_of(raw: object) -> str:
    name = raw.strip().lower() if isinstance(raw, str) else ""
    return name if name in HOSTS else "other"


def client_of(raw: object) -> str:
    name = raw.strip().lower() if isinstance(raw, str) else ""
    return name if name in CLIENTS else "other"


class DeviceBook:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._devices: dict[str, Device] = {}
        # A file there but unreadable: no first device is taken on trust after it.
        self._damaged = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            # Unreadable: every device is new again, which says too much, never too little.
            logger.warning("devices file not read: %s", type(e).__name__)
            self._damaged = True
            return
        items = raw.get("devices") if isinstance(raw, dict) else None
        for item in items if isinstance(items, list) else []:
            try:
                d = Device(
                    id=str(item["id"]),
                    host=host_of(item.get("host")),
                    client=client_of(item.get("client")),
                    first_seen_ms=int(item["first_seen_ms"]),
                    last_seen_ms=int(item["last_seen_ms"]),
                    has_id=item.get("has_id") is not False,
                    forgotten_ms=(
                        int(item["forgotten_ms"])
                        if isinstance(item.get("forgotten_ms"), int)
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._devices[d.id] = d

    def _save(self) -> None:
        data = {"devices": [asdict(d) for d in self._devices.values()]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".devices-", suffix=".json", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def seen(self, raw: object, host: str, client: str, now_ms: int) -> tuple[Sighting, Device]:
        """A page signed in. `first`: the first device ever, kept silently; `new`: one
        not known (or forgotten), an event; `known`: silence."""
        host, client = host_of(host), client_of(client)
        kept_id, has_id = device_id(raw, host)
        found = self._devices.get(kept_id)
        if found is not None and found.forgotten_ms is None:
            if now_ms - found.last_seen_ms >= SEEN_EVERY_MS or found.host != host:
                found = replace(found, last_seen_ms=now_ms, host=host, client=client)
                self._devices[kept_id] = found
                self._save_quietly()
            return "known", found
        sighting: Sighting = "first" if not self._devices and not self._damaged else "new"
        d = Device(
            id=kept_id,
            host=host,
            client=client,
            first_seen_ms=now_ms,
            last_seen_ms=now_ms,
            has_id=has_id,
        )
        self._devices[kept_id] = d
        self._trim()
        self._save_quietly()
        return sighting, d

    def _trim(self) -> None:
        """At most MAX_DEVICES: the forgotten go first, then the longest unseen."""
        while len(self._devices) > MAX_DEVICES:
            oldest = min(
                self._devices.values(), key=lambda d: (d.forgotten_ms is None, d.last_seen_ms)
            )
            del self._devices[oldest.id]

    def _save_quietly(self) -> None:
        try:
            self._save()
        except OSError as e:
            logger.warning("devices file not written: %s", type(e).__name__)

    def known(self) -> list[Device]:
        """The devices not forgotten, last seen first."""
        live = [d for d in self._devices.values() if d.forgotten_ms is None]
        return sorted(live, key=lambda d: d.last_seen_ms, reverse=True)

    def forget(self, kept_id: str, now_ms: int) -> bool:
        """Soft: hidden, and unknown again until it next signs in. Whether it was known."""
        found = self._devices.get(kept_id)
        if found is None or found.forgotten_ms is not None:
            return False
        self._devices[kept_id] = replace(found, forgotten_ms=now_ms)
        self._save()
        return True
