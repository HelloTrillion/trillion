"""`trillion doctor`: what is set up on this machine, what is not, and how to fix it (ONB-5).

It measures and never repairs: nothing here creates a file, applies a migration or
changes a setting. The database is opened read-only. The only things it contacts are
the providers the user already chose, and only to ask what they offer, which costs
nothing and proves a key works. `--offline` skips even that.

No credential is ever printed. A key is shown as present, with its length and its
last four characters at most (SEC-4, SEC-9).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal

from trillion import __version__, build, owner_only, paths
from trillion.brain import config
from trillion.brain.config import ConfigError, ModelSettings
from trillion.brain.types import ProviderError
from trillion.secrets.fallback import PASSPHRASE_ENV, FileStore
from trillion.secrets.store import SecretStore
from trillion.storage import inspect
from trillion.voice.catalog import LISTENING, SPEECH, listen_option, speech_option
from trillion.voice.types import VoiceError

Status = Literal["ok", "info", "warn", "fail"]
OWNER = "local"
NETWORK_TIMEOUT_S = 8.0
DAY_MS = 24 * 60 * 60 * 1000
# Folders that sync clients own. A database or a memory file inside one can lose writes (OPS-2).
_SYNCED = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "mobile documents")


@dataclass(frozen=True, kw_only=True)
class Check:
    name: str
    status: Status
    detail: str
    # What to do about it, as something the user can act on. Empty when there is nothing to do.
    fix: str = ""


@dataclass(frozen=True, kw_only=True)
class Options:
    port: int = 7777
    host: str = "127.0.0.1"
    # Skip every check that needs the network.
    offline: bool = False
    # Also test every saved credential, not only the ones in use.
    secrets: bool = False


def shown(value: str) -> str:
    """A credential as diagnostics may show it: never the value (SEC-4)."""
    ending = f", ending in {value[-4:]}" if len(value) >= 20 else ""
    return f"{len(value)} characters{ending}"


# ── This machine ─────────────────────────────────────────────────────────


def check_build() -> Check:
    v = sys.version_info
    detail = f"Trillion {__version__}, Python {v.major}.{v.minor}.{v.micro}, {platform.system()}"
    if (v.major, v.minor) < (3, 13):
        return Check(
            name="Version",
            status="fail",
            detail=detail,
            fix="Trillion needs Python 3.13 or newer. Reinstall with: uv tool install trillion",
        )
    return Check(name="Version", status="ok", detail=detail)


def check_home(home: Path) -> Check:
    name = "Data folder"
    if any(mark in part.lower() for part in home.parts for mark in _SYNCED):
        return Check(
            name=name,
            status="warn",
            detail=f"{home} is inside a synced folder",
            fix="Sync clients can cut off a write and lose a memory. Move it out, or point "
            "TRILLION_HOME at a folder that is not synced.",
        )
    if not home.exists():
        return Check(name=name, status="info", detail=f"{home} will be made on the first run")
    try:
        with tempfile.NamedTemporaryFile(dir=home, prefix=".doctor-"):
            pass
    except OSError:
        return Check(
            name=name,
            status="fail",
            detail=f"{home} cannot be written to",
            fix="Make the folder yours to write, or point TRILLION_HOME at one that is.",
        )
    return Check(name=name, status="ok", detail=str(home))


def check_privacy(home: Path) -> Check | None:
    """Whether anyone but its owner can open the data folder, on every system. None
    before it exists. Trillion limits it on each start; this only looks."""
    if not home.is_dir():
        return None
    name = "Data folder access"
    who = owner_only.exposed(home)
    if who is None:
        return Check(name=name, status="info", detail="could not tell who else can open it")
    if not who:
        return Check(name=name, status="ok", detail="only you can open it")
    return Check(
        name=name,
        status="warn",
        detail=f"{' and '.join(who)} can open it too",
        fix="Run: trillion setup. It limits the folder to your account.",
    )


def check_database(path: Path) -> Check:
    name = "Database"
    if not path.exists():
        return Check(name=name, status="info", detail="not made yet: the first run creates it")
    try:
        state = inspect.schema(path)
    except inspect.Unreadable:
        return Check(
            name=name,
            status="fail",
            detail=f"{path} could not be read",
            fix="Stop Trillion if it is running and try again. If it still fails, the file is "
            "damaged: keep a copy, then move it aside so a new one is made.",
        )
    healthy, applied = state.sound, state.applied
    shipped = inspect.shipped()
    if not healthy:
        return Check(
            name=name,
            status="fail",
            detail="the file failed its integrity check",
            fix="Keep a copy of the file, then move it aside so a new one is made.",
        )
    if set(applied) - set(shipped):
        return Check(
            name=name,
            status="fail",
            detail="it was last used by a newer Trillion than this one",
            fix="Update Trillion: uv tool upgrade trillion",
        )
    if any(applied[v] != shipped[v] for v in applied):
        return Check(
            name=name,
            status="fail",
            detail="its history does not match this build",
            fix="This build's migrations differ from the ones that made the database. "
            "Reinstall the released version.",
        )
    pending = len(set(shipped) - set(applied))
    if pending:
        s = "" if pending == 1 else "s"
        return Check(name=name, status="info", detail=f"{pending} update{s} will apply on start")
    return Check(name=name, status="ok", detail=f"up to date ({len(applied)} migrations)")


def _is_trillion(host: str, port: int) -> bool:
    """Whether the thing on the port answers the way this app's own server does.

    A settings request with no token is refused in a way that is ours: a 401 whose
    whole body is one word. A page that merely mentions the name does not count."""
    try:
        urllib.request.urlopen(f"http://{host}:{port}/api/settings/persona", timeout=1.5).close()
    except urllib.error.HTTPError as e:
        return e.code == 401 and e.read(64) == b"unauthenticated"
    except (OSError, ValueError):
        return False
    return False


# How far past a busy port to look for a free one to suggest.
_NEARBY = 20


def _bindable(host: str, port: int) -> bool:
    """Whether a server could listen on the port now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "posix":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def check_port(host: str, port: int) -> Check:
    name = "Port"
    if _bindable(host, port):
        return Check(name=name, status="ok", detail=f"{port} is free")
    if _is_trillion(host, port):
        return Check(name=name, status="ok", detail=f"{port}: Trillion is already running on it")
    # Suggest a port that is free now, not one that may be just as busy.
    nearby = range(port + 1, min(port + 1 + _NEARBY, 65536))
    spare = next((p for p in nearby if _bindable(host, p)), None)
    if spare is not None:
        instead = f"start on another: trillion run --port {spare}"
    elif nearby:
        instead = "start on another with trillion run --port (the next few are busy too)"
    else:
        instead = "start on another with trillion run --port, choosing a lower one"
    return Check(
        name=name,
        status="warn",
        detail=f"{port} is in use by something else",
        fix=f"Stop what is using it, or {instead}",
    )


