"""Where Trillion keeps its state. One module, so the deny floor and storage agree."""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "TRILLION_HOME"


def home() -> Path:
    raw = os.environ.get(ENV_HOME)
    return Path(raw).expanduser() if raw else Path.home() / ".trillion"


def db_path() -> Path:
    return home() / "trillion.db"


def persona_dir() -> Path:
    return home() / "persona"


def secrets_path() -> Path:
    return home() / "secrets.age"


def models_dir() -> Path:
    return home() / "models"


def package_root() -> Path:
    """The installed `trillion` package: tools may never write here."""
    return Path(__file__).resolve().parent
