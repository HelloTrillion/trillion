"""Open a file only beneath a granted folder, however the path changes underneath (#2).

The deny floor checks where a path resolves when an action is proposed and again
when it is approved. A file tool then opens the path, and between the two a folder
on the way can be swapped for a link: `granted/sub` becomes a link to `~/.ssh`,
and the write lands there. Refusing a link only at the last step does not help;
the link is in the middle.

So the file tools never open a path by name. They resolve it once, find the granted
folder it is under, open that folder, and walk down from it one name at a time with
`O_NOFOLLOW`, each step opened relative to the folder before it (`dir_fd`). A link
anywhere on the way, put there after the check, stops the walk. Links the user
already had are still fine: resolving first turns them into the real path, and only
a link that appears after that is refused.

A file with more than one hard link is refused for reading and appending: a second
name for `~/.ssh/authorized_keys` inside the granted folder is the same file, and
no check on the name can see that.

Windows has no `dir_fd`. There each step is checked for a link or a junction just
before the file is opened, which leaves a window too small to aim for but not none.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path

from trillion.spine import deny_floor

# Where the walk can be done properly: every step opened relative to the last.
WALKS = os.name == "posix" and os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)


class Escaped(OSError):
    """The path left the granted folder, or changed into something that would."""


def locate(
    path: Path,
    roots: Sequence[Path],
    *,
    folder: bool = False,
    read_only: Sequence[Path] = (),
) -> tuple[Path, tuple[str, ...]]:
    """The granted folder `path` is under, and the names from it down to the file.
    `folder` allows the granted folder itself, with no names below it. A write passes
    the shares that are read only: where a link on the way now leads into one, it is
    refused here, as the deny floor would.

    Links the user already has on the way are followed once, here, and the deny floor
    is applied to where they lead: a folder swapped for a link to `.git` after the
    tool's own check is refused here, and the walk refuses any link after this.
    Granted folders are matched the way the deny floor matches them."""
    given = path.expanduser()
    # The folders on the way are resolved; the last name is not, so a link there is
    # met by the walk and refused rather than followed. A folder being listed is the
    # last name, and is resolved like the rest.
    resolved = (
        given.resolve(strict=False) if folder else given.parent.resolve(strict=False) / given.name
    )
    for root in roots:
        forms = deny_floor.root_forms(root)
        # A granted folder now reached through a link reaches nothing (the deny floor).
        if not forms:
            continue
        top = forms[0]
        if deny_floor.inside(resolved, top):
            names = resolved.parts[len(top.parts) :]
            if (names or folder) and all(name not in ("", ".", "..") for name in names):
                place = top.joinpath(*names)
                reason = deny_floor.refused(place, tuple(roots), read_only_roots=tuple(read_only))
                if reason is not None:
                    raise Escaped(reason)
                return top, names
    raise Escaped("the path is not inside a folder the user granted")


def _checked(top: Path, names: Sequence[str]) -> Path:
    """Without `dir_fd`: each step checked for a link or a junction, as late as can be."""
    here = top
    for name in names:
        here = here / name
        if here.is_symlink() or here.is_junction() or not here.is_dir():
            raise Escaped(f"{name} is a link, or not a folder")
    return here


def _walk(top: Path, names: Sequence[str]) -> int:
    """A descriptor for `top/names...`, every step opened relative to the one before
    and refused if it is a link. The caller closes it."""
    fd = os.open(top, os.O_RDONLY | _DIRECTORY | _CLOEXEC)
    for name in names:
        try:
            step = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=fd)
        except OSError as e:
            os.close(fd)
            # ELOOP or ENOTDIR: a link, or a file, where a folder was.
            raise Escaped(f"{name} is a link, or not a folder") from e
        os.close(fd)
        fd = step
    return fd


@contextlib.contextmanager
def parent(
    path: Path, roots: Sequence[Path], read_only: Sequence[Path] = ()
) -> Iterator[tuple[int | None, Path, str]]:
    """The folder that holds `path`, opened by walking down from its granted folder.

    Yields (descriptor, folder path, final name). The descriptor is None where the
    walk cannot be done (Windows); each step has then been checked for a link."""
    top, names = locate(path, roots, read_only=read_only)
    if not WALKS:
        yield None, _checked(top, names[:-1]), names[-1]
        return
    fd = _walk(top, names[:-1])
    try:
        yield fd, top.joinpath(*names[:-1]), names[-1]
    finally:
        os.close(fd)


def _regular(fd: int, *, single: bool) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise Escaped("that is not an ordinary file")
    if single and info.st_nlink > 1:
        raise Escaped("that file has another name elsewhere, so it is not opened")


def open_file(
    path: Path,
    roots: Sequence[Path],
    flags: int,
    mode: int = 0o666,
    *,
    single: bool = True,
    read_only: Sequence[Path] = (),
) -> int:
    """A descriptor for the file itself, never a link to it. `single` refuses a file
    with a second hard link; it does not apply to a file this call creates. An open
    that writes passes the shares that are `read_only`."""
    with parent(path, roots, read_only) as (fd, folder, name):
        if fd is None:
            target = folder / name
            if target.is_symlink() or target.is_junction():
                raise Escaped("that is a link: give the path of the file it points to")
            out = os.open(target, flags | _BINARY, mode)
        else:
            try:
                out = os.open(name, flags | _NOFOLLOW | _CLOEXEC, mode, dir_fd=fd)
            except OSError as e:
                if e.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
                    raise Escaped("that is a link: give the path of the file it points to") from e
                raise
    try:
        _regular(out, single=single and not flags & os.O_EXCL)
    except BaseException:
        os.close(out)
        raise
    return out


def read_bytes(path: Path, roots: Sequence[Path], limit: int) -> bytes:
    """At most `limit` bytes of the file, opened beneath its granted folder."""
    fd = open_file(path, roots, os.O_RDONLY)
    with os.fdopen(fd, "rb") as f:
        return f.read(limit)


def replace(
    path: Path,
    roots: Sequence[Path],
    data: bytes,
    *,
    must_exist: bool,
    read_only: Sequence[Path] = (),
) -> None:
    """Write beside the file, keep its mode, then swap it in: never a half-written
    file, and never through a link. The swap replaces the name, not the file it named,
    so a second hard link to the old file is left as it was."""
    with parent(path, roots, read_only) as (fd, folder, name):
        if fd is None:
            target = folder / name
            if target.is_symlink() or target.is_junction():
                raise Escaped("that is a link: give the path of the file it points to")
            current = target.stat().st_mode if target.exists() else None
        else:
            try:
                current = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                current = None
            if current is not None and not stat.S_ISREG(current):
                raise Escaped("that is not an ordinary file")
        if must_exist and current is None:
            raise FileNotFoundError(name)
        temporary = f".{name}.{uuid.uuid4().hex[:8]}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY
        out = (
            os.open(folder / temporary, flags, 0o600)
            if fd is None
            else os.open(temporary, flags, 0o600, dir_fd=fd)
        )
        try:
            with os.fdopen(out, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
                if current is not None:
                    os.chmod(f.fileno() if os.chmod in os.supports_fd else folder / temporary,
                             stat.S_IMODE(current))  # fmt: skip
            if fd is None:
                os.replace(folder / temporary, folder / name)
            else:
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            with contextlib.suppress(OSError):
                if fd is None:
                    os.unlink(folder / temporary)
                else:
                    os.unlink(temporary, dir_fd=fd)
            raise


def entries(path: Path, roots: Sequence[Path]) -> list[tuple[str, bool]]:
    """A folder's names, and which are folders, read through the walked descriptor."""
    top, names = locate(path, roots, folder=True)
    if not WALKS:
        with os.scandir(_checked(top, names)) as found:
            return [(e.name, e.is_dir(follow_symlinks=False)) for e in found]
    fd = _walk(top, names)
    try:
        with os.scandir(fd) as found:
            return [(e.name, e.is_dir(follow_symlinks=False)) for e in found]
    finally:
        os.close(fd)