def _newest_source(found: Path) -> float:
    """In a source checkout, when the page's sources last changed. 0 for an installed build."""
    root = found.parents[2] if found.name == "dist" and len(found.parents) > 2 else None
    if root is None or not (found.parent / "src").is_dir():
        return 0.0
    sources = [found.parent / "src", found.parent / "index.html", found.parent / "orb.html"]
    sources += [pkg / "src" for pkg in (root / "packages").glob("*")]
    newest = 0.0
    for source in sources:
        files = source.rglob("*") if source.is_dir() else [source]
        newest = max([newest, *(f.stat().st_mtime for f in files if f.is_file())])
    return newest


def check_page(found: Path | None) -> Check:
    fix = "In a source checkout: pnpm install && pnpm -r build"
    if found is None:
        return Check(
            name="App page",
            status="fail",
            detail="the page the browser opens has not been built",
            fix=fix,
        )
    built = found / "index.html"
    if built.is_file() and _newest_source(found) > built.stat().st_mtime:
        return Check(
            name="App page",
            status="warn",
            detail=f"{found} is older than its source, so the browser gets the old app",
            fix=fix,
        )
    return Check(name="App page", status="ok", detail=str(found))


def check_running(host: str, page: Path | None, home: Path | None = None) -> Check | None:
    """Whether the Trillion running is the one on disk (ONB-11). None when none is."""
    note = build.read(home)
    if note is None or not build.alive(note.pid) or not _is_trillion(host, note.port):
        return None
    name = "Running build"
    where = f"on port {note.port} since {_clock(note.started_at)}"
    if note.fingerprint != build.fingerprint(page):
        return Check(
            name=name,
            status="warn",
            detail=f"Trillion {where} is not running the files on disk: they changed after "
            "it started",
            fix="Stop it (Ctrl+C) and start it again, so what runs is what is installed.",
        )
    return Check(name=name, status="ok", detail=f"Trillion {where} runs the files on disk")


