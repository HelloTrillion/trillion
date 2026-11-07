"""`trillion update`: channels, the check, the upgrade (ONB-8). No network: GitHub is faked."""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from trillion.cli import main as cli
from trillion.cli import update as updates

SRC = Path(__file__).resolve().parents[2] / "src" / "trillion"
PAGE = "https://github.com/HelloTrillion/trillion/releases/tag/"
WHEEL = "https://github.com/HelloTrillion/trillion/releases/download/dev/"


def release(tag: str, *, draft: bool = False, prerelease: bool = False) -> dict[str, Any]:
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "html_url": PAGE + tag}


RELEASES = [
    release("dev", prerelease=True),
    release("v2026.12.0-beta.1", prerelease=True),
    release("v2026.11.1"),
    release("v2026.11.0"),
    release("v2026.13.0", draft=True),  # a draft never counts
    release("v0.0.2", prerelease=True),  # 0.x: marked pre-release, but a plain version
    release("not-a-version"),
]
DEV = {
    "tag_name": "dev",
    "html_url": PAGE + "dev",
    "assets": [
        {"name": "notes.txt", "browser_download_url": WHEEL + "notes.txt"},
        {
            "name": "trillion-2026.11.2.dev57-py3-none-any.whl",
            "browser_download_url": WHEEL + "trillion-2026.11.2.dev57-py3-none-any.whl",
        },
    ],
}


