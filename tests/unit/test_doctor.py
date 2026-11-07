"""`trillion doctor` and `trillion setup`: what they check, and that doctor only looks."""

from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from trillion import paths
from trillion.brain import config
from trillion.brain.types import ModelChoice, ProviderError
from trillion.cli import doctor
from trillion.cli.doctor import Check, Options
from trillion.cli.main import main
from trillion.persona import setup
from trillion.secrets.store import MemoryStore
from trillion.voice.providers.deepgram import Deepgram
from trillion.voice.types import Choice, VoiceError

KEY = "sk-" + "test-" * 6 + "9f3e"  # built from pieces: never a real credential


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    for name in (config.OPENAI_BASE_URL, config.MODEL, config.ANTHROPIC_KEY, config.OPENAI_KEY):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "home"


@pytest.fixture(autouse=True)
def keychain(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    """A working keychain, whatever the machine running the tests has, and never its own."""
    import keyring

    store = MemoryStore()
    monkeypatch.setattr("trillion.secrets.store.KeychainStore", lambda: store)
    backend = type("Backend", (), {"name": "macOS Keyring"})()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    return store


@pytest.fixture(autouse=True)
def sound(monkeypatch: pytest.MonkeyPatch) -> None:
    """A running sound server with a microphone, whatever the machine running the tests has."""
    fake = {"info": "", "list": "2\talsa_input.usb\tx\n", "status": "", "inspect": ""}
    monkeypatch.setattr(doctor, "_answers", lambda *command: fake.get(command[1]))
    which = doctor.shutil.which
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda name, *a, **k: f"/usr/bin/{name}" if name == "pactl" else which(name, *a, **k),
    )


