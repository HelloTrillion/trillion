"""Which Google app Trillion signs in as, and which accounts the user connected.

Google lets an app ask people to sign in only through an OAuth client registered
in a Google Cloud project. Trillion has no client of its own built in; it uses, in
this order:

1. `TRILLION_GOOGLE_CLIENT_ID` and `TRILLION_GOOGLE_CLIENT_SECRET` in the environment;
2. a client the user saved in Settings (kept in the keychain);
3. `google_client.json` shipped next to this file by a distribution of Trillion.

For an installed app Google's "client secret" is not a secret in the usual sense
(Google says so), but it is still kept in the keychain, never in a file of ours.

The accounts and which services each has are kept in `google.json` in Trillion's
folder: email addresses and choices only. Each account's sign-in is in the keychain.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from trillion.secrets.store import SecretStore

Service = Literal["gmail", "calendar", "drive"]
SERVICES: tuple[Service, ...] = ("gmail", "calendar", "drive")
TITLES: Mapping[Service, str] = {
    "gmail": "Gmail",
    "calendar": "Google Calendar",
    "drive": "Google Drive",
}

# Only what each service's tools need. `openid email` tells Trillion which account
# signed in.
IDENTITY = ("openid", "email")
SCOPES: Mapping[Service, tuple[str, ...]] = {
    "gmail": (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    ),
    "calendar": (
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ),
    # Only the files the user picks in Google's file picker: a non-sensitive scope, so
    # it needs no verification, and she never sees the rest of their Drive.
    "drive": ("https://www.googleapis.com/auth/drive.file",),
}
# The Google Cloud APIs a self-made client has to switch on, by service.
APIS: Mapping[Service, tuple[str, str]] = {
    "gmail": ("Gmail API", "gmail.googleapis.com"),
    "calendar": ("Google Calendar API", "calendar-json.googleapis.com"),
    "drive": ("Google Drive API", "drive.googleapis.com"),
}
PICKER_API = ("Google Picker API", "picker.googleapis.com")

CLIENT_KEY = "google/client"
PICKER_KEY = "google/picker-key"
ENV_PICKER = "TRILLION_GOOGLE_PICKER_KEY"
ENV_ID = "TRILLION_GOOGLE_CLIENT_ID"
ENV_SECRET = "TRILLION_GOOGLE_CLIENT_SECRET"  # noqa: S105 - the name of a variable
BUNDLED = Path(__file__).with_name("google_client.json")
_CLIENT_ID = re.compile(r"^[0-9]+-[0-9a-z]+\.apps\.googleusercontent\.com$")


class GoogleError(ValueError):
    """Something about the Google setup is wrong. The message is safe to show."""


@dataclass(frozen=True, kw_only=True)
class Client:
    id: str
    secret: str
    source: Literal["environment", "saved", "bundled"]
    # A browser API key for Google's file picker (Drive). Public by design: it is
    # restricted to the Picker API and identifies the project, nothing more.
    picker_key: str = ""

    @property
    def project_number(self) -> str:
        """Google's app id for the picker: the number a client ID starts with."""
        return self.id.split("-", 1)[0]


def check_client(client_id: str, secret: str) -> tuple[str, str]:
    client_id, secret = client_id.strip(), secret.strip()
    if not _CLIENT_ID.match(client_id):
        raise GoogleError(
            "that is not a Google OAuth client ID (it ends in .apps.googleusercontent.com)"
        )
    if not secret or len(secret) > 200 or any(c.isspace() for c in secret):
        raise GoogleError("the client secret is missing or not valid")
    return client_id, secret


def client(secrets: SecretStore, env: Mapping[str, str] | None = None) -> Client | None:
    """The OAuth client to sign in with, or None if there is none yet."""
    env = os.environ if env is None else env
    saved_key = secrets.get(PICKER_KEY) or ""
    if env.get(ENV_ID) and env.get(ENV_SECRET):
        return Client(
            id=env[ENV_ID],
            secret=env[ENV_SECRET],
            source="environment",
            picker_key=env.get(ENV_PICKER, "") or saved_key,
        )
    saved = secrets.get(CLIENT_KEY)
    if saved:
        with contextlib.suppress(ValueError, KeyError, TypeError):
            data = json.loads(saved)
            return Client(
                id=data["id"], secret=data["secret"], source="saved", picker_key=saved_key
            )
    if BUNDLED.is_file():
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            data = json.loads(BUNDLED.read_text(encoding="utf-8"))
            installed = data.get("installed", data)
            return Client(
                id=installed["client_id"],
                secret=installed["client_secret"],
                source="bundled",
                picker_key=str(data.get("picker_key", "")) or saved_key,
            )
    return None


def save_client(secrets: SecretStore, client_id: str, secret: str) -> None:
    client_id, secret = check_client(client_id, secret)
    secrets.set(CLIENT_KEY, json.dumps({"id": client_id, "secret": secret}))


def save_picker_key(secrets: SecretStore, key: str) -> None:
    key = key.strip()
    if not re.fullmatch(r"AIza[0-9A-Za-z_-]{35}", key):
        raise GoogleError("that is not a Google API key (it starts with AIza)")
    secrets.set(PICKER_KEY, key)


def read_client_file(text: str) -> tuple[str, str]:
    """The ID and secret from the JSON file Google offers to download for a client."""
    try:
        data = json.loads(text)
    except ValueError:
        raise GoogleError("that is not the JSON file Google gave you") from None
    inner = data.get("installed") or data.get("web") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        raise GoogleError('that file has no "installed" client in it')
    return check_client(str(inner.get("client_id", "")), str(inner.get("client_secret", "")))


# ── The accounts ─────────────────────────────────────────────────────────


def account_key(email: str) -> str:
    return f"google/account/{email.lower()}"


@dataclass(kw_only=True)
class Account:
    email: str
    services: list[Service] = field(default_factory=list)


@dataclass(kw_only=True)
class Saved:
    """What `google.json` holds: the accounts, and which tools may run without asking.
    Only tools that change nothing anyone else sees can be given that."""

    accounts: list[Account] = field(default_factory=list)
    free: set[str] = field(default_factory=set)
    # Drive files the user picked for her, by account: id and name, for Settings.
    files: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def find(self, email: str) -> Account | None:
        return next((a for a in self.accounts if a.email.lower() == email.lower()), None)

    def with_service(self, service: Service) -> list[str]:
        return [a.email for a in self.accounts if service in a.services]


def load(path: Path) -> Saved:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Saved()
    if not isinstance(raw, dict):
        return Saved()
    accounts: list[Account] = []
    for a in raw.get("accounts", []):
        if isinstance(a, dict) and isinstance(a.get("email"), str):
            services = [s for s in a.get("services", []) if s in SERVICES]
            accounts.append(Account(email=a["email"], services=services))
    free = {str(t) for t in raw.get("free", []) if isinstance(t, str)}
    files: dict[str, list[dict[str, str]]] = {}
    found = raw.get("files")
    if isinstance(found, dict):
        for email, items in found.items():
            if isinstance(items, list):
                files[str(email)] = [
                    {"id": str(f.get("id", "")), "name": str(f.get("name", ""))}
                    for f in items
                    if isinstance(f, dict) and f.get("id")
                ]
    return Saved(accounts=accounts, free=free, files=files)


def save(path: Path, saved: Saved) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "accounts": [{"email": a.email, "services": list(a.services)} for a in saved.accounts],
        "free": sorted(saved.free),
        "files": saved.files,
    }
    fd, tmp = tempfile.mkstemp(prefix=".google-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