def _clock(ms: int) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M on %b %d")


# What the `keyring` library calls each backend, in the words people know it by.
KEYCHAIN_NAMES = {
    "macOS Keyring": "macOS Keychain",
    "Windows WinVaultKeyring": "Windows Credential Manager",
    "SecretService Keyring": "Secret Service",
    "libsecret Keyring": "Secret Service",
    "kwallet DBusKeyring": "KDE Wallet",
}


def check_key_file(store: FileStore) -> Check:
    """Which store is in use when it is the encrypted file, and whether it is open (SEC-9).
    Never a value, never the passphrase."""
    name = "Key file"
    where = str(store.path)
    if store.available():
        how = "by the system keychain" if store.unlocked_by == "keychain" else "with a passphrase"
        return Check(name=name, status="ok", detail=f"{where}, unlocked {how}")
    problem = store.problem
    if problem is None or problem.kind in {"locked", "wrong_passphrase"}:
        detail = str(problem) if problem else f"{where} is locked"
        return Check(
            name=name,
            status="warn",
            detail=f"{detail}: features that need a key are off",
            fix=problem.fix
            if problem and problem.fix
            else f"Set {PASSPHRASE_ENV}, or run trillion doctor --secrets in a terminal.",
        )
    return Check(name=name, status="fail", detail=str(problem), fix=problem.fix)


def backend_name(backend: Any) -> str:
    """The name `keyring` gives the backend it chose. When several work, it chains
    them, and the first one is where a new key is written."""
    chained = getattr(backend, "backends", None)
    if isinstance(chained, list | tuple) and chained:
        backend = chained[0]
    return str(getattr(backend, "name", ""))


def check_keychain(secrets: SecretStore, backend: str = "") -> Check:
    if isinstance(secrets, FileStore):
        return check_key_file(secrets)
    name = "Keychain"
    if secrets.available():
        shown = KEYCHAIN_NAMES.get(backend, backend or "available")
        return Check(name=name, status="ok", detail=shown)
    fix = (
        "Without one, a key cannot be saved in the app. A local model still works. "
        "To keep keys in an encrypted file instead: trillion setup --secrets-file"
    )
    if sys.platform.startswith("linux"):
        fix = (
            "Install and unlock a Secret Service keychain (GNOME Keyring or KWallet), "
            "or keep keys in an encrypted file: trillion setup --secrets-file. "
            "Without either, a key cannot be saved in the app. A local model still works."
        )
    return Check(name=name, status="warn", detail="this machine has no system keychain", fix=fix)


def _answers(*command: str) -> str | None:
    """A sound tool's output, or None if it is missing, fails or hangs."""
    if shutil.which(command[0]) is None:
        return None
    try:
        # Only the fixed sound-tool commands above, never anything the user typed.
        done = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _linux_sound() -> tuple[bool, bool]:
    """Whether a sound server answers, and whether it has a microphone.

    pactl speaks for both PulseAudio and PipeWire's Pulse layer, which is what the
    browser itself talks to. A monitor source is speaker output, not a microphone."""
    if shutil.which("pactl"):
        if _answers("pactl", "info") is None:
            return False, False
        sources = _answers("pactl", "list", "short", "sources") or ""
        names = [line.split("\t")[1] for line in sources.splitlines() if "\t" in line]
        return True, any(not n.endswith(".monitor") for n in names)
    if _answers("wpctl", "status") is None:
        return False, False
    return True, _answers("wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@") is not None


