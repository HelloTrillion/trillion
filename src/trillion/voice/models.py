"""Model files that run on this machine: fetched once, from a pinned address, and
checked before they are ever loaded (ONB-7).

Nothing here runs during a conversation. A download happens when the user asks
for it in settings, each file is compared with the checksum written in this
repository, and a file that does not match is deleted, not used.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from trillion.voice.types import VoiceError

_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
_CHUNK = 1 << 20


@dataclass(frozen=True, kw_only=True)
class ModelFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True, kw_only=True)
class ModelPack:
    """One model: where its files are, pinned to an exact revision, and what they must be."""

    id: str
    base_url: str
    files: tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def installed(pack: ModelPack, folder: Path) -> bool:
    """Present and the right size. Contents were verified when they were written."""
    try:
        return all((folder / f.name).stat().st_size == f.size for f in pack.files)
    except OSError:
        return False


async def download(
    pack: ModelPack,
    folder: Path,
    *,
    client: httpx.AsyncClient | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Fetch what is missing into `folder`. Raises VoiceError and leaves no partial file."""
    folder.mkdir(parents=True, exist_ok=True)
    http = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    done = 0
    try:
        for f in pack.files:
            target = folder / f.name
            if target.is_file() and target.stat().st_size == f.size:
                done += f.size
                continue
            # A name may sit in a subfolder of the model's own (`onnx/model.onnx`).
            target.parent.mkdir(parents=True, exist_ok=True)
            part = folder / (f.name + ".part")
            digest = hashlib.sha256()
            written = 0
            try:
                async with http.stream("GET", f"{pack.base_url}/{f.name}") as r:
                    if r.status_code != 200:
                        raise VoiceError("download_failed", f"HTTP {r.status_code}")
                    with part.open("wb") as out:
                        async for chunk in r.aiter_bytes(_CHUNK):
                            written += len(chunk)
                            if written > f.size:
                                raise VoiceError("download_mismatch", "larger than expected")
                            digest.update(chunk)
                            out.write(chunk)
                            if progress is not None:
                                progress(done + written, pack.size)
                if written != f.size or digest.hexdigest() != f.sha256:
                    raise VoiceError("download_mismatch", "checksum or size differs")
                os.replace(part, target)
            except httpx.TimeoutException as e:
                raise VoiceError("timeout", type(e).__name__) from e
            except httpx.HTTPError as e:
                raise VoiceError("unreachable", type(e).__name__) from e
            except OSError as e:
                raise VoiceError("disk", type(e).__name__) from e
            finally:
                part.unlink(missing_ok=True)
            done += f.size
    finally:
        if client is None:
            await http.aclose()
