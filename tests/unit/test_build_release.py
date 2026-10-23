"""scripts/build_release.py: what it ships of our Google app, and what it refuses."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_release.py"
CLIENT_ID = "123456789012-abc123def456.apps.googleusercontent.com"
SECRET = "GOCSPX-" + "x" * 28  # built from pieces: never a real secret
PICKER = "AIza" + "B" * 35


@pytest.fixture(scope="module")
def release() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_release"] = module
    spec.loader.exec_module(module)
    return module


def downloaded(**installed: object) -> dict[str, object]:
    """The file Google offers to download for a Desktop app client."""
    return {
        "installed": {
            "client_id": CLIENT_ID,
            "project_id": "some-project",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": SECRET,
            "redirect_uris": ["http://localhost"],
            **installed,
        }
    }


def test_only_what_sign_in_needs_is_shipped(release: ModuleType) -> None:
    shipped = json.loads(release.google_file(json.dumps({**downloaded(), "picker_key": PICKER})))
    assert shipped == {
        "installed": {"client_id": CLIENT_ID, "client_secret": SECRET},
        "picker_key": PICKER,
    }


def test_the_picker_key_is_optional(release: ModuleType) -> None:
    assert "picker_key" not in json.loads(release.google_file(json.dumps(downloaded())))


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"web": downloaded()["installed"]}),  # a web client, not a Desktop app
        json.dumps(downloaded(client_id="not-a-client-id")),
        json.dumps(downloaded(client_secret="")),
        json.dumps(downloaded(client_secret="has a space")),  # noqa: S106 - a fake
        json.dumps({**downloaded(), "picker_key": "not-a-key"}),
    ],
)
def test_a_wrong_secret_is_refused_without_repeating_it(release: ModuleType, text: str) -> None:
    with pytest.raises(release.ReleaseError) as caught:
        release.google_file(text)
    assert SECRET not in str(caught.value) and "has a space" not in str(caught.value)


@pytest.mark.parametrize(
    "name",
    [
        "trillion/.env",
        "trillion/.env.local",
        "trillion/data/trillion.db",
        "trillion/x.sqlite3",
        "trillion/persona/USER.md",
        "trillion/persona/IDENTITY.md",
    ],
)
def test_private_files_are_never_in_a_wheel(release: ModuleType, name: str) -> None:
    assert release.NEVER.search(name)


@pytest.mark.parametrize(
    "name",
    [
        "trillion/persona/default/SOUL.md",
        "trillion/api/static/index.html",
        "trillion/storage/db.py",
    ],
)
def test_ordinary_files_are_allowed(release: ModuleType, name: str) -> None:
    assert not release.NEVER.search(name)