def check_microphone() -> Check:
    name = "Microphone"
    told = "your browser opens it and asks permission the first time you press Speak"
    if sys.platform.startswith("linux"):
        server = "PipeWire" if shutil.which("wpctl") or shutil.which("pw-cli") else None
        server = server or ("PulseAudio" if shutil.which("pactl") else None)
        if server is not None:
            running, mic = _linux_sound()
            if not running:
                return Check(
                    name=name,
                    status="warn",
                    detail=f"{server} is installed but not running",
                    fix=f"Start {server} (or log out and back in) so the browser has a "
                    "microphone to open.",
                )
            if not mic:
                return Check(
                    name=name,
                    status="warn",
                    detail=f"{server} is running, but no microphone is connected",
                    fix="Plug in or turn on a microphone. You can still type to her.",
                )
            return Check(name=name, status="ok", detail=f"{server} has a microphone; {told}")
        return Check(
            name=name,
            status="warn",
            detail="no PipeWire or PulseAudio was found",
            fix="Install PipeWire (or PulseAudio) so the browser has a microphone to open.",
        )
    if sys.platform == "win32":
        return _windows_microphone(told)
    return Check(name=name, status="info", detail=told)


def _windows_microphone(told: str) -> Check:
    """The microphones Windows lists, and its privacy switches. Opens none of them."""
    from trillion.cli import windows_audio

    name = "Microphone"
    allowed = windows_audio.access()
    if allowed in ("off", "desktop_off"):
        switch = (
            "Microphone access" if allowed == "off" else "Let desktop apps access your microphone"
        )
        return Check(
            name=name,
            status="warn",
            detail=f"Windows' privacy settings keep apps off the microphone ({switch} is off)",
            fix="Open Settings → Privacy & security → Microphone and turn on Microphone access "
            "and Let desktop apps access your microphone.",
        )
    found = windows_audio.inputs()
    if found is None:
        return Check(name=name, status="info", detail=f"Windows did not list its devices; {told}")
    if not found.names:
        return Check(
            name=name,
            status="warn",
            detail="Windows lists no microphone",
            fix="Plug in or turn on a microphone, or enable it in Settings → System → Sound. "
            "You can still type to her.",
        )
    count = len(found.names)
    listed = f"{count} microphone{'s' if count != 1 else ''}"
    if found.default is None:
        return Check(
            name=name,
            status="warn",
            detail=f"{listed} ({', '.join(found.names)}), but none is the default",
            fix="Choose one in Settings → System → Sound → Input.",
        )
    return Check(name=name, status="ok", detail=f"{listed}; default: {found.default}; {told}")


# ── What the user chose ──────────────────────────────────────────────────


async def _within[T](call: Awaitable[T]) -> T:
    return await asyncio.wait_for(call, NETWORK_TIMEOUT_S)


_WHY: Mapping[str, str] = {
    "unauthorized": "the key was refused",
    "forbidden": "the key is not allowed to do this",
    "rate_limited": "the provider is rate limiting this key",
    "unreachable": "it could not be reached",
    "timeout": "it did not answer in time",
    "not_installed": "its optional part is not installed",
}


def _why(kind: str) -> str:
    return _WHY.get(kind, f"it failed ({kind})")


async def _try(call: Callable[[], Awaitable[Any]]) -> str | None:
    """Run one provider check. None if it worked, otherwise a safe word for why not."""
    try:
        await _within(call())
    except TimeoutError:
        return "timeout"
    except (ProviderError, VoiceError) as e:
        return e.kind
    except ConfigError as e:
        return e.code
    except OSError:
        return "unreachable"
    return None


