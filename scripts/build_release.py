#!/usr/bin/env python3
"""Build the wheel that testers and users install: the app, its page, and our Google app.

A source checkout serves the page from apps/web/dist and has no Google app of its
own. An installed wheel has neither unless the build puts them in. So this:

1. copies the built page (apps/web/dist) into the package, as trillion/api/static;
2. writes the distribution's Google app, connectors/google/google_client.json, from
   the GOOGLE_CLIENT_JSON environment variable (a CI secret), or uses the file
   already there on a maintainer's machine;
3. builds the wheel into dist-release/;
4. checks the wheel: the page and the Google app are in it, nothing private is,
   and installed on its own it finds both.

Never prints the Google app's values. Run it after building the page:

    pnpm install && pnpm --filter @hellotrillion/web build
    uv run python scripts/build_release.py [--tag v0.0.1] [--require-google]

The copied page and a Google file written from the environment are removed again
afterwards, so a checkout keeps serving apps/web/dist and keeps no secret.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "apps" / "web" / "dist"
STATIC = ROOT / "src" / "trillion" / "api" / "static"
GOOGLE = ROOT / "src" / "trillion" / "connectors" / "google" / "google_client.json"
OUT = ROOT / "dist-release"
ENV = "GOOGLE_CLIENT_JSON"

CLIENT_ID = re.compile(r"^[0-9]+-[0-9a-z]+\.apps\.googleusercontent\.com$")
PICKER_KEY = re.compile(r"^AIza[0-9A-Za-z_-]{35}$")
# What must never be inside a wheel, whatever the patterns below would say.
NEVER = re.compile(r"(^|/)(\.env(\..*)?|.*\.db|.*\.sqlite3|USER\.md|IDENTITY\.md)$")


class ReleaseError(Exception):
    """Something is wrong with the build. The message never holds a secret."""


def version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def google_file(text: str, *, require_picker: bool = False) -> str:
    """The file to ship, from what Google gave us: only the fields sign-in needs.

    A release needs the Drive picker's key too: without it Gmail and Calendar work
    but choosing Drive files does not, and nothing else would say so."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ReleaseError(f"{ENV} is not JSON") from None
    inner = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        raise ReleaseError(f'{ENV} has no "installed" client (a Desktop app client)')
    client_id = str(inner.get("client_id", ""))
    secret = str(inner.get("client_secret", ""))
    if not CLIENT_ID.match(client_id):
        raise ReleaseError(f"{ENV} has no valid client_id")
    if not secret or any(c.isspace() for c in secret):
        raise ReleaseError(f"{ENV} has no valid client_secret")
    shipped: dict[str, object] = {"installed": {"client_id": client_id, "client_secret": secret}}
    picker = str(data.get("picker_key", ""))
    if require_picker and not picker:
        raise ReleaseError(
            f"{ENV} has no picker_key (the Drive picker's API key, added at the top level)"
        )
    if picker:
        if not PICKER_KEY.match(picker):
            raise ReleaseError(f"{ENV} has a picker_key that is not a Google API key")
        shipped["picker_key"] = picker
    return json.dumps(shipped, indent=2) + "\n"


@contextmanager
def staged(*, require_google: bool) -> Iterator[bool]:
    """The page and the Google app in place for the build; cleaned up after."""
    if not (PAGE / "index.html").is_file():
        raise ReleaseError("the page is not built: pnpm --filter @hellotrillion/web build")
    from_env = os.environ.get(ENV, "").strip()
    if from_env and GOOGLE.exists():
        raise ReleaseError(f"{ENV} is set and {GOOGLE.name} already exists: use one or the other")
    if require_google and not from_env and not GOOGLE.exists():
        raise ReleaseError(f"no Google app: set the {ENV} secret")
    shutil.rmtree(STATIC, ignore_errors=True)
    shutil.copytree(PAGE, STATIC)
    wrote = False
    try:
        if from_env:
            GOOGLE.write_text(
                google_file(from_env, require_picker=require_google), encoding="utf-8"
            )
            wrote = True
        elif GOOGLE.exists():
            # Check it, by the same rules.
            google_file(GOOGLE.read_text(encoding="utf-8"), require_picker=require_google)
        yield GOOGLE.exists()
    finally:
        shutil.rmtree(STATIC, ignore_errors=True)
        if wrote:
            GOOGLE.unlink(missing_ok=True)


