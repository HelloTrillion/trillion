"""Entry point for the `trillion` command: `run`, `setup` and `doctor`."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import webbrowser
from collections.abc import Sequence
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
    for checks in (doctor, setup):
        checks.add_argument(
            "--offline", action="store_true", help="skip the checks that need the network"
        )
    args = parser.parse_args(argv)
    if args.version:
        sys.stdout.write(f"trillion {__version__}\n")
        return 0
    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "doctor":
        return asyncio.run(_doctor(args))
    if args.command == "setup":
        return asyncio.run(_setup(args))
    parser.print_help()
    return 0


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
    report, healthy = await _examine(args, secrets=args.secrets)
    sys.stdout.write(report)
    return 0 if healthy else 1


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
    for line in done or ["already prepared: nothing to do"]:
        sys.stdout.write(f"setup  {line}\n")
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


async def _run(args: argparse.Namespace) -> int:
    # Imported here so `trillion --version` stays instant.
    import uvicorn

    from trillion.api.app import HOST, MAX_FRAME_BYTES, create_app, local_hosts, web_dir
    from trillion.api.runtime import open_runtime
    from trillion.brain.config import ConfigError, provider_from_env
    from trillion.secrets import redact

    for folder in args.grant:
        if not folder.expanduser().is_dir():
            sys.stderr.write(f"trillion: --grant {folder}: not a folder\n")
            return 2
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

    rt = await open_runtime(granted_roots=tuple(args.grant))
    redact.install(lambda: _secrets_in_use(rt))
    provider = rt.provider
    try:
        app = create_app(
            rt,
            hosts=local_hosts(args.port),
            extra_origins=frozenset({DEV_ORIGIN}) if args.dev else frozenset(),
            static=web_dir(),
        )
        server = uvicorn.Server(
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
        model = (
            f"{provider.name} ({provider.model})"
            if provider
            else "not set up yet (the app will ask)"
        )
        grants = ", ".join(str(r) for r in rt.granted_roots) or "none"
        sys.stdout.write(
            f"Trillion is running on this machine only.\n"
            f"  open:    {url}\n"
            f"  model:   {model}\n"
            f"  folders: {grants}\n"
            f"Press Ctrl+C to stop.\n"
        )
        sys.stdout.flush()
        if not args.no_open:
            webbrowser.open(url)
        await server.serve()
    finally:
        await rt.close()
    return 0


def _secrets_in_use(rt: Any) -> list[str]:
    """Every credential this process can currently send, for the log scrubber."""
    from trillion.brain.config import ANTHROPIC_KEY, KINDS, OPENAI_KEY, secret_name
    from trillion.voice.catalog import LISTENING, SPEECH

    names = [secret_name(k) for k in KINDS]
    names += [o.secret for o in SPEECH if o.needs_key]
    names += [o.secret for o in LISTENING if o.needs_key]
    stored = [rt.secrets.get(n) for n in names]
    from_env = [os.environ.get(ANTHROPIC_KEY), os.environ.get(OPENAI_KEY)]
    return [v for v in (*stored, *from_env, rt.token) if v]


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