async def check_model(
    settings: ModelSettings | None,
    secrets: SecretStore,
    env: Mapping[str, str],
    *,
    offline: bool,
) -> Check:
    name = "Model"
    base, key = env.get(config.OPENAI_BASE_URL, "").strip(), None
    try:
        provider, source = config.resolve(settings, secrets, env)
    except ConfigError as e:
        return Check(
            name=name,
            status="fail",
            detail=f"the environment's model settings are not usable: {e}",
            fix="Fix or unset the TRILLION_ variables for the model.",
        )
    if provider is None:
        if settings is not None:
            return Check(
                name=name,
                status="fail",
                detail=f"{settings.model} is chosen, but its key is gone from the keychain",
                fix="Open the app and enter the key again in the model settings.",
            )
        return Check(name=name, status="info", detail="none chosen yet: the app asks on first run")
    if source == "env":
        kind: config.Kind = "custom" if base else "anthropic"
        key = env.get(config.OPENAI_KEY if base else config.ANTHROPIC_KEY) or None
        url = base or None
    elif settings is not None:
        kind, url = settings.kind, settings.base_url
        key = secrets.get(config.secret_name(kind))
    else:  # pragma: no cover - resolve() only builds from the environment or from settings
        return Check(name=name, status="info", detail="none chosen yet")
    said = f"{provider.model} ({'environment' if source == 'env' else 'chosen in the app'})"
    if offline:
        return Check(name=name, status="info", detail=f"{said}, not tested: offline")

    found: list[Any] = []

    async def ask() -> None:
        found.extend(await config.list_models(kind, url, key))

    problem = await _try(ask)
    if problem is not None:
        local = kind == "local" or (url is not None and config.keyless(url))
        fix = (
            "Start the model server on this computer (for Ollama: ollama serve) and try again."
            if local
            else "Check the key and your connection, then run: trillion doctor"
        )
        return Check(name=name, status="fail", detail=f"{said}: {_why(problem)}", fix=fix)
    if not any(m.id == provider.model for m in found):
        return Check(
            name=name,
            status="warn",
            detail=f"{said} answered, but does not list that model",
            fix="Open the app and choose a model it offers.",
        )
    return Check(name=name, status="ok", detail=f"{said} answers")


async def check_speech(raw: Any, secrets: SecretStore, *, offline: bool) -> Check:
    name = "Voice"
    option = speech_option(str(raw.get("kind"))) if isinstance(raw, dict) else None
    if option is None:
        return Check(name=name, status="info", detail="off: she answers in text")
    if option.where == "client":
        return Check(name=name, status="ok", detail=f"{option.title}: spoken by your browser")
    if option.needs_key and not secrets.get(option.secret):
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title} is chosen, but its key is gone from the keychain",
            fix="Open the app and enter the key again in the voice settings.",
        )
    if offline:
        return Check(name=name, status="info", detail=f"{option.title}, not tested: offline")
    url = str(raw["base_url"]) if raw.get("base_url") else None
    problem = await _try(lambda: option.build(secrets, url).voices())
    if problem is not None:
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title}: {_why(problem)}",
            fix="Open the voice settings in the app and check the key or the address.",
        )
    return Check(name=name, status="ok", detail=f"{option.title} answers")


async def check_listening(raw: Any, secrets: SecretStore, *, offline: bool) -> Check:
    name = "Hearing"
    option = listen_option(str(raw.get("kind"))) if isinstance(raw, dict) else None
    if option is None:
        return Check(name=name, status="info", detail="off: you type to her")
    if option.needs_key and not secrets.get(option.secret):
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title} is chosen, but its key is gone from the keychain",
            fix="Open the app and enter the key again in the mic settings.",
        )
    if option.needs_key and offline:
        return Check(name=name, status="info", detail=f"{option.title}, not tested: offline")
    choices: list[Any] = []

    async def ask() -> None:
        choices.extend(await option.build(secrets).choices())

    problem = await _try(ask)
    if problem == "not_installed":
        hint = f" Install it with: {option.install_hint}" if option.install_hint else ""
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title} is chosen, but it is not installed",
            fix=f"The part of Trillion that does this is optional.{hint}",
        )
    if problem is not None:
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title}: {_why(problem)}",
            fix="Open the mic settings in the app and check the key.",
        )
    chosen = next((c for c in choices if c.id == raw.get("choice")), None)
    if chosen is not None and chosen.download is not None:
        return Check(
            name=name,
            status="warn",
            detail=f"{option.title} is chosen, but its model has not been downloaded",
            fix="Open the mic settings in the app and press the download button once.",
        )
    return Check(name=name, status="ok", detail=f"{option.title} is ready")