@pytest.fixture(autouse=True)
def built_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A built app page, whether or not this checkout has run the web build (CI has not)."""
    page = tmp_path / "web"
    page.mkdir()
    (page / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr("trillion.api.app.web_dir", lambda: page)


@pytest.fixture
def offers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None, str | None]]:
    asked: list[tuple[str, str | None, str | None]] = []

    async def fake(kind: str, base_url: str | None, api_key: str | None) -> list[ModelChoice]:
        asked.append((kind, base_url, api_key))
        if api_key == "wrong":
            raise ProviderError("unauthorized", f"echo {api_key}")
        if base_url and "down" in base_url:
            raise ProviderError("unreachable")
        return [ModelChoice(id="model-a", label="Model A", max_output=8000)]

    monkeypatch.setattr(config, "list_models", fake)
    return asked


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def examine(store: MemoryStore | None = None, **options: Any) -> dict[str, Check]:
    found = asyncio.run(
        doctor.examine(
            Options(port=free_port(), **options),
            secrets=store or MemoryStore(),
            env={},
            page=Path("/built"),
        )
    )
    return {c.name: c for c in found}


def prepared(home: Path) -> None:
    assert main(["setup", "--no-run", "--offline", "--port", str(free_port())]) == 0


# ── A machine with nothing on it ─────────────────────────────────────────


def test_a_fresh_machine_is_healthy_and_nothing_is_created(home: Path, offers: list) -> None:
    checks = examine()
    assert doctor.healthy(list(checks.values()))
    assert {c.status for c in checks.values()} <= {"ok", "info"}
    assert checks["Model"].detail.startswith("none chosen yet")
    assert not home.exists()  # it looked, and made nothing
    assert offers == []  # and called nobody


def test_the_report_says_what_to_do_about_each_problem() -> None:
    text = doctor.render(
        [
            Check(name="Port", status="warn", detail="7777 is in use", fix="Use another."),
            Check(name="Model", status="fail", detail="refused", fix="Enter the key again."),
            Check(name="Voice", status="ok", detail="fine"),
        ]
    )
    assert "warn  Port   7777 is in use\n             -> Use another.\n" in text
    assert "fail  Model  refused\n             -> Enter the key again.\n" in text
    assert text.endswith("1 thing to fix before Trillion will work.\n")
    assert doctor.render([Check(name="Voice", status="ok", detail="fine")]).endswith(
        "Everything checks out.\n"
    )
    assert "Ready, with 1 thing worth a look." in doctor.render(
        [Check(name="Port", status="warn", detail="busy")]
    )


# ── The data folder and the database ─────────────────────────────────────


@pytest.mark.parametrize("folder", ["OneDrive", "Dropbox", "Google Drive", "iCloud Drive"])
def test_a_data_folder_inside_a_synced_folder_is_warned_about(tmp_path: Path, folder: str) -> None:
    check = doctor.check_home(tmp_path / folder / "trillion")
    assert check.status == "warn" and "synced folder" in check.detail and "lose" in check.fix


def test_setup_prepares_the_machine_and_can_be_run_again(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prepared(home)
    first = capsys.readouterr().out
    assert "database ready (21 updates applied)" in first and "Start it with: trillion run" in first
    assert (home / "trillion.db").exists() and (home / "persona").is_dir()
    prepared(home)  # a second run finishes what is there and changes nothing
    assert "already prepared: nothing to do" in capsys.readouterr().out
    assert examine()["Database"] == Check(
        name="Database", status="ok", detail="up to date (22 migrations)"
    )


def test_a_database_from_a_newer_trillion_is_not_touched(home: Path) -> None:
    prepared(home)
    with sqlite3.connect(home / "trillion.db") as db:
        db.execute("INSERT INTO schema_migrations VALUES ('999', 'future', 'x', 0)")
    db.close()
    check = examine()["Database"]
    assert check.status == "fail" and "newer Trillion" in check.detail
    assert "upgrade" in check.fix


def test_a_file_that_is_not_a_database_is_reported_not_repaired(home: Path) -> None:
    home.mkdir()
    (home / "trillion.db").write_bytes(b"this is not sqlite" * 100)
    check = examine()["Database"]
    assert check.status == "fail" and "keep a copy" in check.fix.lower()
    assert (home / "trillion.db").read_bytes().startswith(b"this is not sqlite")


def test_doctor_never_writes_to_the_database(home: Path) -> None:
    prepared(home)
    before = (home / "trillion.db").stat().st_mtime_ns
    examine()
    assert (home / "trillion.db").stat().st_mtime_ns == before


# ── The port ─────────────────────────────────────────────────────────────


def test_a_port_held_by_something_else_is_named_with_the_way_round_it() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        check = doctor.check_port("127.0.0.1", port)
    assert check.status == "warn" and "in use by something else" in check.detail
    # The next free one, which is usually but not always the next port, and none when
    # the ports after it are all taken or there are none.
    _, _, after = check.fix.rpartition("--port ")
    if after.isdigit():
        assert port < int(after) <= port + 20
    else:
        assert not any(ch.isdigit() for ch in check.fix)


def test_the_port_it_suggests_instead_is_one_that_is_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy = {5000, 5001}
    monkeypatch.setattr(doctor, "_bindable", lambda host, port: port not in busy)
    monkeypatch.setattr(doctor, "_is_trillion", lambda host, port: False)
    check = doctor.check_port("127.0.0.1", 5000)
    assert check.status == "warn"
    assert "trillion run --port 5002" in check.fix
    assert "5001" not in check.fix


def test_with_no_free_port_nearby_it_names_no_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_bindable", lambda host, port: False)
    monkeypatch.setattr(doctor, "_is_trillion", lambda host, port: False)
    check = doctor.check_port("127.0.0.1", 5000)
    assert check.status == "warn"
    assert "--port" in check.fix and not any(ch.isdigit() for ch in check.fix)


def test_at_the_last_port_it_does_not_say_the_next_ones_are_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "_bindable", lambda host, port: False)
    monkeypatch.setattr(doctor, "_is_trillion", lambda host, port: False)
    check = doctor.check_port("127.0.0.1", 65535)
    assert "busy" not in check.fix
    assert "trillion run --port" in check.fix and "65536" not in check.fix


# ── What the user chose ──────────────────────────────────────────────────


def chosen(home: Path, **prefs: Any) -> None:
    """Save settings the way the app does, straight into the prepared database."""
    import json

    prepared(home)
    with sqlite3.connect(home / "trillion.db") as db:
        for key, value in prefs.items():
            db.execute(
                "INSERT OR REPLACE INTO preferences VALUES ('local', ?, ?, 0)",
                (key, json.dumps(value)),
            )
    db.close()


def test_a_model_that_answers_is_ok_and_one_that_does_not_says_why(
    home: Path, offers: list
) -> None:
    chosen(home, model={"kind": "local", "model": "model-a", "base_url": None})
    assert examine()["Model"].detail == "model-a (chosen in the app) answers"

    chosen(home, model={"kind": "local", "model": "model-a", "base_url": "http://localhost:1/down"})
    down = examine()["Model"]
    assert down.status == "fail" and "could not be reached" in down.detail
    assert "ollama serve" in down.fix

    chosen(home, model={"kind": "local", "model": "gone", "base_url": None})
    missing = examine()["Model"]
    assert missing.status == "warn" and "does not list that model" in missing.detail


def test_a_key_that_is_refused_is_a_failure_and_is_never_printed(home: Path, offers: list) -> None:
    chosen(home, model={"kind": "anthropic", "model": "model-a"})
    secrets = MemoryStore()
    secrets.set(config.secret_name("anthropic"), "wrong")
    check = examine(secrets)["Model"]
    assert check.status == "fail" and "the key was refused" in check.detail
    assert "wrong" not in doctor.render([check])


def test_a_chosen_model_whose_key_is_gone_is_a_failure(home: Path, offers: list) -> None:
    chosen(home, model={"kind": "anthropic", "model": "model-a"})
    check = examine()["Model"]
    assert check.status == "fail" and "key is gone from the keychain" in check.detail
    assert offers == []


def test_offline_tests_nothing_over_the_network(home: Path, offers: list) -> None:
    chosen(
        home,
        model={"kind": "local", "model": "model-a", "base_url": None},
        speech={"kind": "elevenlabs", "voice": "v1"},
    )
    secrets = MemoryStore()
    secrets.set("elevenlabs/api_key", KEY)
    checks = examine(secrets, offline=True, secrets=True)
    assert checks["Model"].detail.endswith("not tested: offline")
    assert checks["Voice"].detail.endswith("not tested: offline")
    assert checks["Key: elevenlabs"].detail.endswith("not tested: offline")
    assert offers == [] and doctor.healthy(list(checks.values()))


def test_hearing_says_when_its_optional_part_is_missing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.voice.providers import whisper

    async def choices(self: Any) -> list[Choice]:
        raise VoiceError("not_installed")

    monkeypatch.setattr(whisper.LocalWhisper, "choices", choices)
    chosen(home, listening={"kind": "whisper", "choice": "base.en"})
    monkeypatch.setattr(paths, "source_checkout", lambda: True)
    check = examine()["Hearing"]
    assert check.status == "fail" and "not installed" in check.detail
    assert "uv sync --extra whisper" in check.fix
    # An installed release has no checkout to sync: it reinstalls with the extra.
    monkeypatch.setattr(paths, "source_checkout", lambda: False)
    fix = examine()["Hearing"].fix
    assert "uv tool install --upgrade --python 3.13 'trillion[whisper]'" in fix
    assert "uv sync" not in fix


def test_names_and_memory_are_reported(home: Path) -> None:
    prepared(home)
    assert examine()["Names"].status == "info"
    setup.save(setup.Profile(user_name="Sam", agent_name="Nova"), paths.persona_dir())
    assert examine()["Names"].detail == "you are Sam, she is Nova"
    (paths.memory_dir() / "user").mkdir(parents=True)
    (paths.memory_dir() / "user" / "mem_0123456789ab.md").write_text("not a memory", "utf-8")
    memory = examine()["Memory"]
    assert memory.status == "warn" and "1 file could not be read" in memory.detail


# ── Every saved key (SEC-9) ──────────────────────────────────────────────


def test_secrets_shows_each_key_works_without_printing_one(
    home: Path, offers: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def choices(self: Deepgram) -> list[Choice]:
        raise VoiceError("unauthorized", f"echo {self._api_key()}")

    monkeypatch.setattr(Deepgram, "choices", choices)
    secrets = MemoryStore()
    secrets.set(config.secret_name("anthropic"), KEY)
    secrets.set("deepgram/api_key", KEY[::-1])
    checks = examine(secrets, secrets=True)
    assert checks["Key: anthropic"].status == "ok"
    assert checks["Key: anthropic"].detail == f"{len(KEY)} characters, ending in 9f3e, works"
    assert checks["Key: deepgram"].status == "fail"
    assert "the key was refused" in checks["Key: deepgram"].detail
    assert checks["Key: openai"].detail == "not set"
    report = doctor.render(list(checks.values()))
    assert KEY not in report and KEY[::-1] not in report and KEY[:12] not in report


def test_a_short_key_shows_its_length_and_nothing_else() -> None:
    assert doctor.shown("abcd1234") == "8 characters"
    assert doctor.shown("x" * 16 + "9f3e") == "20 characters, ending in 9f3e"


# ── The commands ─────────────────────────────────────────────────────────


def test_the_doctor_command_exits_clean_when_healthy_and_not_when_broken(
    home: Path, offers: list, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real built page is used here; whether this checkout's build is fresh is not
    # what this test is about, and a pull without a rebuild would otherwise fail it.
    monkeypatch.setattr(doctor, "_newest_source", lambda found: 0.0)
    port = str(free_port())
    assert main(["doctor", "--offline", "--port", port]) == 0
    assert "Everything checks out." in capsys.readouterr().out
    home.mkdir()
    (home / "trillion.db").write_bytes(b"junk" * 200)
    assert main(["doctor", "--offline", "--port", port]) == 1
    assert "1 thing to fix" in capsys.readouterr().out


def test_setup_stops_before_starting_when_something_is_broken(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_WEB_DIR", str(home / "nowhere"))
    monkeypatch.setattr("trillion.api.app.web_dir", lambda: None)
    assert main(["setup", "--offline", "--port", str(free_port())]) == 1
    out = capsys.readouterr().out
    assert "fail  App page" in out and "Fix the lines marked fail, then run: trillion setup" in out
    assert "Trillion is running" not in out  # it never started


def test_setup_refuses_a_grant_that_is_not_a_folder(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "--no-run", "--grant", str(home / "missing")]) == 2
    assert "not a folder" in capsys.readouterr().err
    assert not home.exists()


def _checkout(tmp_path: Path) -> Path:
    dist = tmp_path / "apps" / "web" / "dist"
    (tmp_path / "apps" / "web" / "src").mkdir(parents=True)
    (tmp_path / "packages" / "galaxy" / "src").mkdir(parents=True)
    dist.mkdir()
    (dist / "index.html").write_text("built")
    return dist


def _touch(path: Path, when: float) -> None:
    path.write_text("x")
    os.utime(path, (when, when))


def test_a_page_built_before_its_source_changed_is_warned_about(tmp_path: Path) -> None:
    dist = _checkout(tmp_path)
    built = (dist / "index.html").stat().st_mtime
    _touch(tmp_path / "apps" / "web" / "src" / "main.ts", built - 60)
    assert doctor.check_page(dist).status == "ok"
    _touch(tmp_path / "packages" / "galaxy" / "src" / "orb.ts", built + 60)
    stale = doctor.check_page(dist)
    assert stale.status == "warn" and "pnpm -r build" in stale.fix


def test_an_installed_page_has_no_source_to_be_older_than(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("built")
    assert doctor.check_page(static).status == "ok"


@pytest.mark.parametrize(
    ("answers", "status", "said"),
    [
        ({}, "warn", "not running"),
        ({"info": "", "list": "1\tspeakers.monitor\tPipeWire\n"}, "warn", "no microphone"),
        ({"info": "", "list": "1\tspeakers.monitor\tx\n2\talsa_input.usb\tx\n"}, "ok", "has a"),
    ],
)
def test_the_microphone_line_checks_the_sound_server_and_a_mic(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, str], status: str, said: str
) -> None:
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(doctor, "_answers", lambda *command: answers.get(command[1]))
    check = doctor.check_microphone()
    assert check.status == status and said in check.detail


def test_secrets_reports_the_keychain_once(home: Path, offers: list) -> None:
    checks = examine(MemoryStore(), secrets=True, offline=True)
    assert checks["Keychain"].status == "ok"
    assert "Secret store" not in checks


@pytest.mark.parametrize(
    ("backend", "said"),
    [
        ("Windows WinVaultKeyring", "Windows Credential Manager"),
        ("macOS Keyring", "macOS Keychain"),
        ("SecretService Keyring", "Secret Service"),
        ("something else Keyring", "something else Keyring"),
        ("", "available"),
    ],
)
def test_the_keychain_line_names_the_store_in_plain_words(backend: str, said: str) -> None:
    check = doctor.check_keychain(MemoryStore(), backend)
    assert check.status == "ok" and check.detail == said


def test_a_made_up_saved_timezone_is_named_with_how_to_fix_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup, "_zones", lambda: {"america/toronto": "America/Toronto"})
    (tmp_path / "USER.md").write_text(
        "---\nuser_name: Sam\ntimezone: America/Torontoo\n---\n", encoding="utf-8"
    )
    check = doctor.check_introduction(tmp_path)
    assert check.status == "warn"
    assert "America/Torontoo" in check.detail and "is ignored" in check.detail
    assert check.detail.startswith("you are Sam, she is Trillion")  # the names are kept
    assert check.fix is not None and "Did you mean America/Toronto?" in check.fix
    (tmp_path / "USER.md").write_text(
        "---\nuser_name: Sam\ntimezone: America/Toronto\n---\n", encoding="utf-8"
    )
    assert doctor.check_introduction(tmp_path).status == "ok"


def _keys(model: config.ModelSettings | None) -> dict[str, Check]:
    secrets = MemoryStore()
    secrets.set(config.secret_name("custom"), KEY)
    found = asyncio.run(doctor.check_credentials(secrets, {}, offline=False, model=model))
    return {c.name: c for c in found}


def test_a_custom_key_is_tested_against_the_chosen_service(offers: list) -> None:
    chosen = config.ModelSettings(kind="custom", model="m", base_url="https://api.example.com/v1")
    checks = _keys(chosen)
    assert checks["Key: custom"].status == "ok"
    assert ("custom", "https://api.example.com/v1", KEY) in offers


def test_an_untested_custom_key_says_why(offers: list) -> None:
    lan = config.ModelSettings(kind="custom", model="m", base_url="http://192.168.1.9:8080/v1")
    assert _keys(lan)["Key: custom"].detail.endswith("the chosen service is sent no key")
    assert _keys(None)["Key: custom"].detail.endswith("no compatible service is chosen")
    assert offers == []


def test_skills_say_how_many_are_on_and_which_need_a_look(home: Path, tmp_path: Path) -> None:
    from trillion.skills.library import Library

    fresh = doctor.check_skills(paths.skills_config_path())
    assert fresh.status == "info" and fresh.detail == "none turned on"
    mine = tmp_path / "mine" / "alpha"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\nx", "utf-8")
    lib = Library(paths.skills_config_path())
    lib.add_folder(str(mine.parent))
    lib.turn_on(f"folder:{mine.resolve()}")
    check = doctor.check_skills(paths.skills_config_path())
    assert check.status == "ok" and check.detail == "1 on"
    (mine / "SKILL.md").write_text("---\nname: alpha\ndescription: e\n---\nx", "utf-8")
    check = doctor.check_skills(paths.skills_config_path())
    assert check.status == "warn" and "changed: alpha" in check.detail and check.fix
    broken = mine.parent / "beta"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no header", "utf-8")
    check = doctor.check_skills(paths.skills_config_path())
    assert "beta" not in check.detail  # not turned on: only counted
    assert "1 other can't be used" in check.detail


def test_skills_that_are_not_turned_on_never_warn(home: Path, tmp_path: Path) -> None:
    from trillion.skills.library import Library

    mine = tmp_path / "mine" / "alpha"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\nx", "utf-8")
    broken = mine.parent / "beta"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no header", "utf-8")
    lib = Library(paths.skills_config_path())
    lib.add_folder(str(mine.parent))
    check = doctor.check_skills(paths.skills_config_path())
    assert check.status == "info" and check.detail == "none turned on; 1 other can't be used"
    lib.turn_on(f"folder:{mine.resolve()}")
    check = doctor.check_skills(paths.skills_config_path())
    assert check.status == "ok" and check.detail == "1 on; 1 other can't be used"


def test_a_skill_folder_that_went_missing_is_reported(home: Path, tmp_path: Path) -> None:
    from trillion.skills.library import Library

    mine = tmp_path / "mine"
    mine.mkdir()
    Library(paths.skills_config_path()).add_folder(str(mine))
    mine.rmdir()
    check = doctor.check_skills(paths.skills_config_path())
    # No skill in it was on: nothing stopped working, so no warning.
    assert check.status == "info" and "1 folder not found" in check.detail


def test_a_missing_folder_with_skills_on_is_reported_once(home: Path, tmp_path: Path) -> None:
    import shutil

    from trillion.skills.library import Library

    mine = tmp_path / "mine" / "alpha"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\nx", "utf-8")
    lib = Library(paths.skills_config_path())
    lib.add_folder(str(mine.parent))
    lib.turn_on(f"folder:{mine.resolve()}")
    shutil.rmtree(mine.parent)
    check = doctor.check_skills(paths.skills_config_path())
    assert check.status == "warn"
    assert f"folder not found: {mine.parent.resolve()}" in check.detail
    assert "turned on but not found" not in check.detail
    assert "http" not in (check.fix or "") and "#show" not in (check.fix or "")


def test_the_examination_includes_skills(home: Path, offers: list) -> None:
    checks = examine()
    assert checks["Skills"].status == "info"


def test_skills_warn_when_their_settings_cannot_be_read(home: Path) -> None:
    path = paths.skills_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    check = doctor.check_skills(path)
    assert check.status == "warn" and "skills.json could not be read" in check.detail


def test_skills_warn_when_one_turned_on_is_gone(home: Path, tmp_path: Path) -> None:
    from trillion.skills.library import Library

    mine = tmp_path / "mine" / "alpha"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\nx", "utf-8")
    lib = Library(paths.skills_config_path())
    lib.add_folder(str(mine.parent))
    lib.turn_on(f"folder:{mine.resolve()}")
    (mine / "SKILL.md").unlink()
    check = doctor.check_skills(paths.skills_config_path())
    assert check.status == "warn"
    assert f"turned on but not found: {mine.resolve()}" in check.detail


# ── Reminders and timed asks ─────────────────────────────────────────────


def scheduled(home: Path, *runs: tuple[str, int]) -> None:
    """One active weekday reminder, and runs of it with these statuses and times."""
    prepared(home)
    with closing(sqlite3.connect(home / "trillion.db")) as db, db:
        db.execute(
            "INSERT INTO schedules (id, owner_id, kind, title, body, zone, at_local, repeat,"
            " next_due_ms, status, created_ms, created_in)"
            " VALUES ('a1b2c3d4', 'local', 'ask', 'Email', 'Check.',"
            " 'America/New_York', '2026-10-26T09:00', '{\"every\": \"weekday\"}',"
            " 1793019600000, 'active', 0, 'c1')"
        )
        for status, at in runs:
            db.execute(
                "INSERT INTO task_runs (schedule_id, due_ms, status, finished_ms, timeout_s,"
                " created_ms) VALUES ('a1b2c3d4', ?, ?, ?, 120, 0)",
                (at, status, at),
            )


def test_the_scheduler_line_says_what_is_set_and_when_next(home: Path) -> None:
    assert examine()["Scheduler"].detail == "no reminders or timed asks set"
    scheduled(home)
    check = doctor.check_scheduler(home / "trillion.db", 1_792_857_600_000)
    assert check.status == "ok" and check.detail == "1 active; next due Mon 26 Oct, 9:00 am"


def test_a_paused_routines_time_is_not_next(home: Path) -> None:
    scheduled(home)
    with closing(sqlite3.connect(home / "trillion.db")) as db, db:
        db.execute("UPDATE schedules SET target = 'own', paused_ms = 1 WHERE id = 'a1b2c3d4'")
    check = doctor.check_scheduler(home / "trillion.db", 1_792_857_600_000)
    assert check.status == "ok" and check.detail == "1 active"


def test_a_failed_or_blocked_run_in_the_last_day_is_worth_a_look(home: Path) -> None:
    now = 1_792_857_600_000
    scheduled(home, ("failed", now - 3_600_000), ("delivered", now - 60_000))
    check = doctor.check_scheduler(home / "trillion.db", now)
    assert check.status == "warn" and "1 run blocked or failed in the last day" in check.detail
    assert "Settings → Reminders" in check.fix
    older = doctor.check_scheduler(home / "trillion.db", now + 2 * doctor.DAY_MS)
    assert older.status == "ok"


# ── Recall by meaning (MEM-2, ONB-7) ─────────────────────────────────────


def test_the_meaning_line_says_whether_memory_is_searched_by_meaning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.memory import meaning
    from trillion.voice.models import ModelFile, ModelPack

    monkeypatch.setattr(meaning, "runtime_installed", lambda: False)
    off = doctor.check_meaning(tmp_path)
    assert off.status == "info" and off.detail == "off: memory is found by its words"
    assert off.fix == "Install trillion[meaning], then run: trillion setup"
    monkeypatch.setattr(meaning, "runtime_installed", lambda: True)
    missing = doctor.check_meaning(tmp_path)
    assert (
        missing.detail == "off: the model is not downloaded"
        and missing.fix == "Run: trillion setup"
    )
    tiny = ModelPack(id="tiny", base_url="", files=(ModelFile(name="m.onnx", size=2, sha256=""),))
    monkeypatch.setattr(meaning, "PACK", tiny)
    (tmp_path / "tiny").mkdir()
    (tmp_path / "tiny" / "m.onnx").write_bytes(b"ok")
    assert doctor.check_meaning(tmp_path) == Check(name="Meaning", status="ok", detail="on: tiny")


def test_the_examination_includes_meaning(home: Path, offers: list) -> None:
    assert examine()["Meaning"].status in ("ok", "info")


@pytest.mark.parametrize("fails", [False, True])
def test_setup_fetches_the_meaning_model_only_when_it_can_be_used(
    home: Path,
    offers: list,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fails: bool,
) -> None:
    from trillion.memory import meaning

    fetched: list[Path] = []

    async def fetch(models_dir: Path, **_: Any) -> None:
        fetched.append(models_dir)
        if fails:
            raise meaning.MeaningError("unreachable")

    monkeypatch.setattr(meaning, "fetch", fetch)
    monkeypatch.setattr(meaning, "runtime_installed", lambda: False)
    prepared(home)  # offline: nothing fetched
    assert main(["setup", "--no-run", "--port", str(free_port())]) == 0
    assert fetched == []  # not installed: nothing to fetch it for
    monkeypatch.setattr(meaning, "runtime_installed", lambda: True)
    prepared(home)
    assert fetched == []  # --offline fetches nothing, ever
    capsys.readouterr()
    assert (
        main(["setup", "--no-run", "--port", str(free_port())]) == 0
    )  # a failure never stops setup
    out = capsys.readouterr().out
    assert fetched == [paths.models_dir()]
    if fails:
        assert "meaning model not fetched (unreachable): memory is found by its words" in out
    else:
        assert "setup  fetched the meaning model (" in out