def build() -> Path:
    shutil.rmtree(OUT, ignore_errors=True)
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(OUT)], cwd=ROOT, check=True)
    wheels = list(OUT.glob("trillion-*.whl"))
    if len(wheels) != 1:
        raise ReleaseError(f"expected one wheel in {OUT.name}, found {len(wheels)}")
    return wheels[0]


def private_patterns() -> list[tuple[str, re.Pattern[str]]]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import containment_check  # a sibling script, not a package

    return [
        (category, re.compile(p, re.IGNORECASE))
        for category, found in containment_check.FORBIDDEN.items()
        for p in found
    ]


def inspect(wheel: Path, *, google: bool) -> None:
    """The wheel holds what an installed app needs and nothing private."""
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
        must = {"trillion/api/static/index.html", "trillion/persona/default/SOUL.md"}
        if google:
            must.add("trillion/connectors/google/google_client.json")
        if missing := sorted(must - names):
            raise ReleaseError(f"the wheel is missing {', '.join(missing)}")
        if not google and "trillion/connectors/google/google_client.json" in names:
            raise ReleaseError("the wheel has a Google app nobody asked for")
        if bad := sorted(n for n in names if NEVER.search(n)):
            raise ReleaseError(f"the wheel holds private files: {', '.join(bad)}")
        patterns = private_patterns()
        for name in sorted(names):
            if name.endswith(("google_client.json", ".png", ".woff2", ".woff", ".ico")):
                continue  # the Google app is meant to be here; images are not text
            text = z.read(name).decode("utf-8", errors="ignore")
            for category, pattern in patterns:
                if pattern.search(text):
                    raise ReleaseError(f"{name} matches the {category} check")


SMOKE = """
import sys
from pathlib import Path
from trillion.api.app import web_dir
from trillion.connectors.google.config import client
from trillion.secrets.store import MemoryStore

page = web_dir()
assert page is not None and "site-packages" in str(page), f"page: {page}"
found = client(MemoryStore(), env={})
if sys.argv[1] == "google":
    assert found is not None and found.source == "bundled", "no bundled Google app"
else:
    assert found is None, "a Google app appeared"
print("installed wheel: page", "and Google app" if found else "(no Google app)", "found")
"""


def smoke(wheel: Path, *, google: bool) -> None:
    """Installed into a fresh environment, away from the checkout, it finds both."""
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run(["uv", "venv", "--quiet", str(venv)], check=True)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            ["uv", "pip", "install", "--quiet", "--python", str(python), str(wheel)],
            check=True,
        )
        env = {**os.environ, "TRILLION_HOME": str(Path(tmp) / "home")}
        for name in ("TRILLION_GOOGLE_CLIENT_ID", "TRILLION_GOOGLE_CLIENT_SECRET"):
            env.pop(name, None)
        subprocess.run(
            [str(python), "-c", SMOKE, "google" if google else "none"],
            cwd=tmp,  # not the checkout, so nothing is found by accident
            env=env,
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="the release tag; must be v<version from pyproject>")
    parser.add_argument(
        "--require-google", action="store_true", help="fail without a Google app (releases)"
    )
    args = parser.parse_args(argv)
    try:
        if args.tag and args.tag != f"v{version()}":
            raise ReleaseError(f"tag {args.tag} does not match version {version()}")
        with staged(require_google=args.require_google) as google:
            wheel = build()
        inspect(wheel, google=google)
        smoke(wheel, google=google)
    except ReleaseError as e:
        print(f"Release build FAILED: {e}", file=sys.stderr)
        return 1
    print(f"Built {wheel.relative_to(ROOT)}" + (" with" if google else " without") + " Google")
    return 0


if __name__ == "__main__":
    sys.exit(main())