def check_introduction(directory: Path) -> Check:
    from trillion.persona import setup
    from trillion.persona.prompt import fields

    profile = setup.load(directory)
    saved = fields(directory / "USER.md").get("timezone", "")
    if profile is not None and saved and (bad := setup.unknown_zone(saved)):
        # The names were kept; only the timezone was set aside (see setup.load).
        hint = f" Did you mean {bad.suggestion}?" if bad.suggestion else ""
        return Check(
            name="Names",
            status="warn",
            detail=(
                f"you are {profile.user_name}, she is {profile.agent_name}, but the saved "
                f"timezone ({saved}) is not one this machine knows, so it is ignored"
            ),
            fix=f"Change the timezone line in USER.md to a name like America/Toronto.{hint}",
        )
    if profile is None:
        return Check(
            name="Names", status="info", detail="not introduced yet: she asks on the first run"
        )
    detail = f"you are {profile.user_name}, she is {profile.agent_name}"
    return Check(name="Names", status="ok", detail=detail)


def check_memory(directory: Path) -> Check:
    from trillion.memory import files as memory_files

    if not directory.exists():
        return Check(name="Memory", status="info", detail="nothing saved yet")
    scan = memory_files.scan(directory)
    total = len(scan.found)
    said = "nothing saved yet" if total == 0 else f"{total} saved"
    if scan.unreadable or not scan.complete:
        bad = len(scan.unreadable)
        return Check(
            name="Memory",
            status="warn",
            detail=f"{said}; {bad} file{'s' if bad != 1 else ''} could not be read",
            fix=f"Look in {directory} for a file that is not a memory, or was cut off.",
        )
    return Check(name="Memory", status="ok" if total else "info", detail=said)


def check_meaning(models_dir: Path) -> Check:
    """Whether memory is searched by meaning as well as words (MEM-2). Looks at what is
    installed and fetched; loads nothing and fetches nothing."""
    from trillion.memory import meaning

    name = "Meaning"
    found = meaning.state(models_dir)
    if found == "no_runtime":
        return Check(
            name=name,
            status="info",
            detail="off: memory is found by its words",
            fix="Install trillion[meaning], then run: trillion setup",
        )
    if found == "no_model":
        return Check(
            name=name,
            status="info",
            detail="off: the model is not downloaded",
            fix="Run: trillion setup",
        )
    return Check(name=name, status="ok", detail=f"on: {meaning.PACK.id}")


# ── Every saved credential (SEC-9) ───────────────────────────────────────


async def check_credentials(
    secrets: SecretStore,
    env: Mapping[str, str],
    *,
    offline: bool,
    model: ModelSettings | None = None,
) -> list[Check]:
    """One line per place a key can live: whether it is there and whether it works.

    Whether the keychain itself is there is already the Keychain line."""
    out: list[Check] = []

    async def report(
        name: str,
        value: str | None,
        test: Callable[[], Awaitable[Any]] | None,
        untested: str = "",
    ) -> None:
        if not value:
            out.append(Check(name=name, status="info", detail="not set"))
            return
        if offline or test is None:
            why = "offline" if offline else untested
            said = f"not tested: {why}" if why else "not tested"
            out.append(Check(name=name, status="info", detail=f"{shown(value)}, {said}"))
            return
        problem = await _try(test)
        if problem is None:
            out.append(Check(name=name, status="ok", detail=f"{shown(value)}, works"))
        else:
            out.append(
                Check(
                    name=name,
                    status="fail",
                    detail=f"{shown(value)}: {_why(problem)}",
                    fix="Replace it in the app's settings.",
                )
            )

    hosted: tuple[config.Kind, ...] = ("anthropic", "openai")
    for kind in hosted:
        value = secrets.get(config.secret_name(kind))
        await report(f"Key: {kind}", value, partial(config.list_models, kind, None, value))
    # A compatible service's key belongs to an address only the saved settings know.
    custom = secrets.get(config.secret_name("custom"))
    url = model.base_url if model is not None and model.kind == "custom" else None
    if url is None:
        await report("Key: custom", custom, None, "no compatible service is chosen")
    elif config.keyless(url):
        await report("Key: custom", custom, None, "the chosen service is sent no key")
    else:
        await report("Key: custom", custom, partial(config.list_models, "custom", url, custom))
    for speech in SPEECH:
        if speech.needs_key:
            await report(
                f"Key: {speech.kind}",
                secrets.get(speech.secret),
                speech.build(secrets, None).voices,
            )
    for listen in LISTENING:
        if listen.needs_key:
            await report(
                f"Key: {listen.kind}",
                secrets.get(listen.secret),
                listen.build(secrets).choices,
            )
    for variable in (config.ANTHROPIC_KEY, config.OPENAI_KEY):
        value = env.get(variable)
        if value:
            out.append(Check(name=f"Env: {variable}", status="info", detail=f"{shown(value)}, set"))
    return out


