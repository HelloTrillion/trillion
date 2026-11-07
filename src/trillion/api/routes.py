"""Routers that plug in: a module under `trillion.api` that defines `ROUTES` is mounted.

A new feature's HTTP routes live in its own module and end with

    ROUTES = (create_my_feature_router,)

and nothing else changes: `app.py` is not edited, so two features added at once don't
collide there. Modules are mounted in name order. The routers that predate this are
still listed by hand in `app.py`; `scripts/check_plug_in.py` keeps that list from growing.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime

RouterFactory = Callable[["Runtime"], APIRouter]


def plugged(rt: Runtime, package: str = "trillion.api") -> list[APIRouter]:
    """Every router a module in `package` offers through `ROUTES`, in module-name order."""
    found: list[APIRouter] = []
    pkg = importlib.import_module(package)
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{package}.{info.name}")
        factories: Sequence[RouterFactory] = getattr(module, "ROUTES", ())
        found.extend(factory(rt) for factory in factories)
    return found
