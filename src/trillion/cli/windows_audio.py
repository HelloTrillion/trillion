"""The microphones Windows knows, for `trillion doctor` (ONB-5, SUR-3c).

Two questions, both answered without opening a microphone or playing a sound:

- `inputs()`: the active recording endpoints and which is the default, from the
  Core Audio device enumerator (the MMDevice API WASAPI is built on), through
  `ctypes` and COM. Only names are read, from each endpoint's property store.
- `access()`: whether Windows' microphone privacy switches let a desktop app, such
  as the browser, use one at all.

Both say None when they can't tell. Nothing is installed for this.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import Any, Literal

_TIMEOUT_S = 3.0

CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
)

Access = Literal["on", "off", "desktop_off"]


@dataclass(frozen=True)
class Inputs:
    names: tuple[str, ...]
    # The default recording device for most apps (the console role), if one is set.
    default: str | None


def inputs() -> Inputs | None:
    """The active microphones, or None when Windows did not say in time."""
    if sys.platform != "win32":
        return None
    found: list[Inputs | None] = [None]

    def ask() -> None:
        try:
            found[0] = _enumerate()
        except Exception:  # a doctor line, never a crash: COM, ctypes or the driver
            found[0] = None

    # A thread of its own, so COM is set up and torn down here and nowhere else.
    worker = threading.Thread(target=ask, name="doctor-audio", daemon=True)
    worker.start()
    worker.join(_TIMEOUT_S)
    return None if worker.is_alive() else found[0]


def access(read: Any = None) -> Access | None:
    """Settings → Privacy & security → Microphone, as far as this user's registry says.

    `read(path)` returns a key's "Value" ("Allow" or "Deny") or None; tests pass one."""
    if read is None:
        if sys.platform != "win32":
            return None
        read = _consent
    everything, desktop = read(CONSENT_KEY), read(CONSENT_KEY + r"\NonPackaged")
    if everything == "Deny":
        return "off"
    if desktop == "Deny":
        return "desktop_off"
    if everything is None and desktop is None:
        return None
    return "on"


def _consent(path: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "Value")
    except OSError:
        return None
    return value if isinstance(value, str) else None


# ── Core Audio through COM ────────────────────────────────────────────────

_CLSID_MMDEVICE_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_IMMDEVICE_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
# PKEY_Device_FriendlyName: "Microphone (USB Audio Device)".
_PKEY_FRIENDLY_NAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
_CLSCTX_ALL = 0x17
_COINIT_MULTITHREADED = 0x0
_E_CAPTURE = 1
_E_CONSOLE = 0
_DEVICE_STATE_ACTIVE = 0x1
_STGM_READ = 0x0
_VT_LPWSTR = 31

# Vtable slots, after IUnknown's QueryInterface (0), AddRef (1) and Release (2).
_RELEASE = 2
_ENUM_AUDIO_ENDPOINTS = 3  # IMMDeviceEnumerator
_GET_DEFAULT_AUDIO_ENDPOINT = 4  # IMMDeviceEnumerator
_COLLECTION_GET_COUNT = 3  # IMMDeviceCollection
_COLLECTION_ITEM = 4  # IMMDeviceCollection
_DEVICE_OPEN_PROPERTY_STORE = 4  # IMMDevice
_DEVICE_GET_ID = 5  # IMMDevice
_STORE_GET_VALUE = 5  # IPropertyStore


def _enumerate() -> Inputs | None:
    if sys.platform != "win32":
        return None
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        # vt, three reserved words, then a union two pointers wide.
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("reserved1", wintypes.WORD),
            ("reserved2", wintypes.WORD),
            ("reserved3", wintypes.WORD),
            ("data", ctypes.c_void_p),
            ("data2", ctypes.c_void_p),
        ]

    def guid(text: str) -> GUID:
        return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

    ole32 = ctypes.OleDLL("ole32")
    ole32.CoTaskMemFree.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoUninitialize.restype = None

    def call(obj: ctypes.c_void_p, slot: int, argtypes: list[Any], *args: Any) -> Any:
        """Call a COM method by its vtable slot. A failed HRESULT raises OSError."""
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[slot])
        return method(obj, *args)

    def release(obj: ctypes.c_void_p) -> None:
        if obj.value:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[_RELEASE])(obj)

    def device_id(device: ctypes.c_void_p) -> str:
        text = ctypes.c_void_p()
        call(device, _DEVICE_GET_ID, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(text))
        try:
            return ctypes.wstring_at(text.value) if text.value else ""
        finally:
            ole32.CoTaskMemFree(text)

    def friendly_name(device: ctypes.c_void_p) -> str:
        store = ctypes.c_void_p()
        call(
            device,
            _DEVICE_OPEN_PROPERTY_STORE,
            [wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)],
            _STGM_READ,
            ctypes.byref(store),
        )
        try:
            key = PROPERTYKEY(guid(_PKEY_FRIENDLY_NAME[0]), _PKEY_FRIENDLY_NAME[1])
            value = PROPVARIANT()
            call(
                store,
                _STORE_GET_VALUE,
                [ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)],
                ctypes.byref(key),
                ctypes.byref(value),
            )
            try:
                if value.vt == _VT_LPWSTR and value.data:
                    return ctypes.wstring_at(value.data)
                return ""
            finally:
                ole32.PropVariantClear(ctypes.byref(value))
        finally:
            release(store)

    ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    try:
        enumerator = ctypes.c_void_p()
        clsid, iid = guid(_CLSID_MMDEVICE_ENUMERATOR), guid(_IID_IMMDEVICE_ENUMERATOR)
        ole32.CoCreateInstance(
            ctypes.byref(clsid), None, _CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(enumerator)
        )
        try:
            collection = ctypes.c_void_p()
            call(
                enumerator,
                _ENUM_AUDIO_ENDPOINTS,
                [ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)],
                _E_CAPTURE,
                _DEVICE_STATE_ACTIVE,
                ctypes.byref(collection),
            )
            by_id: dict[str, str] = {}
            try:
                count = ctypes.c_uint()
                call(
                    collection,
                    _COLLECTION_GET_COUNT,
                    [ctypes.POINTER(ctypes.c_uint)],
                    ctypes.byref(count),
                )
                for i in range(count.value):
                    device = ctypes.c_void_p()
                    call(
                        collection,
                        _COLLECTION_ITEM,
                        [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)],
                        i,
                        ctypes.byref(device),
                    )
                    try:
                        by_id[device_id(device)] = friendly_name(device) or "a microphone"
                    finally:
                        release(device)
            finally:
                release(collection)
            default: str | None = None
            chosen = ctypes.c_void_p()
            try:
                call(
                    enumerator,
                    _GET_DEFAULT_AUDIO_ENDPOINT,
                    [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                    _E_CAPTURE,
                    _E_CONSOLE,
                    ctypes.byref(chosen),
                )
            except OSError:
                pass  # E_NOTFOUND: no default, because no microphone
            else:
                try:
                    default = by_id.get(device_id(chosen))
                finally:
                    release(chosen)
            return Inputs(names=tuple(by_id.values()), default=default)
        finally:
            release(enumerator)
    finally:
        ole32.CoUninitialize()