# ── Connected tools (MCP) ────────────────────────────────────────────────


def check_mcp(path: Path, secrets: SecretStore) -> list[Check]:
    """The MCP servers in mcp.json (CON-5): only read, never started from here."""
    from trillion.connectors.mcp.config import load, plain_secrets

    loaded = load(path)
    out: list[Check] = []
    for name, why in loaded.problems.items():
        if name in loaded.foreign:
            # It names a key that is not its own: it would be sent someone else's.
            out.append(
                Check(
                    name=f"MCP: {name}",
                    status="fail",
                    detail=f"refused: {why}",
                    fix=f"Remove that reference from {path}, or remove the server.",
                )
            )
            continue
        out.append(
            Check(
                name=f"MCP: {name}",
                status="warn",
                detail=f"skipped: {why}",
                fix=f"Fix it in {path}",
            )
        )
    for server in loaded.servers:
        label = f"MCP: {server.name}"
        if server.disabled:
            out.append(Check(name=label, status="info", detail="turned off in mcp.json"))
            continue
        missing = [k for k in server.key_names() if not secrets.get(k)]
        in_file = plain_secrets(server)
        if missing:
            out.append(
                Check(
                    name=label,
                    status="fail",
                    detail=f"needs a key that is not saved: {', '.join(missing)}",
                    fix="Save it in the keychain under that name (Settings will do this soon).",
                )
            )
        elif server.transport == "stdio" and shutil.which(server.command) is None:
            out.append(
                Check(
                    name=label,
                    status="warn",
                    detail=f"its program {server.command!r} was not found",
                    fix="Install it, or give its full path as the command in mcp.json.",
                )
            )
        else:
            free = sum(r.value == "read" for r in server.risks.values())
            asks = f"{free} tools run without asking" if free else "every tool asks first"
            where = server.command if server.transport == "stdio" else server.url
            out.append(Check(name=label, status="ok", detail=f"{where}; {asks}"))
        if in_file:
            out.append(
                Check(
                    name=label,
                    status="warn",
                    detail=f"a key is written into mcp.json ({', '.join(in_file)})",
                    fix='Save it in the keychain and write "keychain:NAME" in its place.',
                )
            )
    return out


def check_skills(path: Path) -> Check:
    """Skills (CON-6): how many are on, and a warning only for what stops a skill the
    user turned on: one that changed, broke or went missing, a folder of theirs that
    is gone, or a settings file that can't be read, which leaves every skill off.
    Skills that were never turned on are only counted. The library is built as the
    running app builds it; nothing here writes."""
    from trillion.skills.library import Library, LibraryError

    lib = Library(path)
    try:
        lost, unreadable = lib.lost(), ""
    except LibraryError as e:
        lost, unreadable = [], str(e)
    turned = lib.turned_on()
    on = [e for e in turned if e.state == "on"]
    changed = [e.name for e in turned if e.state == "changed"]
    broken = [e.name for e in turned if e.state == "invalid"]
    others = sum(1 for e in lib.scan() if e.state == "invalid") - len(broken)
    missing = [f.path for f in lib.folders() if not f.found]

    def under(sid: str, folder: Path) -> bool:
        skill = Path(sid.removeprefix("folder:"))
        return sid.startswith("folder:") and folder in (skill, skill.parent)

    # A folder that is gone is named once, not again for each skill that was on in it.
    gone = [f for f in missing if any(under(sid, f) for sid in lost)]
    alone = [sid for sid in lost if not any(under(sid, f) for f in gone)]
    problems = [
        *([unreadable] if unreadable else []),
        *([f"changed: {', '.join(changed)}"] if changed else []),
        *([f"can't be used: {', '.join(broken)}"] if broken else []),
        *([f"folder not found: {', '.join(map(str, gone))}"] if gone else []),
        *(f"turned on but not found: {' '.join(sid.partition(':')[2].split())}" for sid in alone),
    ]
    count = f"{len(on)} on" if on or problems else "none turned on"
    away = len(missing) - len(gone)
    notes = [
        *([f"{others} other can't be used"] if others > 0 else []),
        *([f"{away} folder{'s' if away > 1 else ''} not found"] if away else []),
    ]
    if problems:
        return Check(
            name="Skills",
            status="warn",
            detail="; ".join([count, *problems, *notes]),
            fix="Open Settings → Skills to look at them again.",
        )
    return Check(name="Skills", status="ok" if on else "info", detail="; ".join([count, *notes]))


