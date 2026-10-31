#!/usr/bin/env python3
"""Tiny stand-in wheels for testing the installers' upgrade path, offline.

Writes into DIR: whisperdep 1.0, and trillion at each VERSION given, with a
`whisper` extra that needs whisperdep and a `trillion` command that prints
"trillion <version>". Point uv at DIR with UV_NO_INDEX=1 UV_FIND_LINKS=DIR and
`uv tool install trillion` gets these instead of anything from PyPI.

    python scripts/fake_trillion_wheels.py DIR 0.0.1 [0.0.2 ...]

CI uses it (the installer job) to check that re-running an installer upgrades
Trillion and keeps the extras someone added, such as Whisper.
"""

from __future__ import annotations

import base64
import hashlib
import sys
import zipfile
from pathlib import Path


def wheel(out: Path, name: str, version: str) -> None:
    info = f"{name}-{version}.dist-info"
    files = {f"{name}/__init__.py": f'def main():\n    print("{name} {version}")\n'}
    meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    if name == "trillion":
        meta += 'Provides-Extra: whisper\nRequires-Dist: whisperdep; extra == "whisper"\n'
        files[f"{info}/entry_points.txt"] = "[console_scripts]\ntrillion = trillion:main\n"
    files[f"{info}/METADATA"] = meta
    files[f"{info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: fake\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record = []
    for path, text in files.items():
        digest = hashlib.sha256(text.encode()).digest()
        b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        record.append(f"{path},sha256={b64},{len(text.encode())}")
    record.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = "\n".join(record) + "\n"
    with zipfile.ZipFile(out / f"{name}-{version}-py3-none-any.whl", "w") as z:
        for path, text in files.items():
            z.writestr(path, text)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    wheel(out, "whisperdep", "1.0")
    for version in argv[1:]:
        wheel(out, "trillion", version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