def github(
    releases: list[dict[str, Any]] | None = None,
    dev: dict[str, Any] | None = DEV,
    *,
    status: int = 200,
    asked: list[str] | None = None,
) -> httpx.Client:
    def answer(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(str(request.url))
        assert request.url.host == "api.github.com"
        if status != 200:
            return httpx.Response(status)
        if request.url.path.endswith("/releases/tags/dev"):
            return httpx.Response(200, json=dev) if dev else httpx.Response(404)
        return httpx.Response(200, json=RELEASES if releases is None else releases)

    return httpx.Client(transport=httpx.MockTransport(answer))


def offline() -> httpx.Client:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("the network is off", request=request)

    return httpx.Client(transport=httpx.MockTransport(refuse))


@pytest.fixture(autouse=True)
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(updates, "__version__", "2026.11.0")
    return tmp_path / "home"


class Uv:
    """Stands in for uv: records what it was asked to run."""

    def __init__(self, code: int = 0, listing: str = "trillion v2026.11.0\n- trillion\n") -> None:
        self.code = code
        self.text = listing
        self.ran: list[list[str]] = []

    def listing(self, uv: str) -> str:
        return self.text

    def run(self, cmd: Sequence[str]) -> int:
        self.ran.append(list(cmd))
        return self.code


def update(uv: Uv, http: httpx.Client, **kw: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    kw.setdefault("check_only", False)
    kw.setdefault("channel", None)
    code = updates.run(
        http=http, uv="/bin/uv", listing=uv.listing, runner=uv.run, out=out, err=err, **kw
    )
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """As if installed with uv, not run from this checkout."""
    monkeypatch.setattr(updates.paths, "source_checkout", lambda: False)


# The channel


def test_the_channel_is_stable_until_one_is_chosen(home: Path) -> None:
    assert updates.remembered() == "stable"
    updates.remember("beta")
    assert (home / "channel").read_text() == "beta\n"
    assert updates.remembered() == "beta"


@pytest.mark.parametrize("text", ["nightly\n", "", "BETA\n"])
def test_a_channel_file_is_read_kindly(home: Path, text: str) -> None:
    home.mkdir(parents=True)
    (home / "channel").write_text(text)
    assert updates.remembered() == ("beta" if text == "BETA\n" else "stable")


def test_the_installers_and_the_app_agree_on_the_file() -> None:
    root = SRC.parents[1]
    sh = (root / "install.sh").read_text()
    ps1 = (root / "install.ps1").read_text()
    assert "${TRILLION_HOME:-$HOME/.trillion}/channel" in sh
    assert "'.trillion'" in ps1 and "'channel'" in ps1


# Which release is newest


def test_stable_is_the_newest_tag_without_a_suffix() -> None:
    found = updates.newest("stable", github())
    assert found == updates.Release("2026.11.1", PAGE + "v2026.11.1")


def test_beta_includes_stable_and_beta_tags() -> None:
    assert updates.newest("beta", github()) == updates.Release(
        "2026.12.0b1", PAGE + "v2026.12.0-beta.1"
    )
    # A stable release newer than every beta is what beta gets.
    later = [release("v2026.12.0-beta.1"), release("v2026.12.0")]
    assert updates.newest("beta", github(later)).version == "2026.12.0"  # type: ignore[union-attr]


def test_the_old_releases_count_as_stable() -> None:
    found = updates.newest("stable", github([release("v0.0.2", prerelease=True)]))
    assert found is not None and found.version == "0.0.2"


def test_dev_is_the_wheel_on_the_dev_release() -> None:
    found = updates.newest("dev", github())
    assert found is not None
    assert found.version == "2026.11.2.dev57"
    assert found.wheel == WHEEL + "trillion-2026.11.2.dev57-py3-none-any.whl"


def test_a_channel_with_nothing_yet() -> None:
    assert updates.newest("stable", github([])) is None
    assert updates.newest("dev", github(dev=None)) is None


def test_versions_sort_as_releases_do() -> None:
    order = ["0.0.2", "2026.11.0b1", "2026.11.0", "2026.11.1.dev3", "2026.11.1", "2026.12.0b1"]
    keys = [updates.key(v) for v in order]
    assert keys == sorted(keys)  # type: ignore[type-var]
    assert updates.key("v2026.11.0-beta.1") == updates.key("2026.11.0b1")
    assert updates.key("latest") is None


# --check


def test_check_says_a_newer_version_is_out_and_changes_nothing(home: Path) -> None:
    uv = Uv()
    code, out, _ = update(uv, github(), check_only=True)
    assert code == 0
    assert "trillion 2026.11.0, on the stable channel." in out
    assert "Newer on stable: 2026.11.1" in out
    assert "trillion update" in out
    assert uv.ran == []
    assert not (home / "channel").exists()


def test_check_says_when_it_is_the_newest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.11.1")
    _, out, _ = update(Uv(), github(), check_only=True)
    assert "That is the newest on stable." in out


def test_check_on_another_channel_does_not_switch_to_it(home: Path) -> None:
    asked: list[str] = []
    _, out, _ = update(Uv(), github(asked=asked), check_only=True, channel="dev")
    assert "Newer on dev: 2026.11.2.dev57" in out
    assert asked == ["https://api.github.com/repos/HelloTrillion/trillion/releases/tags/dev"]
    assert updates.remembered() == "stable"


@pytest.mark.parametrize(
    ("client", "said"),
    [(offline, "couldn't reach GitHub"), (lambda: github(status=403), "limiting")],
)
def test_check_fails_plainly_without_github(client: Any, said: str) -> None:
    code, out, err = update(Uv(), client(), check_only=True)
    assert code == 1 and out == ""
    assert said in err and "Nothing was changed" in err


# trillion update


def test_update_runs_the_installers_uv_command_and_keeps_extras(installed: None) -> None:
    uv = Uv(listing="trillion v2026.11.0 [extras: whisper]\n- trillion\n")
    code, out, _ = update(uv, github())
    assert code == 0
    assert uv.ran == [
        ["/bin/uv", "tool", "install", "--upgrade", "--python", "3.13", "trillion[whisper]"]
    ]
    assert "Updating trillion 2026.11.0 to 2026.11.1 (stable)" in out


def test_update_does_nothing_when_it_is_the_newest(
    installed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.11.1")
    uv = Uv()
    code, out, _ = update(uv, github())
    assert code == 0 and uv.ran == []
    assert "newest" in out


def test_beta_asks_uv_for_pre_releases_of_trillion_only(installed: None, home: Path) -> None:
    uv = Uv()
    code, _, _ = update(uv, github(), channel="beta")
    assert code == 0
    assert uv.ran[0][-1] == "trillion>=0.0.0b0"
    assert "--prerelease" not in uv.ran[0]  # that would let every dependency be one too
    assert updates.remembered() == "beta"  # an update with --channel switches to it


def test_dev_installs_the_dev_wheel(installed: None) -> None:
    updates.remember("dev")
    uv = Uv(listing="trillion v2026.11.0 [extras: whisper]\n")
    code, _, _ = update(uv, github())
    assert code == 0
    assert uv.ran == [
        [
            "/bin/uv",
            "tool",
            "install",
            "--reinstall",
            "--python",
            "3.13",
            "trillion[whisper] @ " + WHEEL + "trillion-2026.11.2.dev57-py3-none-any.whl",
        ]
    ]


def test_dev_with_no_build_yet_installs_nothing(installed: None) -> None:
    uv = Uv()
    code, _, _ = update(uv, github(dev=None), channel="dev")
    assert code == 0 and uv.ran == []
    assert updates.remembered() == "stable"


def test_switching_back_to_stable_reinstalls_even_from_a_newer_beta(
    installed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    updates.remember("beta")
    monkeypatch.setattr(updates, "__version__", "2026.12.0b1")
    uv = Uv()
    code, _, _ = update(uv, github(), channel="stable")
    assert code == 0
    assert uv.ran[0][3:] == ["--reinstall", "--python", "3.13", "trillion"]
    assert updates.remembered() == "stable"


def test_a_failed_uv_keeps_the_old_channel(installed: None) -> None:
    uv = Uv(code=2)
    code, _, err = update(uv, github(), channel="beta")
    assert code == 1
    assert "couldn't install it" in err
    assert updates.remembered() == "stable"


def test_without_github_stable_still_asks_uv(installed: None) -> None:
    uv = Uv()
    code, _, err = update(uv, offline())
    assert code == 0 and len(uv.ran) == 1
    assert "Asking uv anyway" in err


def test_without_github_dev_changes_nothing(installed: None) -> None:
    uv = Uv()
    code, _, _ = update(uv, offline(), channel="dev")
    assert code == 1 and uv.ran == []


def test_a_source_checkout_is_left_to_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates.paths, "source_checkout", lambda: True)
    uv = Uv()
    code, _, err = update(uv, github())
    assert code == 1 and uv.ran == []
    assert "git pull" in err


# The command line, and ONB-8


def test_the_command_line_runs_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(updates, "client", lambda: github())
    assert cli.main(["update", "--check", "--channel", "beta"]) == 0
    assert "Newer on beta: 2026.12.0b1" in capsys.readouterr().out


def test_nothing_else_asks_about_updates() -> None:
    """ONB-8: the check runs only when typed. Only the `update` command reaches it,
    and nothing else in the core names GitHub's API."""
    importers = []
    naming_api = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(SRC).as_posix()
        if rel != "cli/update.py" and "api.github.com" in text:
            naming_api.append(rel)
        if "cli import update" in text or "cli.update" in text:
            importers.append(rel)
    assert importers == ["cli/main.py"]
    assert naming_api == []
    main_text = (SRC / "cli" / "main.py").read_text(encoding="utf-8")
    # Imported inside the `update` branch only, never at the top of main.py.
    assert "\n        from trillion.cli import update as updates\n" in main_text