def check_scheduler(path: Path, now_ms: int) -> Check:
    """Reminders and timed asks (AGT-4): how many are set and when the next is due. A run
    blocked or failed in the last day is worth a look. Read from the database only:
    doctor never starts the scheduler."""
    from trillion.scheduler.when import WhenError, spoken

    name = "Scheduler"
    found = inspect.schedules(path, OWNER, now_ms - DAY_MS)
    if found is None or not (found["active"] or found["troubled"]):
        return Check(name=name, status="info", detail="no reminders or timed asks set")
    detail = f"{found['active']} active"
    if found["next_ms"] is not None:
        try:
            detail += f"; next due {spoken(found['next_ms'], found['zone'])}"
        except WhenError:
            detail += "; next due at a time in a timezone this computer doesn't know"
    troubled = found["troubled"]
    if troubled:
        runs = "run" if troubled == 1 else "runs"
        return Check(
            name=name,
            status="warn",
            detail=f"{detail}; {troubled} {runs} blocked or failed in the last day",
            fix="Open Settings → Reminders to see which. A blocked one is waiting for a model.",
        )
    return Check(name=name, status="ok", detail=detail)


# ── The whole examination ────────────────────────────────────────────────


async def examine(
    options: Options,
    *,
    secrets: SecretStore,
    env: Mapping[str, str] | None = None,
    page: Path | None = None,
    backend: str = "",
) -> list[Check]:
    env = os.environ if env is None else env
    prefs = inspect.preferences(paths.db_path(), OWNER)
    model = ModelSettings.from_json(prefs.get("model"))
    checks = [
        check_build(),
        check_home(paths.home()),
        *([access] if (access := check_privacy(paths.home())) else []),
        check_database(paths.db_path()),
        check_page(page),
        check_port(options.host, options.port),
        *([running] if (running := check_running(options.host, page)) else []),
        check_keychain(secrets, backend),
        await check_model(model, secrets, env, offline=options.offline),
        await check_speech(prefs.get("speech"), secrets, offline=options.offline),
        await check_listening(prefs.get("listening"), secrets, offline=options.offline),
        check_microphone(),
        check_introduction(paths.persona_dir()),
        check_memory(paths.memory_dir()),
        check_meaning(paths.models_dir()),
        *check_mcp(paths.mcp_config_path(), secrets),
        check_skills(paths.skills_config_path()),
        check_scheduler(paths.db_path(), time.time_ns() // 1_000_000),
    ]
    if options.secrets:
        checks += await check_credentials(secrets, env, offline=options.offline, model=model)
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        lines.append(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
        if c.fix:
            lines.append(f"{'':<4}  {'':<{width}}  -> {c.fix}")
    failed = sum(c.status == "fail" for c in checks)
    warned = sum(c.status == "warn" for c in checks)
    if failed:
        verdict = f"{failed} thing{'s' if failed != 1 else ''} to fix before Trillion will work."
    elif warned:
        verdict = f"Ready, with {warned} thing{'s' if warned != 1 else ''} worth a look."
    else:
        verdict = "Everything checks out."
    return "\n".join([*lines, "", verdict]) + "\n"


def healthy(checks: list[Check]) -> bool:
    return not any(c.status == "fail" for c in checks)
