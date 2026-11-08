"""`trillion update`: is there a newer Trillion on my channel, and install it (ONB-8).

Nothing here runs unless the user types `trillion update`. There is no check at
startup and none in the background: this is the only place Trillion asks the
internet about itself.

Three channels (see docs/specs/2026-11-07-release-channels.md):

- **stable**: releases tagged `vYYYY.M.PATCH`, on PyPI.
- **beta**: those and `vYYYY.M.PATCH-beta.N` pre-releases, also on PyPI.
- **dev**: the newest build of main, a wheel on the rolling `dev` GitHub release.

Both questions go to GitHub's releases API, the one place that knows all three.
The upgrade itself is the `uv tool install` the installer runs. The channel the
user chose is remembered in the data folder, in a file named `channel`, which the
installers write too.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from trillion import __version__, paths

REPO = "HelloTrillion/trillion"
API = f"https://api.github.com/repos/{REPO}"
CHANNELS = ("stable", "beta", "dev")
DEFAULT = "stable"
TIMEOUT_S = 15.0
INSTALL_SH = f"curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | sh"
INSTALL_PS1 = f"irm https://raw.githubusercontent.com/{REPO}/main/install.ps1 | iex"
# The beta channel asks for any version, pre-releases included. uv up to about 0.12
# picks a pre-release when the requirement names one; newer uv also needs
# --prerelease-package, which keeps every dependency on its releases. Older uv
# doesn't know that option, so its help is asked first (`scoped_prereleases`).
ANY_BETA = ">=0.0.0b0"
PRERELEASE = ["--prerelease-package", "trillion=allow"]

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")
_PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:b(\d+))?(?:\.dev(\d+))?$")
_WHEEL = re.compile(r"^trillion-([^-]+)-py3-none-any\.whl$")

Runner = Callable[[Sequence[str]], int]


class UpdateError(Exception):
    """Said to the user as it is. Never holds anything private."""


def channel_path() -> Path:
    return paths.home() / "channel"


def remembered() -> str:
    """The channel the user installed from, or stable."""
    try:
        word = channel_path().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return DEFAULT
    return word if word in CHANNELS else DEFAULT


def remember(channel: str) -> None:
    if channel not in CHANNELS:
        raise ValueError(channel)
    home = paths.home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    channel_path().write_text(channel + "\n", encoding="utf-8")


def key(version: str) -> tuple[float, ...] | None:
    """Sort order for a PEP 440 version or a release tag. None if neither."""
    if m := _TAG.match(version):
        y, mo, p, b = m.groups()
        d = None
    elif m := _PEP440.match(version):
        y, mo, p, b, d = m.groups()
    else:
        return None
    beta = math.inf if b is None else int(b)
    dev = math.inf if d is None else int(d)
    return (int(y), int(mo), int(p), beta, dev)


def pep440(tag: str) -> str:
    """`v2026.11.0-beta.1` → `2026.11.0b1`, as PyPI and `trillion --version` say it."""
    m = _TAG.match(tag)
    if not m:
        return tag
    y, mo, p, b = m.groups()
    return f"{y}.{mo}.{p}" + (f"b{b}" if b else "")


@dataclass(frozen=True)
class Release:
    version: str  # as PEP 440
    page: str  # the release's page on GitHub
    wheel: str | None = None  # for dev: the wheel to install


def _get(client: httpx.Client, url: str) -> Any:
    try:
        r = client.get(url)
    except httpx.HTTPError:
        raise UpdateError("couldn't reach GitHub to check. Are you online?") from None
    if r.status_code == 404:
        return None
    if r.status_code in (403, 429):
        raise UpdateError("GitHub is limiting how often this can be asked. Try again in an hour.")
    if r.status_code != 200:
        raise UpdateError(f"GitHub answered {r.status_code}. Try again later.")
    try:
        return r.json()
    except ValueError:
        raise UpdateError("GitHub's answer wasn't readable. Try again later.") from None


def newest(channel: str, client: httpx.Client) -> Release | None:
    """The newest release on a channel, or None if it has none yet."""
    if channel == "dev":
        found = _get(client, f"{API}/releases/tags/dev")
        if not isinstance(found, dict):
            return None
        for asset in found.get("assets") or []:
            m = _WHEEL.match(str(asset.get("name", "")))
            if m and key(m.group(1)):
                return Release(
                    m.group(1), str(found.get("html_url", "")), asset.get("browser_download_url")
                )
        return None
    listed = _get(client, f"{API}/releases?per_page=100")
    best: tuple[tuple[float, ...], Release] | None = None
    for item in listed if isinstance(listed, list) else []:
        tag = str(item.get("tag_name", ""))
        k = key(tag) if _TAG.match(tag) else None
        if k is None or item.get("draft"):
            continue
        # The tag says the channel, not GitHub's pre-release mark: the 0.x releases
        # are marked pre-release but are plain versions on PyPI.
        if channel == "stable" and "-beta." in tag:
            continue
        if best is None or k > best[0]:
            best = (k, Release(pep440(tag), str(item.get("html_url", ""))))
    return best[1] if best else None


def client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_S,
        follow_redirects=True,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"trillion/{__version__} (trillion update)",
        },
    )


def report(channel: str, installed: str, latest: Release | None) -> tuple[str, bool]:
    """What to say, and whether a newer version is there."""
    head = f"trillion {installed}, on the {channel} channel.\n"
    if latest is None:
        if channel == "dev":
            return head + "There is no dev build yet.\n", False
        return head + f"There is no {channel} release yet.\n", False
    have, got = key(installed), key(latest.version)
    if have is not None and got is not None and got <= have:
        return head + f"That is the newest on {channel}.\n", False
    page = f" ({latest.page})" if latest.page else ""
    return (
        head + f"Newer on {channel}: {latest.version}{page}\nTo install it: trillion update\n",
        True,
    )


def _find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    exe = "uv.exe" if os.name == "nt" else "uv"
    folders = [Path(os.environ[n]) for n in ("UV_INSTALL_DIR", "XDG_BIN_HOME") if os.environ.get(n)]
    folders += [Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"]
    for candidate in (f / exe for f in folders):
        if candidate.is_file():
            return str(candidate)
    return None


def extras(listing: str) -> str:
    """The extras Trillion was installed with, from `uv tool list --show-extras`."""
    for line in listing.splitlines():
        if line.startswith("trillion v"):
            m = re.search(r"\[extras: ([^\]]*)\]", line)
            return m.group(1).replace(" ", "") if m else ""
    return ""


def command(
    uv: str,
    channel: str,
    latest: Release | None,
    with_extras: str,
    *,
    switching: bool = False,
    scoped: bool = False,
) -> list[str]:
    """The `uv tool install` that puts the newest of a channel in place, keeping extras.

    Moving to another channel starts afresh (`--reinstall`): `--upgrade` alone keeps
    an installed beta, and moving to stable means the newest stable. `scoped`: this
    uv has --prerelease-package, which newer uv needs for a beta."""
    name = f"trillion[{with_extras}]" if with_extras else "trillion"
    if channel == "dev":
        if latest is None or not latest.wheel:
            raise UpdateError("there is no dev build to install yet.")
        return [
            uv,
            "tool",
            "install",
            "--reinstall",
            "--python",
            "3.13",
            f"{name} @ {latest.wheel}",
        ]
    fresh = "--reinstall" if switching else "--upgrade"
    if channel == "beta":
        extra = PRERELEASE if scoped else []
        return [uv, "tool", "install", fresh, *extra, "--python", "3.13", name + ANY_BETA]
    return [uv, "tool", "install", fresh, "--python", "3.13", name]


def scoped_prereleases(uv: str) -> bool:
    """Whether this uv knows --prerelease-package (newer uv needs it for a beta)."""
    try:
        done = subprocess.run(  # noqa: S603 - uv's own path and fixed arguments, no shell
            [uv, "tool", "install", "--help"], capture_output=True, text=True, check=False
        )
    except OSError:
        return False
    return "--prerelease-package" in done.stdout


def _listing(uv: str) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - uv's own path and fixed arguments, no shell
            [uv, "tool", "list", "--show-extras"], capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return done.stdout if done.returncode == 0 else ""


def _run(cmd: Sequence[str]) -> int:
    try:
        # uv's own path and arguments built here, no shell.
        return subprocess.run(list(cmd), check=False).returncode  # noqa: S603
    except OSError:
        return 127


def run(
    *,
    check_only: bool,
    channel: str | None,
    http: httpx.Client | None = None,
    uv: str | None = None,
    listing: Callable[[str], str] = _listing,
    scoped: Callable[[str], bool] = scoped_prereleases,
    runner: Runner = _run,
    out: Any = None,
    err: Any = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    chosen = channel or remembered()
    owned = http is None
    http = http or client()
    failed = False
    try:
        latest = newest(chosen, http)
    except UpdateError as e:
        # The check failed. For stable and beta uv can still ask PyPI itself.
        if check_only or chosen == "dev":
            err.write(f"trillion: {e} Nothing was changed.\n")
            return 1
        err.write(f"trillion: {e} Asking uv anyway.\n")
        latest, failed = None, True
    finally:
        if owned:
            http.close()
    text, newer = report(chosen, __version__, latest)
    if check_only:
        out.write(text)
        return 0
    switching = channel is not None and channel != remembered()
    # Nothing to install: the channel has no release yet, or this is its newest.
    if not failed and (latest is None or (not newer and not switching)):
        out.write(text)
        return 0
    if paths.source_checkout():
        err.write(
            "trillion: this is a source checkout, so uv doesn't manage it. "
            "Update it with: git pull && uv sync\n"
        )
        return 1
    uv = uv or _find_uv()
    if not uv:
        err.write(
            f"trillion: uv isn't on this machine. Run the installer again:\n  {_installer()}\n"
        )
        return 1
    try:
        cmd = command(
            uv,
            chosen,
            latest,
            extras(listing(uv)),
            switching=switching,
            scoped=chosen == "beta" and scoped(uv),
        )
    except UpdateError as e:
        err.write(f"trillion: {e}\n")
        return 1
    if latest is not None:
        out.write(f"Updating trillion {__version__} to {latest.version} ({chosen}).\n")
    out.write("Running: " + " ".join(_shown(c) for c in cmd) + "\n")
    out.flush()
    code = runner(cmd)
    if code != 0:
        err.write(
            "trillion: uv couldn't install it (see above). On Windows, close Trillion "
            "first and run that line yourself. If your uv is old, run 'uv self update'.\n"
        )
        return 1
    if channel is not None:
        remember(channel)
    out.write(f"Done. Restart Trillion to use the new version. Channel: {chosen}.\n")
    return 0


def _shown(part: str) -> str:
    return f"'{part}'" if any(c in part for c in " []<>=@") else part


def _installer() -> str:
    return INSTALL_PS1 if os.name == "nt" else INSTALL_SH
