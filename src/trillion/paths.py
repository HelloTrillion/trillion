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


def memory_dir() -> Path:
    return home() / "memory"


def audit_key_path() -> Path:
    """The key that makes audit hashes unguessable (#4). Owner-only, never sent anywhere."""
    return home() / "audit.key"


def rules_path() -> Path:
    """The user's standing rules (BRN-5): one plain file they can read and edit."""
    return home() / "rules.md"


def models_dir() -> Path:
    return home() / "models"


def package_root() -> Path:
    """The installed `trillion` package: tools may never write here."""
    return Path(__file__).resolve().parent


def mcp_config_path() -> Path:
    """The MCP servers the user connected (CON-5). Inside Trillion's own folder, so no
    tool can edit it: what runs without asking is the user's say alone."""
    return home() / "mcp.json"


def google_config_path() -> Path:
    """The Google accounts the user connected and their choices (CON-4). Email
    addresses only: each sign-in is in the keychain."""
    return home() / "google.json"


def slack_config_path() -> Path:
    """Which Slack tools may run without asking. Tokens are in the keychain."""
    return home() / "slack.json"


def channels_config_path() -> Path:
    """Who may talk to her on Slack and Telegram (SUR-5). Tokens are in the keychain."""
    return home() / "channels.json"
