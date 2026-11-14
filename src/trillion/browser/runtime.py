"""Her browser's Chromium: Trillion's own, pinned, headless (the her browser spec, 1-3).

- **Trillion's own.** The Chromium is Playwright's headless shell, at the revision the
  locked Playwright release pins (`PINNED`), fetched once on request into Trillion's
  data folder (`<home>/browser/chromium`) and nowhere else. It is launched only from
  there, by its path: never the user's Chrome, Edge or Safari, never a `channel`, never
  a profile of theirs. After the fetch its executable's SHA-256 is written beside it,
  and a launch refuses an executable that no longer matches.
- **In the background.** Headless, muted, no window, nothing in the dock or taskbar.
  It gets a clean environment with a home of its own inside Trillion's folder, a mock
  keychain and a plain password store, so it never reads the user's home, profiles
  or keychain.
- **A fresh context per task.** Each task gets a new, empty context (no cookies, no
  storage, no extensions, service workers blocked), its own way out (`proxy.Egress`),
  and is closed when the task ends. The only state a context can start with is a
  saved sign-in the task was allowed (decision 11a).
- **Honest about the boundary.** Chromium's own sandbox stays on. This is a separate
  browser process and profile, not a virtual machine (decision 3).

Nothing here is fetched or started at import: Playwright is imported only when the
browser is used, so Trillion runs without the `browser` extra.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trillion import paths
from trillion.browser.proxy import Egress, FixtureForTests, ProxySettings
from trillion.owner_only import restrict
from trillion.tools import web

logger = logging.getLogger(__name__)

# The Chromium the locked Playwright release pins (its browsers.json). A different
# Playwright is refused rather than fetching or running a browser nobody pinned.
PLAYWRIGHT_VERSION = "1.65.1"
PINNED = "1254"
CHROMIUM_VERSION = "158.0.8127.18"
APPROX_MB = 150
PIN_FILE = "trillion-pin.json"
CREATE_NO_WINDOW = 0x08000000
INSTALL_TIMEOUT_S = 900.0
# Every launch: no sound, no WebRTC around the proxy, nothing Chromium would fetch on
# its own, and never the system keychain.
LAUNCH_ARGS = (
    "--mute-audio",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-pings",
    "--use-mock-keychain",
    "--password-store=basic",
    "--disable-features=Translate,OptimizationHints,MediaRouter,AutofillServerCommunication",
)
VIEWPORT = {"width": 1280, "height": 800}


class NotInstalled(Exception):
    """Her browser isn't set up, or isn't the one that was set up."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def root() -> Path:
    return paths.home() / "browser"


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def playwright_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("playwright")
    except Exception:
        return None


def _owner_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    restrict(path)
    return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True, kw_only=True)
class Installed:
    version: str
    revision: str
    executable: Path


