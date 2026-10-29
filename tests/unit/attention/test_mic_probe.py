"""The mic probe: every system's logic on recorded outputs, on every runner."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from trillion.attention.probes import mic

FIXTURES = Path(__file__).parent / "fixtures"
PACTL = (FIXTURES / "pactl_source_outputs.json").read_text(encoding="utf-8")
PW_DUMP = (FIXTURES / "pw_dump.json").read_text(encoding="utf-8")
OWN_PID = 4242


class FakeRun:
    """Answers by command name; a missing name is a missing command."""

    def __init__(self, **outputs: str | None) -> None:
        self.outputs = outputs
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str | None:
        self.calls.append(list(args))
        return self.outputs.get(args[0].replace("-", "_"))


# --- Linux ------------------------------------------------------------------


def test_pactl_counts_zoom_when_trillion_is_in_chrome() -> None:
    assert mic._linux(FakeRun(pactl=PACTL), "chrome", OWN_PID) is True


def test_pactl_ignores_chrome_alone_when_trillion_is_in_chrome() -> None:
    only_chrome = json.dumps(json.loads(PACTL)[:1])
    assert mic._linux(FakeRun(pactl=only_chrome), "chrome", OWN_PID) is False


def test_pactl_counts_chrome_when_trillion_is_elsewhere() -> None:
    only_chrome = json.dumps(json.loads(PACTL)[:1])
    assert mic._linux(FakeRun(pactl=only_chrome), "firefox", OWN_PID) is True
    assert mic._linux(FakeRun(pactl=only_chrome), "other", OWN_PID) is True


def test_pactl_ignores_this_server() -> None:
    zoom = json.loads(PACTL)[1]
    zoom["properties"]["application.process.id"] = str(OWN_PID)
    assert mic._linux(FakeRun(pactl=json.dumps([zoom])), "other", OWN_PID) is False


def test_pactl_skips_a_paused_stream() -> None:
    zoom = json.loads(PACTL)[1]
    zoom["corked"] = True
    assert mic._linux(FakeRun(pactl=json.dumps([zoom])), "other", OWN_PID) is False


def test_pactl_with_nothing_recording() -> None:
    run = FakeRun(pactl="[]", pw_dump=PW_DUMP)
    assert mic._linux(run, "chrome", OWN_PID) is False
    assert [c[0] for c in run.calls] == ["pactl"]


def test_pw_dump_when_pactl_is_missing() -> None:
    # OBS is running; Firefox's stream is idle.
    run = FakeRun(pw_dump=PW_DUMP)
    assert mic._linux(run, "chrome", OWN_PID) is True
    assert [c[0] for c in run.calls] == ["pactl", "pw-dump"]


def test_pw_dump_skips_idle_and_own_streams() -> None:
    data = json.loads(PW_DUMP)
    data[3]["info"]["props"]["application.process.id"] = OWN_PID
    assert mic._linux(FakeRun(pw_dump=json.dumps(data)), "firefox", OWN_PID) is False


def test_pw_dump_ignores_the_host_browser() -> None:
    data = json.loads(PW_DUMP)
    data[2]["info"]["state"] = "running"
    del data[3]
    text = json.dumps(data)
    assert mic._linux(FakeRun(pw_dump=text), "firefox", OWN_PID) is False
    assert mic._linux(FakeRun(pw_dump=text), "chrome", OWN_PID) is True


def test_desktop_ignores_webkit_web_process() -> None:
    zoom = json.loads(PACTL)[1]
    zoom["properties"]["application.process.binary"] = "WebKitWebProcess"
    assert mic._linux(FakeRun(pactl=json.dumps([zoom])), "desktop", OWN_PID) is False


def test_garbage_pactl_falls_back_to_pw_dump() -> None:
    assert mic._linux(FakeRun(pactl="Invalid option -f", pw_dump=PW_DUMP), "x", 1) is True


@pytest.mark.parametrize("garbage", ["", "not json", "{}", '{"a": 1}', "null"])
def test_garbage_from_both_cannot_tell(garbage: str) -> None:
    assert mic._linux(FakeRun(pactl=garbage, pw_dump=garbage), "other", OWN_PID) is None


def test_neither_tool_cannot_tell() -> None:
    assert mic._linux(FakeRun(), "other", OWN_PID) is None


# --- Windows ----------------------------------------------------------------

LIVE = {"LastUsedTimeStart": 133_745_000_000_000_000, "LastUsedTimeStop": 0}
STOPPED = {
    "LastUsedTimeStart": 133_745_000_000_000_000,
    "LastUsedTimeStop": 133_745_000_100_000_000,
}
NEVER = {"Value": 0}


class FakeRegistry:
    """A registry tree: path → (values, subkeys)."""

    def __init__(self, tree: dict[str, dict[str, int]]) -> None:
        self.tree = tree

    def subkeys(self, path: str) -> list[str]:
        if path not in self.tree:
            raise FileNotFoundError(path)
        prefix = path + "\\"
        return sorted({p[len(prefix) :].split("\\")[0] for p in self.tree if p.startswith(prefix)})

    def value(self, path: str, name: str) -> int | None:
        return self.tree.get(path, {}).get(name)


def registry(**apps: dict[str, int]) -> FakeRegistry:
    base = mic.MIC_KEY
    tree: dict[str, dict[str, int]] = {base: NEVER, base + "\\NonPackaged": {}}
    for name, values in apps.items():
        tree[base + "\\" + name.replace("__", "\\")] = values
    return FakeRegistry(tree)


ZOOM = r"NonPackaged__C:#Users#sam#AppData#Roaming#Zoom#bin#Zoom.exe"
CHROME = r"NonPackaged__C:#Program Files#Google#Chrome#Application#chrome.exe"
WEBVIEW = (
    r"NonPackaged__C:#Program Files (x86)#Microsoft#EdgeWebView#Application#msedgewebview2.exe"
)
CAMERA = "Microsoft.WindowsCamera_8wekyb3d8bbwe"


def test_windows_stopped_apps_do_not_count() -> None:
    reg = registry(**{ZOOM: STOPPED, CAMERA: STOPPED, CHROME: STOPPED})
    assert mic._windows(reg, "chrome") is False


def test_windows_live_nonpackaged_app_counts() -> None:
    reg = registry(**{ZOOM: LIVE, CAMERA: STOPPED})
    assert mic._windows(reg, "chrome") is True


def test_windows_live_packaged_app_counts() -> None:
    reg = registry(**{ZOOM: STOPPED, CAMERA: LIVE})
    assert mic._windows(reg, "desktop") is True


def test_windows_host_browser_is_ignored() -> None:
    reg = registry(**{CHROME: LIVE, ZOOM: STOPPED})
    assert mic._windows(reg, "chrome") is False
    assert mic._windows(reg, "edge") is True


def test_windows_desktop_ignores_its_webview() -> None:
    reg = registry(**{WEBVIEW: LIVE})
    assert mic._windows(reg, "desktop") is False
    assert mic._windows(reg, "other") is True


def test_windows_never_used_is_not_live() -> None:
    assert mic._windows(registry(**{CAMERA: NEVER}), "other") is False


def test_windows_missing_key_raises_for_the_guard() -> None:
    with pytest.raises(OSError):
        mic._windows(FakeRegistry({}), "other")


# --- macOS ------------------------------------------------------------------


class FakeAudio:
    """CoreAudio's process objects: obj → (pid, bundle id, recording)."""

    def __init__(self, procs: dict[int, tuple[int | None, str | None, bool | None]] | None) -> None:
        self.procs = procs

    def processes(self) -> list[int] | None:
        return None if self.procs is None else list(self.procs)

    def pid(self, obj: int) -> int | None:
        assert self.procs is not None
        return self.procs[obj][0]

    def bundle_id(self, obj: int) -> str | None:
        assert self.procs is not None
        return self.procs[obj][1]

    def recording(self, obj: int) -> bool | None:
        assert self.procs is not None
        return self.procs[obj][2]


