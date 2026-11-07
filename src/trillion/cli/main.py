"""Entry point for the `trillion` command: `run`, `setup`, `doctor`, `import` and `update`."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import webbrowser
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import Any

from trillion import __version__

DEV_ORIGIN = "http://127.0.0.1:5174"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trillion")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="start Trillion on this machine and open the app")
    setup = sub.add_parser(
        "setup", help="prepare this machine, check it, then start Trillion (safe to run again)"
    )
    for starts in (run, setup):
        starts.add_argument("--port", type=int, default=7777)
        starts.add_argument(
            "--grant",
            action="append",
            default=[],
            type=Path,
            metavar="DIR",
            help="a folder the file tools may use; repeat for more. None by default.",
        )
        starts.add_argument("--no-open", action="store_true", help="do not open a browser")
        starts.add_argument(
            "--dev", action="store_true", help=f"also accept the Vite dev server at {DEV_ORIGIN}"
        )
    setup.add_argument(
        "--no-run", action="store_true", help="prepare and check, but do not start the app"
    )
    doctor = sub.add_parser("doctor", help="check this machine and say how to fix what is wrong")
    doctor.add_argument("--port", type=int, default=7777, help="the port you start Trillion on")
    doctor.add_argument(
        "--secrets",
        action="store_true",
        help="also test every saved key, without ever printing one",
    )
    doctor.add_argument(
        "--trace",
        nargs="?",
        const=10,
        type=int,
        metavar="N",
        help="show how long her last N turns took, and where the time went (10 if no N)",
    )
    for checks in (doctor, setup):
        checks.add_argument(
            "--offline", action="store_true", help="skip the checks that need the network"
        )
    imports = sub.add_parser(
        "import",
        help="bring names, memories and skills over from OpenClaw or Hermes (CON-8)",
        description="Shows what would be imported. Nothing changes without --apply. "
        "The other app's SOUL.md is never imported.",
    )
    imports.add_argument("app", choices=["openclaw", "hermes"])
    imports.add_argument(
        "--from",
        dest="source",
        type=Path,
        metavar="DIR",
        help="the app's folder, if not where it usually is",
    )
    imports.add_argument("--apply", action="store_true", help="import; without it, a dry run")
    imports.add_argument(
        "--replace-profile",
        action="store_true",
        help="replace a name or timezone you already set, without asking",
    )
    update = sub.add_parser(
        "update",
        help="install the newest Trillion on your channel; --check only looks (ONB-8)",
        description="Asks GitHub for the newest release on your channel (stable, beta or "
        "dev), only when you run this. Nothing checks for updates on its own.",
    )
    update.add_argument(
        "--check", action="store_true", help="only say whether a newer version is out"
    )
    update.add_argument(
        "--channel",
        choices=["stable", "beta", "dev"],
        help="use this channel instead of the one you installed from; "
        "remembered once an update with it succeeds",
    )
    args = parser.parse_args(argv)
    if args.version:
        sys.stdout.write(f"trillion {__version__}\n")
        return 0
    if args.command == "run":
        return _until_stopped(_run(args))
    if args.command == "doctor":
        return asyncio.run(_doctor(args))
    if args.command == "setup":
        return _until_stopped(_setup(args))
    if args.command == "import":
        return _import(args)
    if args.command == "update":
        from trillion.cli import update as updates

        return updates.run(check_only=args.check, channel=args.channel)
    parser.print_help()
    return 0


def _until_stopped(work: Coroutine[Any, Any, int]) -> int:
    """Run until done. Ctrl+C before the server is up stops it without a traceback."""
    try:
        return asyncio.run(work)
    except KeyboardInterrupt:
        sys.stdout.write("\nStopped.\n")
        return 130


async def _examine(args: argparse.Namespace, *, secrets: bool) -> tuple[str, bool]:
    """Run the checks and return what to print and whether nothing is broken."""
    import keyring

    from trillion.api.app import HOST, web_dir
    from trillion.cli import doctor
    from trillion.secrets.store import KeychainStore

    store = KeychainStore()
    checks = await doctor.examine(
        doctor.Options(port=args.port, host=HOST, offline=args.offline, secrets=secrets),
        secrets=store,
        page=web_dir(),
        backend=keyring.get_keyring().name if store.available() else "",
    )
    return doctor.render(checks), doctor.healthy(checks)


async def _doctor(args: argparse.Namespace) -> int:
    if args.trace is not None:
        return _trace(args)
    report, healthy = await _examine(args, secrets=args.secrets)
    sys.stdout.write(report)
    return 0 if healthy else 1


def _trace(args: argparse.Namespace) -> int:
    """How long the last turns took (ONB-11). Nothing contacted, nothing changed."""
    from trillion import paths
    from trillion.api.app import HOST, web_dir
    from trillion.cli import doctor, trace
    from trillion.storage import inspect

    if args.trace < 1:
        sys.stderr.write("trillion: --trace needs a number of turns, 1 or more\n")
        return 2
    running = doctor.check_running(HOST, web_dir())
    if running is not None:
        sys.stdout.write(doctor.render([running]).rsplit("\n\n", 1)[0] + "\n\n")
    rows = inspect.timings(paths.db_path(), doctor.OWNER, args.trace)
    sys.stdout.write(trace.report(rows))
    return 0


def _import(args: argparse.Namespace) -> int:
    from trillion.cli import importer

    return asyncio.run(
        importer.run(args.app, args.source, do=args.apply, replace_profile=args.replace_profile)
    )


async def _prepare() -> list[str]:
    """Everything local and deterministic that a first start needs (ONB-2a).

    No credential and no network. Each step is safe to repeat, so a setup that was
    interrupted is finished by running it again."""
    from trillion import paths
    from trillion.storage.db import connect
    from trillion.storage.migrate import migrate

    done: list[str] = []
    home = paths.home()
    for folder in (home, paths.persona_dir(), paths.memory_dir(), paths.models_dir()):
        if not folder.is_dir():
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            done.append(f"made {folder}")
    conn = await connect(paths.db_path())
    try:
        applied = await migrate(conn)
    finally:
        await conn.close()
    if applied:
        s = "" if len(applied) == 1 else "s"
        done.append(f"database ready ({len(applied)} update{s} applied)")
    return done


def _meaning_wanted() -> bool:
    """Its runtime is installed and its model is not here yet."""
    from trillion import paths
    from trillion.memory import meaning

    return meaning.state(paths.models_dir()) == "no_model"


async def _fetch_meaning() -> str:
    """The model for recall by meaning (MEM-2), fetched once and checked (ONB-7). A
    failure is said, never fatal: memory works by its words until setup runs again."""
    from trillion import paths
    from trillion.memory import meaning

    mb = round(meaning.PACK.size / (1 << 20))
    sys.stdout.write(f"setup  fetching the meaning model ({mb} MB)...\n")
    sys.stdout.flush()
    try:
        await meaning.fetch(paths.models_dir())
    except meaning.MeaningError as e:
        return f"meaning model not fetched ({e.kind}): memory is found by its words for now"
    return f"fetched the meaning model ({mb} MB)"


async def _setup(args: argparse.Namespace) -> int:
    for folder in args.grant:
        if not folder.expanduser().is_dir():
            sys.stderr.write(f"trillion: --grant {folder}: not a folder\n")
            return 2
    try:
        done = await _prepare()
    except OSError as e:
        sys.stderr.write(f"trillion: the data folder could not be prepared: {e.strerror}\n")
        return 1
    fetch = not args.offline and _meaning_wanted()
    for line in done or ([] if fetch else ["already prepared: nothing to do"]):
        sys.stdout.write(f"setup  {line}\n")
    if fetch:
        sys.stdout.write(f"setup  {await _fetch_meaning()}\n")
    sys.stdout.write("\n")
    report, healthy = await _examine(args, secrets=False)
    sys.stdout.write(report)
    if not healthy:
        sys.stdout.write("Fix the lines marked fail, then run: trillion setup\n")
        return 1
    if args.no_run:
        sys.stdout.write("Start it with: trillion run\n")
        return 0
    sys.stdout.write("\n")
    sys.stdout.flush()
    return await _run(args)


def _folders_line(roots: tuple[Path, ...], narrower: dict[Path, str]) -> str:
    """The folders she reaches, and any `--grant` that a saved share holds back."""
    shown = [f"{r} ({narrower[r]})" if r in narrower else str(r) for r in roots]
    shown += [f"{p} ({why})" for p, why in narrower.items() if p not in roots]
    return ", ".join(shown) or "none"


async def _run(args: argparse.Namespace) -> int:
    from trillion.secrets import redact

    # Scrub from the first record, before anything opens. The session token is known
    # once the app hands it over or the runtime makes one.
    session: dict[str, str | None] = {"token": None}
    undo = redact.install(lambda: _secrets_in_use(session["token"]))
    try:
        return await _serve(args, session)
    finally:
        undo()


async def _serve(args: argparse.Namespace, session: dict[str, str | None]) -> int:
    # Imported here so `trillion --version` stays instant.
    import uvicorn

    from trillion.api.app import HOST, MAX_FRAME_BYTES, create_app, local_hosts, web_dir
    from trillion.api.runtime import (
        SESSION_TOKEN_ENV,
        SessionTokenError,
        open_runtime,
        session_token,
    )
    from trillion.brain.config import ConfigError, provider_from_env
    from trillion.cli.server import Server, stop_when_closed
    from trillion.files.shares import EVERYONE, ShareError, Shares

    # A `--grant` folder is a share for this run only, under the same rule as any other
    # share (the shared folders spec, D4, D10): refused before anything is opened.
    shares = Shares()
    # A grant that matched a folder already saved keeps the saved share's access and
    # who it is for: the folders line says so.
    narrower: dict[Path, str] = {}
    for folder in args.grant:
        if not folder.expanduser().is_dir():
            sys.stderr.write(f"trillion: --grant {folder}: not a folder\n")
            return 2
        try:
            # Relative to where it was started, as the shell gave it.
            share = shares.add_run_only(folder.expanduser().resolve())
        except ShareError as e:
            sys.stderr.write(f"trillion: --grant {folder}: {e}\n")
            return 2
        if share.scope != EVERYONE:
            narrower[share.path] = "saved for one routine only"
        elif share.access == "read":
            narrower[share.path] = "saved as read only"
    # Taken out of the environment at once, so no tool or MCP server she starts inherits it.
    app_token = os.environ.pop(SESSION_TOKEN_ENV, None)
    session["token"] = app_token
    if app_token is not None:
        try:
            session_token({SESSION_TOKEN_ENV: app_token})  # a bad one fails before anything opens
        except SessionTokenError as e:
            sys.stderr.write(f"trillion: {e}\n")
            return 2
    # The desktop app made this token and shows the page itself: its token is never printed.
    from_app = app_token is not None
    try:
        provider_from_env()  # fail on a half-made configuration before opening anything
    except ConfigError as e:
        sys.stderr.write(f"trillion: {e}\n")
        return 2

    if not _port_is_free(HOST, args.port):
        sys.stderr.write(
            f"trillion: port {args.port} is already in use on this machine."
            f" Stop what is using it, or pick another: trillion run --port {args.port + 1}\n"
        )
        return 2

    from trillion import build

    rt = await open_runtime(shares=shares, session=app_token)
    session["token"] = rt.token
    provider = rt.provider
    try:
        app = create_app(
            rt,
            hosts=local_hosts(args.port),
            extra_origins=frozenset({DEV_ORIGIN}) if args.dev else frozenset(),
            static=web_dir(),
        )
        server = Server(
            uvicorn.Config(
                app,
                host=HOST,
                port=args.port,
                log_level="warning",
                access_log=False,
                ws_max_size=MAX_FRAME_BYTES,
            )
        )
        # The token rides in the fragment: a browser never sends that part to a server
        # or to another site, and the page removes it from the address bar on load.
        base = DEV_ORIGIN if args.dev else f"http://{HOST}:{args.port}"
        url = f"{base}/#token={rt.token}"
        open_line = "started by the desktop app" if from_app else url
        model = (
            f"{provider.name} ({provider.model})"
            if provider
            else "not set up yet (the app will ask)"
        )
        grants = _folders_line(rt.granted_roots, narrower)
        sys.stdout.write(
            f"Trillion is running on this machine only.\n"
            f"  open:    {open_line}\n"
            f"  model:   {model}\n"
            f"  folders: {grants}\n"
            f"Press Ctrl+C to stop.\n"
        )
        sys.stdout.flush()
        if not args.no_open and not from_app:
            webbrowser.open(url)
        try:
            # So `trillion doctor` can tell whether this is the build on disk (ONB-11).
            build.announce(args.port, web_dir())
        except OSError as e:
            sys.stderr.write(f"trillion: could not note the running build: {e.strerror}\n")
        # No stdin (launchd, a detached run) means no app holding the other end.
        if from_app and sys.stdin is not None:
            stop_when_closed(sys.stdin.buffer, server)
        await server.serve()
    finally:
        build.withdraw()
        await rt.close()
    sys.stdout.write("Trillion stopped.\n")
    return 0


def _secrets_in_use(token: str | None) -> list[str]:
    """Every credential this process can currently send, for the log scrubber.

    The store notes each value it holds for every connector, so a value fetched
    lazily is known from the moment it is in memory.
    """
    from trillion.secrets.redact import env_secrets
    from trillion.secrets.store import handed_out

    env = dict(os.environ)  # a snapshot: another thread may change it meanwhile
    return [v for v in (*handed_out(), *env_secrets(env), token) if v]


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "posix":
            # Lets the probe past connections a stopped server left in TIME_WAIT, as
            # the server's own socket gets past them; a live listener still refuses.
            # Not on Windows, where the same option binds over a port in use.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
