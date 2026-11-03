"""SPN-4: a pure floor under every path a tool touches, on the normal AND the confirmed path.

Names are compared folded, not lowercased: macOS volumes are case- AND
normalization-insensitive, so `.\u017fsh` (long s), `.\u00dfh` and `.SSH` all open `.ssh`.
Symlinks are resolved before any comparison, the on-disk spelling of the path is
checked as well as the one asked for, and anything that is not a usable path
string fails closed.
"""

from __future__ import annotations

import functools
import ipaddress
import os
import re
import socket
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath

from trillion import paths
from trillion.spine.contract import JSON, Risk, ToolDefinition

try:
    import fcntl
except ImportError:  # Windows: resolve() already returns the on-disk spelling there.
    _F_GETPATH: int | None = None
else:
    # macOS only: ask the kernel for the path of an open descriptor, as stored on disk.
    _F_GETPATH = getattr(fcntl, "F_GETPATH", None)

# What a write in a folder shared `read` is told (the shared folders spec, D11).
READ_ONLY = (
    "that folder is shared read only; change it to read and change in Settings to let her "
    "write there"
)

PROTECTED_SEGMENTS: frozenset[str] = frozenset({".git", ".ssh", ".aws", ".gnupg"})

_MAXPATHLEN = 1024


@dataclass(frozen=True)
class Denial:
    arg: str
    reason: str


@dataclass(frozen=True)
class DenyPolicy:
    protected_roots: tuple[Path, ...]

    @classmethod
    def default(cls) -> DenyPolicy:
        return cls(
            protected_roots=(
                paths.home().expanduser().resolve(),
                paths.package_root(),
                *credential_stores(),
            )
        )


def credential_stores() -> tuple[Path, ...]:
    """Where each operating system keeps its keychain, held on every OS (SEC-10).

    A home folder copied from another machine, or a disk mounted from one, carries
    its stores along. Moved data folders are followed, and the usual spots are kept
    too, since a variable can change after this is read.
    """
    home = Path.home()
    share = [home / ".local" / "share"]
    roaming = [home / "AppData" / "Roaming"]
    local = [home / "AppData" / "Local"]
    for var, into in (("XDG_DATA_HOME", share), ("APPDATA", roaming), ("LOCALAPPDATA", local)):
        if moved := os.environ.get(var):
            into.append(Path(moved))
    found = [
        home / "Library" / "Keychains",  # macOS
        home / ".kde" / "share" / "apps" / "kwallet",  # KWallet 4
        home / ".kde4" / "share" / "apps" / "kwallet",
        *(d / name for d in share for name in ("keyrings", "kwalletd")),  # Secret Service, KWallet
        # Credential Manager, DPAPI master keys, and the Vault.
        *(d / "Microsoft" / n for d in roaming for n in ("Credentials", "Protect", "Vault")),
        *(d / "Microsoft" / n for d in local for n in ("Credentials", "Vault")),
    ]
    out: list[Path] = []
    for p in found:
        try:
            r = p.expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if r not in out:
            out.append(r)
    return tuple(out)


def fold(s: str) -> str:
    """NFKD, case-fold, NFKD again: long s (U+017F) -> s, sharp s -> ss, full-width -> ASCII."""
    return unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", s).casefold())


_FOLDED_SEGMENTS: frozenset[str] = frozenset(fold(s) for s in PROTECTED_SEGMENTS)


def _folded_parts(p: PurePath) -> tuple[str, ...]:
    if isinstance(p, PureWindowsPath):
        # Windows drops trailing dots and spaces: `Credentials. ` opens `Credentials`.
        return tuple(fold(part.rstrip(". ") or part) for part in p.parts)
    return tuple(fold(part) for part in p.parts)


def is_under(p: PurePath, root: PurePath) -> bool:
    """Under `root` by any spelling: for what is protected, refusing too much is safe."""
    rp, rr = _folded_parts(p), _folded_parts(root)
    return rp[: len(rr)] == rr


# Whether one folder answers to names that differ only in case. On Linux `Docs` and
# `docs` are two folders: a grant of one must not reach into the other, or the card
# would show one file while another is changed.
CASE_BLIND = sys.platform in ("darwin", "win32")


def inside(p: Path, root: Path) -> bool:
    """Under a granted folder, comparing names the way this system's disks do."""
    if CASE_BLIND:
        return is_under(p, root)
    rp, rr = p.parts, root.parts
    return rp[: len(rr)] == rr


def _has_stream(p: PurePath) -> bool:
    """A colon in a name after the drive. On Windows `.env::$DATA` is `.env` itself, and
    `.git::$INDEX_ALLOCATION` the folder `.git`, so no name rule could see them."""
    names = p.parts[1:] if p.anchor else p.parts
    return any(":" in name for name in names)


def _is_env_file(leaf: str) -> bool:
    return leaf in (".env", ".envrc") or leaf.startswith(".env.") or leaf.endswith(".env")


def _resolve(raw: str) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


# How a folder is opened to ask its on-disk spelling; tests put one that blocks.
_open = os.open