QUIET_MAC: dict[int, tuple[int | None, str | None, bool | None]] = {
    120: (561, "com.apple.audiomxd", False),
    143: (45120, "com.apple.WebKit.GPU", False),
    165: (33705, "com.google.Chrome", False),
    172: (59866, "ai.hellotrillion.desktop", False),
    173: (OWN_PID, "org.python.python", False),
}


def with_recording(
    *objs: int, extra: dict[int, tuple[int | None, str | None, bool | None]] | None = None
) -> FakeAudio:
    procs = {**QUIET_MAC, **(extra or {})}
    for obj in objs:
        pid, bundle, _ = procs[obj]
        procs[obj] = (pid, bundle, True)
    return FakeAudio(procs)


def test_macos_nothing_recording() -> None:
    assert mic._macos(FakeAudio(QUIET_MAC), "desktop", OWN_PID) is False


def test_macos_desktop_ignores_itself_webkit_and_the_server() -> None:
    assert mic._macos(with_recording(143, 172, 173), "desktop", OWN_PID) is False


def test_macos_desktop_counts_chrome() -> None:
    assert mic._macos(with_recording(165), "desktop", OWN_PID) is True


def test_macos_chrome_host_ignores_chrome_helpers_but_not_zoom() -> None:
    extra = {
        166: (33842, "com.google.Chrome.helper", False),
        180: (2001, "us.zoom.xos", False),
    }
    assert mic._macos(with_recording(165, 166, extra=extra), "chrome", OWN_PID) is False
    assert mic._macos(with_recording(165, 180, extra=extra), "chrome", OWN_PID) is True


