"""Is another app recording from the microphone? One function per system.

`host` is where Trillion's own page runs (`desktop`, or a browser family): the
recording processes that belong to it are Trillion's own, so they don't count. A call
in that same browser is invisible here; the meeting rule covers it.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import struct
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from trillion.attention.probes import CACHE, Answer, Run, guarded, run

MIC_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
)


@dataclass(frozen=True)
class Own:
    """What belongs to one host: macOS bundle ids (and their sub-ids), executable names."""

    bundles: tuple[str, ...] = ()
    exes: frozenset[str] = field(default_factory=frozenset)

    def owns_bundle(self, bundle: str) -> bool:
        return any(bundle == b or bundle.startswith(b + ".") for b in self.bundles)

    def owns_exe(self, path: str) -> bool:
        return exe_name(path) in self.exes


HOSTS: dict[str, Own] = {
    # The app itself, and the web view that does its recording on each system.
    "desktop": Own(
        ("ai.hellotrillion.desktop", "com.apple.WebKit"),
        frozenset({"trillion-desktop", "msedgewebview2", "webkitwebprocess"}),
    ),
    "chrome": Own(
        ("com.google.Chrome", "org.chromium.Chromium"),
        frozenset(
            {
                "chrome",
                "google-chrome",
                "google-chrome-stable",
                "google-chrome-beta",
                "google-chrome-unstable",
                "chromium",
                "chromium-browser",
            }
        ),
    ),
    "edge": Own(
        ("com.microsoft.edgemac",),
        frozenset({"msedge", "microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta"}),
    ),
    "firefox": Own(
        ("org.mozilla.firefox", "org.mozilla.nightly", "org.mozilla.plugincontainer"),
        frozenset({"firefox", "firefox-bin", "firefox-esr", "firefox.real"}),
    ),
    "safari": Own(("com.apple.Safari", "com.apple.WebKit")),
    "other": Own(),
}


def own(host: str) -> Own:
    return HOSTS.get(host.lower(), HOSTS["other"])


def exe_name(path: str) -> str:
    """`C:#Program Files#x#Chrome.exe`, `/usr/bin/chrome` or `chrome.exe` → `chrome`."""
    name = path.replace("#", "/").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.removesuffix(".exe")


def _pid(value: object) -> int | None:
    try:
        return int(str(value))
    except ValueError:
        return None


# --- macOS ------------------------------------------------------------------


class CoreAudio(Protocol):
    def processes(self) -> list[int] | None:
        """The audio process objects; None where the system has no such list."""

    def pid(self, obj: int) -> int | None: ...

    def bundle_id(self, obj: int) -> str | None: ...

    def recording(self, obj: int) -> bool | None: ...


def _macos(audio: CoreAudio, host: str, own_pid: int) -> Answer:
    objects = audio.processes()
    if objects is None:
        return None
    mine = own(host)
    for obj in objects:
        if not audio.recording(obj):
            continue
        if audio.pid(obj) == own_pid:
            continue
        bundle = audio.bundle_id(obj) or ""
        if bundle and mine.owns_bundle(bundle):
            continue
        return True
    return False


def _fourcc(code: str) -> int:
    return int(struct.unpack(">I", code.encode("ascii"))[0])


class _Address(ctypes.Structure):
    _fields_ = [
        ("selector", ctypes.c_uint32),
        ("scope", ctypes.c_uint32),
        ("element", ctypes.c_uint32),
    ]


class _MacAudio:
    """CoreAudio's process list (macOS 14.2+), read through ctypes. No permission prompt."""

    SYSTEM = 1  # kAudioObjectSystemObject
    GLOBAL = _fourcc("glob")
    MAIN = 0
    UTF8 = 0x08000100

    def __init__(self) -> None:
        audio, cf = (
            ctypes.util.find_library("CoreAudio"),
            ctypes.util.find_library("CoreFoundation"),
        )
        if audio is None or cf is None:
            raise OSError("CoreAudio not found")
        self._ca = ctypes.CDLL(audio)
        self._cf = ctypes.CDLL(cf)
        address = ctypes.POINTER(_Address)
        size = ctypes.POINTER(ctypes.c_uint32)
        self._ca.AudioObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32, address, ctypes.c_uint32, ctypes.c_void_p, size,
        ]  # fmt: skip
        self._ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        self._ca.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32, address, ctypes.c_uint32, ctypes.c_void_p, size, ctypes.c_void_p,
        ]  # fmt: skip
        self._ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        self._cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32,
        ]  # fmt: skip
        self._cf.CFStringGetCString.restype = ctypes.c_bool
        self._cf.CFRelease.argtypes = [ctypes.c_void_p]
        self._cf.CFRelease.restype = None

    def _address(self, code: str) -> _Address:
        return _Address(_fourcc(code), self.GLOBAL, self.MAIN)

    def _get(self, obj: int, code: str, out: Any) -> bool:
        address = self._address(code)
        size = ctypes.c_uint32(ctypes.sizeof(out))
        status = self._ca.AudioObjectGetPropertyData(
            obj, ctypes.byref(address), 0, None, ctypes.byref(size), ctypes.byref(out)
        )
        return int(status) == 0

    def processes(self) -> list[int] | None:
        # 'prs#': kAudioHardwarePropertyProcessObjectList; older systems refuse it.
        address = self._address("prs#")
        size = ctypes.c_uint32(0)
        status = self._ca.AudioObjectGetPropertyDataSize(
            self.SYSTEM, ctypes.byref(address), 0, None, ctypes.byref(size)
        )
        if int(status) != 0:
            return None
        items = (ctypes.c_uint32 * (size.value // 4))()
        if not self._get(self.SYSTEM, "prs#", items):
            return None
        return [int(i) for i in items]

    def pid(self, obj: int) -> int | None:
        out = ctypes.c_int32()
        return int(out.value) if self._get(obj, "ppid", out) else None

    def recording(self, obj: int) -> bool | None:
        out = ctypes.c_uint32()
        return bool(out.value) if self._get(obj, "piri", out) else None

    def bundle_id(self, obj: int) -> str | None:
        ref = ctypes.c_void_p()
        if not self._get(obj, "pbid", ref) or not ref.value:
            return None
        try:
            text = ctypes.create_string_buffer(1024)
            if not self._cf.CFStringGetCString(ref, text, len(text), self.UTF8):
                return None
            return text.value.decode("utf-8", "replace")
        finally:
            # The caller owns the returned CFString.
            self._cf.CFRelease(ref)


# --- Windows ----------------------------------------------------------------


class Registry(Protocol):
    def subkeys(self, path: str) -> list[str]:
        """The subkey names under HKCU\\path; raises OSError when it doesn't exist."""

    def value(self, path: str, name: str) -> int | None: ...


def _live(registry: Registry, path: str) -> bool:
    # The privacy indicator's rule: started, and not stopped since.
    start = registry.value(path, "LastUsedTimeStart")
    stop = registry.value(path, "LastUsedTimeStop")
    return bool(start) and stop == 0


def _windows(registry: Registry, host: str) -> Answer:
    mine = own(host)
    for name in registry.subkeys(MIC_KEY):
        if name == "NonPackaged":
            base = MIC_KEY + "\\NonPackaged"
            for exe in registry.subkeys(base):
                if not mine.owns_exe(exe) and _live(registry, base + "\\" + exe):
                    return True
        elif _live(registry, MIC_KEY + "\\" + name):
            return True
    return False


if sys.platform == "win32":
    import winreg

    class _WinRegistry:
        def subkeys(self, path: str) -> list[str]:
            names: list[str] = []
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    names.append(winreg.EnumKey(key, i))
            return names

        def value(self, path: str, name: str) -> int | None:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                    found, _ = winreg.QueryValueEx(key, name)
            except OSError:
                return None
            return found if isinstance(found, int) else None


# --- Linux ------------------------------------------------------------------


def _recorders(items: Iterable[dict[str, Any]], host: str, own_pid: int) -> bool:
    mine = own(host)
    for props in items:
        if _pid(props.get("application.process.id")) == own_pid:
            continue
        binary = str(props.get("application.process.binary") or "")
        if binary and mine.owns_exe(binary):
            continue
        return True
    return False


def _pactl(text: str) -> list[dict[str, Any]] | None:
    data = json.loads(text)
    if not isinstance(data, list):
        return None
    found: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or item.get("corked") is True:
            continue  # a paused stream is not recording
        props = item.get("properties")
        if isinstance(props, dict):
            found.append(props)
    return found


def _pw_dump(text: str) -> list[dict[str, Any]] | None:
    data = json.loads(text)
    if not isinstance(data, list):
        return None
    found: list[dict[str, Any]] = []
    for obj in data:
        info = obj.get("info") if isinstance(obj, dict) else None
        if not isinstance(info, dict):
            continue
        props = info.get("props")
        if not isinstance(props, dict) or props.get("media.class") != "Stream/Input/Audio":
            continue
        if info.get("state") != "running":
            continue  # idle or suspended streams hold the device without recording
        found.append(props)
    return found


def _linux(run: Run, host: str, own_pid: int) -> Answer:
    for args, parse in (
        (["pactl", "-f", "json", "list", "source-outputs"], _pactl),
        (["pw-dump"], _pw_dump),
    ):
        text = run(args)
        if text is None:
            continue
        try:
            items = parse(text)
        except ValueError:
            items = None  # garbage: try the next tool
        if items is not None:
            return _recorders(items, host, own_pid)
    return None


# --- entry point ------------------------------------------------------------


def _system(host: str) -> Answer:
    if sys.platform == "darwin":
        return _macos(_MacAudio(), host, os.getpid())
    if sys.platform == "win32":
        return _windows(_WinRegistry(), host)
    if sys.platform.startswith("linux"):
        return _linux(run, host, os.getpid())
    return None


def mic_in_use(host: str) -> Answer:
    """True while an app other than this host (and this server) records from a mic."""
    key = host.lower() if host.lower() in HOSTS else "other"
    return CACHE.get(("mic", key), lambda: guarded("mic", lambda: _system(key)))
