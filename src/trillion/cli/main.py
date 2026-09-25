"""Entry point for the `trillion` command. Subcommands arrive with their tracks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from trillion import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trillion")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)
    if args.version:
        sys.stdout.write(f"trillion {__version__}\n")
        return 0
    parser.print_help()
    return 0


def entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
