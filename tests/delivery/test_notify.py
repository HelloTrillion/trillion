"""System notifications: an argument list per platform, never a shell, never a raise."""

from __future__ import annotations

import base64
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trillion.delivery import notify as n

MARKER = "zq-notice-4417"


class Recorder:
    def __init__(self, result: Any = None, raises: BaseException | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self._result = result if result is not None else subprocess.CompletedProcess([], 0)
        self._raises = raises

    def __call__(self, args: list[str], **kw: Any) -> Any:
        self.calls.append((args, kw))
        if self._raises is not None:
            raise self._raises
        return self._result


def which(name: str) -> str | None:
    return f"/bin/{name}"


def test_macos_passes_the_words_as_arguments_to_the_script() -> None:
    run = Recorder()
    title = 'Say "hi" \\ end'
    assert n.notify(title, "Body", platform="darwin", run=run, which=which, env={})
    ((args, kw),) = run.calls
    assert args[0] == "/bin/osascript" and args[-2:] == [title, "Body"]
    assert all(title not in a for a in args[:-2])  # never inside the script itself
    assert "shell" not in kw and kw["timeout"] == n.TIMEOUT_S and "creationflags" not in kw


def test_linux_uses_notify_send_when_there_is_one() -> None:
    run = Recorder()
    assert n.notify("Title", "-r Body", platform="linux", run=run, which=which, env={})
    ((args, _),) = run.calls
    assert args == ["/bin/notify-send", "--app-name=Trillion", "--", "Title", "-r Body"]
    assert not n.notify("Title", "Body", platform="linux", run=run, which=lambda _: None)
    assert len(run.calls) == 1


def test_windows_flashes_no_console_and_keeps_the_words_out_of_the_script() -> None:
    run = Recorder()
    assert n.notify(f"T {MARKER}", "B '$(x)'", platform="win32", run=run, which=which, env={})
    ((args, kw),) = run.calls
    assert args[0] == "/bin/powershell.exe" and "-EncodedCommand" in args
    script = base64.b64decode(args[-1]).decode("utf-16-le")
    assert MARKER not in script and "$(x)" not in script
    assert kw["creationflags"] == n.CREATE_NO_WINDOW
    assert kw["env"][n.TITLE_ENV] == f"T {MARKER}" and kw["env"][n.BODY_ENV] == "B '$(x)'"


def test_text_is_one_line_and_cut_to_the_limit() -> None:
    run = Recorder()
    n.notify("a\nb", "x" * 500, platform="linux", run=run, which=which, env={})
    ((args, _),) = run.calls
    assert args[-2] == "a b" and len(args[-1]) == n.LIMIT and args[-1].endswith("…")


@pytest.mark.parametrize(
    "run",
    [
        Recorder(raises=subprocess.TimeoutExpired("x", n.TIMEOUT_S)),
        Recorder(raises=FileNotFoundError(MARKER)),
        Recorder(result=subprocess.CompletedProcess([], 1, stderr=MARKER.encode())),
    ],
)
def test_a_failure_returns_false_and_logs_only_its_kind(
    run: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert not n.notify(MARKER, MARKER, platform="linux", run=run, which=which, env={})
    assert MARKER not in caplog.text


def test_macos_words_come_after_a_double_dash_so_they_are_never_options() -> None:
    run = Recorder()
    assert n.notify("-e x", "-e y", platform="darwin", run=run, which=which, env={})
    ((args, _),) = run.calls
    assert args[-3:] == ["--", "-e x", "-e y"]


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("text", ["a\x00b", "a\ud800b", "a\x07b\x1b[0m"])
def test_odd_characters_never_raise_and_are_cleaned(platform: str, text: str) -> None:
    run = Recorder()
    assert n.notify(text, text, platform=platform, run=run, which=which, env={})
    ((args, kw),) = run.calls
    sent = [*args, *kw.get("env", {}).values()]
    assert all("\x00" not in s and "\x07" not in s and "\x1b" not in s for s in sent)
    for s in sent:
        s.encode("utf-8")


def test_a_value_error_from_the_run_returns_false() -> None:
    run = Recorder(raises=ValueError("embedded null byte"))
    assert not n.notify("T", "B", platform="linux", run=run, which=which, env={})


def test_linux_body_markup_is_escaped() -> None:
    run = Recorder()
    n.notify("T <b>", "a & <b>x</b>", platform="linux", run=run, which=which, env={})
    ((args, _),) = run.calls
    assert args[-1] == "a &amp; &lt;b&gt;x&lt;/b&gt;" and args[-2] == "T <b>"


def test_windows_prefers_the_system_powershell(tmp_path: object) -> None:
    run = Recorder()
    root = Path(str(tmp_path))
    exe = root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    n.notify("T", "B", platform="win32", run=run, which=which, env={"SystemRoot": str(root)})
    ((args, _),) = run.calls
    assert args[0] == str(exe)
