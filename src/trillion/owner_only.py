"""Files and folders only their owner can open, on Windows too (SUR-3c).

macOS and Linux keep Trillion's private files with POSIX modes (0700 folders, 0600
files), which Windows ignores. There a file's access list decides, and a new file
takes its folder's. `restrict` gives a path an access list of its own: the current
user and SYSTEM, full control, nothing inherited. A folder's entries are inherited
by what is made inside it, so restricting the data folder covers everything in it,
and the folder's existing contents follow when Windows passes the change down.

It uses the Windows security API through `ctypes`: no process is started and nothing
is installed. Off Windows it does nothing; callers keep setting modes there.

`exposed` answers the other question, on every system: who besides the owner can
open a path. `trillion doctor` asks it about the data folder.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# The well-known SIDs an owner-only path may still list, in their SDDL short forms
# and in full. SYSTEM runs the OS itself; administrators can take any file anyway.
# CREATOR OWNER and OWNER RIGHTS stand for whoever owns the file.
_TRUSTED = {"SY", "S-1-5-18", "BA", "S-1-5-32-544", "CO", "S-1-3-0", "OW", "S-1-3-4"}
# How the common groups read, for a doctor line.
_NAMES = {
    "WD": "Everyone",
    "S-1-1-0": "Everyone",
    "AU": "Authenticated Users",
    "S-1-5-11": "Authenticated Users",
    "BU": "Users",
    "S-1-5-32-545": "Users",
    "IU": "Interactive users",
    "S-1-5-4": "Interactive users",
    "BG": "Guests",
    "S-1-5-32-546": "Guests",
    "AN": "Anonymous",
    "S-1-5-7": "Anonymous",
}
_ACE = re.compile(r"\(([^()]*)\)")
# An ACE that grants: allow, object allow, callback allow.
_ALLOWS = {"A", "OA", "XA", "ZA"}

# Applies an SDDL to a path; raises OSError when Windows refuses.
Apply = Callable[[Path, str], None]


def sddl(user_sid: str, *, folder: bool) -> str:
    """The access list `restrict` gives: protected (P, nothing inherited), full control
    (FA) for the user and SYSTEM. A folder's entries pass to what is made inside it
    (OI: files, CI: folders)."""
    inherit = "OICI" if folder else ""
    return f"D:P(A;{inherit};FA;;;{user_sid})(A;{inherit};FA;;;SY)"


def restrict(path: Path, *, apply: Apply | None = None, user: str | None = None) -> bool:
    """Make `path` the current user's alone on Windows. True when it is (or when there
    is nothing to do, off Windows); False when Windows refused, which is logged and
    never raised: a file system with no access lists (FAT, some network shares) still
    works, only unprotected."""
    if sys.platform != "win32" and apply is None:
        return True
    try:
        sid = user or user_sid()
        if sid is None:
            raise OSError("the current user's SID could not be read")
        (apply or _apply)(path, sddl(sid, folder=path.is_dir()))
    except OSError as e:
        logger.warning("could not make %s private to its owner: %s", path.name, type(e).__name__)
        return False
    return True


def others(descriptor: str, user_sid: str) -> list[str]:
    """Who besides the user (and SYSTEM and administrators) an SDDL lets in, by name."""
    if "NO_ACCESS_CONTROL" in descriptor.split("S:")[0]:
        return ["Everyone"]  # a null DACL lets anyone in
    dacl = descriptor.partition("D:")[2].split("S:")[0]
    found: list[str] = []
    for ace in _ACE.findall(dacl):
        fields = ace.split(";")
        if len(fields) < 6 or fields[0] not in _ALLOWS:
            continue
        who = fields[5]
        if who == user_sid or who in _TRUSTED:
            continue
        name = _NAMES.get(who, "another account")
        if name not in found:
            found.append(name)
    return found


def exposed(
    path: Path, *, read: Callable[[Path], str] | None = None, user: str | None = None
) -> list[str] | None:
    """Who else can open `path`: [] when only its owner can, None when it can't tell.

    POSIX: any group or other permission bit. Windows: its access list."""
    if sys.platform != "win32" and read is None:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            return None
        return [
            *(["its group"] if mode & 0o070 else []),
            *(["everyone on this computer"] if mode & 0o007 else []),
        ]
    try:
        sid = user or user_sid()
        descriptor = (read or _read)(path)
    except OSError:
        return None
    return None if sid is None else others(descriptor, sid)


# ── Windows, through ctypes ──────────────────────────────────────────────

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


def user_sid() -> str | None:
    """The SID of the user this process runs as, like S-1-5-21-…-1001."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        return None
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            return None
        # TOKEN_USER starts with SID_AND_ATTRIBUTES, which starts with the SID pointer.
        sid = ctypes.c_void_p.from_buffer(buffer).value
        text = ctypes.c_void_p()
        if not sid or not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return None
        try:
            return ctypes.wstring_at(text.value) if text.value else None
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def _apply(path: Path, descriptor: str) -> None:
    """Set `path`'s DACL from an SDDL, protected, so nothing is inherited from above.
    SetNamedSecurityInfo passes a folder's inheritable entries down to what is in it."""
    if sys.platform != "win32":
        raise OSError("access lists are set on Windows only")
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    convert.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    descriptor_ptr = ctypes.c_void_p()
    if not convert(descriptor, _SDDL_REVISION_1, ctypes.byref(descriptor_ptr), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor_ptr, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        error = advapi32.SetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        if error:
            raise ctypes.WinError(error)
    finally:
        kernel32.LocalFree(descriptor_ptr)


def _read(path: Path) -> str:
    """`path`'s DACL as SDDL."""
    if sys.platform != "win32":
        raise OSError("access lists are read on Windows only")
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    to_string = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    to_string.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    to_string.restype = wintypes.BOOL

    where = os.fspath(path)
    size = wintypes.DWORD(0)
    advapi32.GetFileSecurityW(where, _DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi32.GetFileSecurityW(
        where, _DACL_SECURITY_INFORMATION, buffer, size, ctypes.byref(size)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    text = ctypes.c_void_p()
    if not to_string(
        buffer, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.wstring_at(text.value) if text.value else ""
    finally:
        kernel32.LocalFree(text)