def _on_disk(existing: Path) -> Path:
    # The platform check lets type checkers on Windows skip the macOS-only branch.
    if sys.platform == "win32" or _F_GETPATH is None:
        return existing
    try:
        # O_NONBLOCK: a FIFO must not hang the check.
        fd = _open(existing, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return existing
    try:
        buf = fcntl.fcntl(fd, _F_GETPATH, bytes(_MAXPATHLEN))
    except OSError:
        return existing
    finally:
        os.close(fd)
    raw = buf.split(b"\0", 1)[0]
    return Path(os.fsdecode(raw)) if raw else existing


def canonical(p: Path, *, open_ok: bool = True) -> Path:
    """The on-disk spelling of the longest existing prefix of `p`, plus the rest as given.

    Asking the spelling opens the folder. On macOS, opening Desktop, Documents,
    Downloads, iCloud or another drive waits until the user answers a privacy prompt,
    so code that only names such a folder (the browser's places and its fence) passes
    `open_ok=False` and keeps the spelling it was given."""
    if not open_ok:
        return p
    prefix, tail = p, []
    while not prefix.exists() and prefix != prefix.parent:
        tail.append(prefix.name)
        prefix = prefix.parent
    return _on_disk(prefix).joinpath(*reversed(tail))


def spellings(p: Path) -> tuple[Path, ...]:
    """The path as given and, when it differs, as it is spelled on disk."""
    c = canonical(p)
    return (p,) if c == p else (p, c)


# A UNC path to this computer reaches its own disks under another name, so any
# spelling of it is refused: loopback and unspecified addresses in any form
# inet_aton reads (2130706433, 0x7f000001, 127.1), localhost names, and this
# machine's own names and addresses.


def _address(s: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    s = s.strip("[]")
    if s.endswith(".ipv6-literal.net"):  # Windows spells IPv6 in UNC this way
        s = s[: -len(".ipv6-literal.net")].replace("-", ":").replace("s", "%")
    try:
        return ipaddress.ip_address(s.split("%")[0])
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(s))
    except (OSError, ValueError):
        return None


@functools.cache
def _own_names() -> tuple[frozenset[str], frozenset[str]]:
    """This machine's names, folded, and its addresses. Looked up once, when first needed."""
    names: set[str] = set()
    addresses: set[str] = set()
    try:
        host = socket.gethostname()
        names.update({host, socket.getfqdn(), socket.getfqdn(host)})
        for info in socket.getaddrinfo(host, None):
            addresses.add(str(info[4][0]).split("%")[0])
    except (OSError, UnicodeError):
        pass
    for n in list(names):
        short = n.split(".")[0]
        names.update({short, short[:15]})  # the NetBIOS name is at most 15 characters
    return frozenset(fold(n).rstrip(". ") for n in names if n), frozenset(addresses)


def _this_computer(server: str) -> bool:
    s = fold(server).rstrip(". ")
    if not s or s in {".", "?"} or s.startswith("localhost"):
        return True
    names, addresses = _own_names()
    ip = _address(s)
    if ip is not None:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return ip.is_loopback or ip.is_unspecified or str(ip) in addresses
    return s in names


def plain_windows(p: PureWindowsPath) -> PureWindowsPath | None:
    r"""The plain drive spelling of a Windows path, or None for one that must be refused.

    `\\?\C:\x` is `C:\x`, and `\\?\UNC\server\share` is `\\server\share`. Device
    paths (`\\.\`), volume paths, admin shares (`C$`) and shares of this computer
    itself reach the local disks under another name, so they are refused outright."""
    drive, rest = p.drive, p.parts[1:]
    if drive.startswith("\\\\?\\"):
        inner = drive[4:]
        if re.fullmatch(r"[A-Za-z]:", inner):
            return PureWindowsPath(inner + "\\", *rest)
        if not fold(inner).startswith("unc\\"):
            return None  # a volume GUID, GLOBALROOT and the like
        p = PureWindowsPath("\\\\" + inner[4:] + "\\", *rest)
        drive = p.drive
    if drive.startswith("\\\\.\\"):
        return None
    if drive.startswith("\\\\"):
        server, _, share = drive[2:].partition("\\")
        if not share or share.endswith("$") or _this_computer(server):
            return None
    return p


def _root_forms(root: PurePath) -> tuple[PurePath, ...]:
    return spellings(root) if isinstance(root, Path) else (root,)


def protected_reason(p: PurePath, protected_roots: tuple[PurePath, ...]) -> str | None:
    """Why one spelling of a resolved path is protected wherever it is, or None. Shared
    with the rule for folders that can never be shared, so the two never drift."""
    if isinstance(p, PureWindowsPath):
        plain = plain_windows(p)
        if plain is None:
            return "a device path or a share of this computer is not allowed"
        p = plain
    if _has_stream(p):
        return "a name with a colon in it is not allowed"
    folded = _folded_parts(p)
    hit = _FOLDED_SEGMENTS.intersection(folded)
    if hit:
        return f"the path touches a protected directory ({sorted(hit)[0]})"
    if folded and _is_env_file(folded[-1]):
        return "environment files are protected"
    for root in protected_roots:
        if any(is_under(p, r) for r in _root_forms(root)):
            return "Trillion's own files are protected"
    return None


def root_forms(root: Path) -> tuple[Path, ...]:
    """The spellings of a granted folder, or none when resolving it changes it. Shares
    are kept resolved, so a change means a folder on the way was swapped for a link
    since: the share reaches nothing, rather than wherever the link now leads."""
    given = root.expanduser()
    try:
        real = given.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return ()
    if real != given and not (CASE_BLIND and fold(str(real)) == fold(str(given))):
        return ()
    return spellings(real)


def _check_one(
    name: str, p: Path, policy: DenyPolicy, granted_roots: tuple[Path, ...]
) -> Denial | None:
    reason = protected_reason(p, policy.protected_roots)
    if reason is not None:
        return Denial(name, reason)
    grants = [s for g in granted_roots for s in root_forms(g)]
    if not any(inside(p, g) for g in grants):
        return Denial(name, "the path is outside the folders the user granted")
    return None


def refused(
    p: Path, granted_roots: tuple[Path, ...], *, read_only_roots: tuple[Path, ...] = ()
) -> str | None:
    """Why a path already resolved is refused, or None. For the file tools, on the very
    path they are about to walk to, after any folder on the way may have changed. A
    write passes `read_only_roots`, so a link swapped in to a read only share is caught
    here too (D11)."""
    policy = DenyPolicy.default()  # resolved once for every spelling
    for form in spellings(p):
        denial = _check_one("path", form, policy, granted_roots)
        if denial is not None:
            return denial.reason
        if read_only_roots and read_only(form, granted_roots, read_only_roots):
            return READ_ONLY
    return None


def read_only(p: Path, granted_roots: tuple[Path, ...], read_only_roots: tuple[Path, ...]) -> bool:
    """Whether the innermost share holding `p` is read only. Where shares nest, the
    innermost decides; two of the same folder, one of them read only, are read only."""
    deepest, locked = -1, False
    shares = [(g, False) for g in granted_roots] + [(r, True) for r in read_only_roots]
    for root, read_only in shares:
        for form in root_forms(root):
            if not inside(p, form):
                continue
            depth = len(form.parts)
            if depth > deepest:
                deepest, locked = depth, read_only
            elif depth == deepest:
                locked = locked or read_only
    return locked


def _local(raw: str) -> bool:
    """A full path on this computer. A relative one given to a connected server names
    a place in its own world, not one under the user's shares."""
    s = raw.strip()
    return s.startswith(("/", "~", "\\\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", s))


def reads_as_path(raw: str) -> bool:
    """Whether a value names a place on a disk, rather than an id or an address."""
    s = raw.strip()
    if "://" in s:
        return False
    return (
        s.startswith(("/", "~", "./", "../", "\\\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", s))
        or "/" in s
        or "\\" in s
    )


def check(
    defn: ToolDefinition,
    args: JSON,
    policy: DenyPolicy,
    granted_roots: tuple[Path, ...],
    *,
    read_only_roots: tuple[Path, ...] = (),
) -> Denial | None:
    """`read_only_roots` are the shares that are `read` only (D11). Any tool that is
    not a read counts as a write there, so a connected server or a skill naming a path
    is held the same as the file tools."""
    writes = defn.risk is not Risk.READ and defn.writes_paths and bool(read_only_roots)
    for name in defn.outside_path_args:
        raw = args.get(name)
        if not isinstance(raw, str) or not reads_as_path(raw):
            continue
        if "\x00" in raw:
            return Denial(name, "the path could not be resolved")
        try:
            forms = spellings(_resolve(raw))
        except (OSError, RuntimeError, ValueError):
            return Denial(name, "the path could not be resolved")
        # Protected wherever it is, granted folder or not (SEC-10): every form is
        # "granted" its own drive, so only the protected rules can refuse it.
        everywhere = tuple({Path(f.anchor) for f in forms if f.anchor})
        for form in forms:
            denial = _check_one(name, form, policy, everywhere)
            if denial is not None:
                return denial
            if writes and _local(raw) and read_only(form, granted_roots, read_only_roots):
                return Denial(name, READ_ONLY)
    for name in defn.path_args:
        if name not in args:
            continue
        raw = args[name]
        if not isinstance(raw, str) or not raw.strip():
            return Denial(name, "a path must be a non-empty string")
        # Windows resolves a NUL without complaint; fail closed everywhere instead.
        if "\x00" in raw:
            return Denial(name, "the path could not be resolved")
        try:
            p = _resolve(raw)
            forms = spellings(p)
        except (OSError, RuntimeError, ValueError):
            return Denial(name, "the path could not be resolved")
        # Deny if EITHER the spelling asked for or the one on disk hits a rule.
        for form in forms:
            denial = _check_one(name, form, policy, granted_roots)
            if denial is not None:
                return denial
            if writes and read_only(form, granted_roots, read_only_roots):
                return Denial(name, READ_ONLY)
    return None