def test_macos_safari_host_ignores_webkit() -> None:
    assert mic._macos(with_recording(143), "safari", OWN_PID) is False
    assert mic._macos(with_recording(143), "chrome", OWN_PID) is True


def test_macos_no_bundle_id_still_counts() -> None:
    extra: dict[int, tuple[int | None, str | None, bool | None]] = {190: (777, None, True)}
    assert mic._macos(with_recording(extra=extra), "desktop", OWN_PID) is True


def test_macos_unreadable_recording_flag_is_skipped() -> None:
    extra: dict[int, tuple[int | None, str | None, bool | None]] = {190: (777, "x.y", None)}
    assert mic._macos(with_recording(extra=extra), "other", OWN_PID) is False


def test_macos_older_than_14_2_cannot_tell() -> None:
    assert mic._macos(FakeAudio(None), "desktop", OWN_PID) is None


# --- hosts and names ---------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "name"),
    [
        (r"C:#Program Files#Google#Chrome#Application#chrome.exe", "chrome"),
        (r"C:\Program Files\Mozilla Firefox\FIREFOX.EXE", "firefox"),
        ("/usr/lib/chromium/chromium", "chromium"),
        ("zoom", "zoom"),
    ],
)
def test_exe_name(path: str, name: str) -> None:
    assert mic.exe_name(path) == name


def test_bundle_prefix_needs_a_dot_boundary() -> None:
    chrome = mic.own("chrome")
    assert chrome.owns_bundle("com.google.Chrome.helper.renderer")
    assert not chrome.owns_bundle("com.google.Chromecast")


def test_unknown_host_ignores_nothing() -> None:
    assert mic.own("netscape") == mic.HOSTS["other"]
    assert mic.own("Chrome") == mic.HOSTS["chrome"]


# --- After the review -------------------------------------------------------


@pytest.mark.parametrize(
    "marks",
    [
        {"stream.capture.sink": "true"},
        {"stream.monitor": "true"},
        {"stream.monitor": True},
        {"media.name": "Peak detect"},
    ],
)
def test_pactl_skips_monitor_and_peak_meter_streams(marks: dict[str, object]) -> None:
    # A volume meter or a recorder of what is playing is not someone on the mic.
    zoom = json.loads(PACTL)[1]
    zoom["properties"].update(marks)
    assert mic._linux(FakeRun(pactl=json.dumps([zoom])), "other", OWN_PID) is False


def test_pw_dump_skips_monitor_streams() -> None:
    data = json.loads(PW_DUMP)
    for obj in data:
        props = (obj.get("info") or {}).get("props")
        if isinstance(props, dict):
            props["stream.monitor"] = "true"
    assert mic._linux(FakeRun(pw_dump=json.dumps(data)), "other", OWN_PID) is False


class _FakeCA:
    """CoreAudio's two calls: the size asked for first is larger than what the second
    fills (a process went away in between)."""

    def AudioObjectGetPropertyDataSize(self, obj, address, q, qd, size):  # type: ignore[no-untyped-def]
        size._obj.value = 16
        return 0

    def AudioObjectGetPropertyData(self, obj, address, q, qd, size, out):  # type: ignore[no-untyped-def]
        items = out._obj
        items[0], items[1] = 7, 9
        size._obj.value = 8
        return 0


def test_macos_process_list_is_cut_to_what_the_second_call_filled() -> None:
    audio = object.__new__(mic._MacAudio)
    audio._ca = _FakeCA()  # type: ignore[attr-defined]
    assert audio.processes() == [7, 9]


def test_macos_audio_is_opened_once_and_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[object] = []

    class Counting:
        def __init__(self) -> None:
            made.append(self)

    monkeypatch.setattr(mic, "_MacAudio", Counting)
    monkeypatch.setattr(mic, "_MAC", None)
    first, second = mic._mac_audio(), mic._mac_audio()
    assert first is second and len(made) == 1