class Install:
    """Where her Chromium lives, whether it's there, and fetching it once."""

    def __init__(self, base: Path | None = None) -> None:
        self.base = base if base is not None else root()

    @property
    def browsers(self) -> Path:
        return self.base / "chromium"

    def _executable(self) -> Path | None:
        name = "chrome-headless-shell.exe" if sys.platform == "win32" else "chrome-headless-shell"
        folder = self.browsers / f"chromium_headless_shell-{PINNED}"
        if not folder.is_dir():
            return None
        for found in sorted(folder.glob(f"*/{name}")):
            exe = found.resolve()
            # Only ever a file inside Trillion's own folder.
            if exe.is_file() and exe.is_relative_to(self.browsers.resolve()):
                return exe
        return None

    def state(self) -> Installed | None:
        """The installed Chromium, when it is the pinned one and unchanged since it was
        fetched (checked by its pin file; `verify` hashes it again)."""
        exe = self._executable()
        pin = self._pin()
        if exe is None or pin is None or pin.get("revision") != PINNED:
            return None
        return Installed(version=str(pin.get("version", "")), revision=PINNED, executable=exe)

    def _pin(self) -> dict[str, Any] | None:
        try:
            raw = json.loads((self.browsers / PIN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) else None

    def verify(self) -> Installed:
        """The installed Chromium, its executable hashed again; NotInstalled if it's
        missing, another version, or changed since it was fetched."""
        if playwright_version() != PLAYWRIGHT_VERSION:
            raise NotInstalled(
                "playwright", "her browser needs the pinned Playwright: uv sync --extra browser"
            )
        state = self.state()
        pin = self._pin()
        if state is None or pin is None:
            raise NotInstalled("missing", "her browser isn't set up yet")
        if _sha256(state.executable) != pin.get("sha256"):
            raise NotInstalled(
                "changed", "her browser changed since it was set up; set it up again"
            )
        return state

    async def fetch(self) -> Installed:
        """Fetch the pinned Chromium into Trillion's folder, once, on the user's request.
        Playwright's own installer does the download, for the revision it pins."""
        if not playwright_available():
            raise NotInstalled("playwright", "install the browser extra: uv sync --extra browser")
        if playwright_version() != PLAYWRIGHT_VERSION:
            raise NotInstalled("playwright", f"her browser needs Playwright {PLAYWRIGHT_VERSION}")
        _owner_dir(self.base)
        _owner_dir(self.browsers)
        env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(self.browsers)}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "--only-shell", "chromium",
            env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            # No console window on Windows (OPS-3).
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )  # fmt: skip
        try:
            await asyncio.wait_for(proc.communicate(), INSTALL_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise NotInstalled("failed", "fetching her browser took too long") from None
        if proc.returncode != 0:
            logger.warning("fetching her browser failed with status %s", proc.returncode)
            raise NotInstalled("failed", "fetching her browser failed")
        exe = self._executable()
        if exe is None:
            raise NotInstalled("failed", "her browser was fetched but can't be found")
        digest = await asyncio.to_thread(_sha256, exe)
        pin = {"revision": PINNED, "version": CHROMIUM_VERSION, "sha256": digest}
        (self.browsers / PIN_FILE).write_text(json.dumps(pin), encoding="utf-8")
        return Installed(version=CHROMIUM_VERSION, revision=PINNED, executable=exe)

    def remove(self) -> None:
        import shutil

        shutil.rmtree(self.browsers, ignore_errors=True)


def browser_env(home: Path) -> dict[str, str]:
    """All Chromium gets of the environment: a home of its own, and nothing of the
    user's (no keys, no tokens, no profile paths)."""
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    for name in ("LANG", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "DISPLAY"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    if sys.platform == "win32":
        env["USERPROFILE"] = str(home)
        env["LOCALAPPDATA"] = str(home)
        env["APPDATA"] = str(home)
    return env


class BrowserRuntime:
    """The one Chromium process, started on first use, and a context per task."""

    def __init__(
        self,
        install: Install | None = None,
        *,
        resolver: web.Resolve = web.resolve,
        sandbox: bool = True,
        fixture_for_tests: FixtureForTests | None = None,
    ) -> None:
        self.install = install or Install()
        self._resolver = resolver
        self._sandbox = sandbox
        self._fixture = fixture_for_tests
        self._pw: Any = None
        self._browser: Any = None
        self._nowhere: Egress | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._browser is not None

    async def _start(self) -> Any:
        async with self._lock:
            if self._browser is not None:
                return self._browser
            exe = (await asyncio.to_thread(self.install.verify)).executable
            from playwright.async_api import async_playwright

            home = _owner_dir(self.install.base / "home")
            tmp = _owner_dir(self.install.base / "tmp")
            # Every context has a way out of its own; the browser's own default goes
            # nowhere, so a context that somehow had none could reach nothing.
            self._nowhere = Egress(may_visit=lambda host: False)
            nowhere = await self._nowhere.start()
            self._pw = await async_playwright().start()
            try:
                self._browser = await self._pw.chromium.launch(
                    executable_path=str(exe),
                    headless=True,
                    chromium_sandbox=self._sandbox,
                    args=list(LAUNCH_ARGS),
                    env=browser_env(home),
                    downloads_path=str(tmp),
                    proxy=nowhere.for_playwright(),
                    handle_sigint=False,
                    handle_sigterm=False,
                    handle_sighup=False,
                )
            except Exception:
                await self._stop_locked()
                raise
            return self._browser

    async def new_context(
        self,
        egress: ProxySettings,
        *,
        storage_state: Mapping[str, Any] | None = None,
    ) -> Any:
        browser = await self._start()
        return await browser.new_context(
            proxy=egress.for_playwright(),
            accept_downloads=True,
            service_workers="block",
            ignore_https_errors=False,
            viewport=VIEWPORT,
            permissions=[],
            storage_state=dict(storage_state) if storage_state is not None else None,
        )

    def egress(
        self,
        *,
        may_visit: Any,
        refused: Any,
    ) -> Egress:
        return Egress(
            may_visit=may_visit,
            refused=refused,
            resolver=self._resolver,
            fixture_for_tests=self._fixture,
        )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None
        if self._nowhere is not None:
            await self._nowhere.close()
            self._nowhere = None
