"""Entry point for the `trillion` command. Subcommands arrive with their tracks."""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from trillion import __version__

DEV_ORIGIN = "http://127.0.0.1:5174"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trillion")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="start Trillion on this machine and open the app")
    run.add_argument("--port", type=int, default=7777)
    run.add_argument(
        "--grant",
        action="append",
        default=[],
        type=Path,
        metavar="DIR",
        help="a folder the file tools may use; repeat for more. None by default.",
    )
    run.add_argument("--no-open", action="store_true", help="do not open a browser")
    run.add_argument(
        "--dev", action="store_true", help=f"also accept the Vite dev server at {DEV_ORIGIN}"
    )
    args = parser.parse_args(argv)
    if args.version:
        sys.stdout.write(f"trillion {__version__}\n")
        return 0
    if args.command == "run":
        return asyncio.run(_run(args))
    parser.print_help()
    return 0


async def _run(args: argparse.Namespace) -> int:
    # Imported here so `trillion --version` stays instant.
    import uvicorn

    from trillion.api.app import HOST, MAX_FRAME_BYTES, create_app, local_hosts, web_dir
    from trillion.api.runtime import open_runtime
    from trillion.brain.config import KINDS, ConfigError, provider_from_env, secret_name
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
    redact.install(lambda: [v for n in KINDS if (v := rt.secrets.get(secret_name(n)))])
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


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
