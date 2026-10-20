"""The privileged setup path: the only code that writes the identity files (PER-2, PER-6).

`USER.md` and `IDENTITY.md` live in the user data directory, outside every folder
a tool can reach. Onboarding and Settings call this, with the session token, from
the app's own page. No tool, skill or agent does: nothing here is registered with
the tool registry, and the model never sees these functions.
"""

from __future__ import annotations

import difflib
import os
import re
import tempfile
import zoneinfo
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, get_args

from trillion import paths
from trillion.persona.prompt import DEFAULT_AGENT_NAME, NAME_LIMIT, fields

Directness = Literal["blunt", "balanced", "gentle"]
DIRECTNESS: tuple[Directness, ...] = get_args(Directness)
DEFAULT_DIRECTNESS: Directness = "balanced"

# An IANA zone name as a browser reports it: Area/City, sometimes Area/Region/City.
_ZONE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]{0,31}(/[A-Za-z0-9_+-]{1,32}){0,2}$")


class SetupError(ValueError):
    """What was offered cannot be written. `code` is safe to show; it carries no input.
    `suggestion`, when there is one, is a name taken from the zone list, never the input."""

    def __init__(self, code: str, suggestion: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.suggestion = suggestion


@cache
def _zones() -> dict[str, str]:
    """Every IANA zone this machine knows, by lower-case name. Empty when the machine has
    no zone data at all (Windows needs the `tzdata` package): then nothing can be checked."""
    return {z.lower(): z for z in zoneinfo.available_timezones()}


def unknown_zone(zone: str) -> SetupError | None:
    """None if `zone` is a real IANA zone (or cannot be checked), else the refusal, with
    the closest real zone as its suggestion when one is near enough to be useful."""
    known = _zones()
    if not known or zone in known.values():
        return None
    close = difflib.get_close_matches(zone.lower(), known, n=1, cutoff=0.7)
    return SetupError(
        "timezone_unknown", known.get(zone.lower()) or (known[close[0]] if close else None)
    )


@dataclass(frozen=True, kw_only=True)
class Profile:
    user_name: str
    agent_name: str = DEFAULT_AGENT_NAME
    timezone: str = ""
    directness: Directness = DEFAULT_DIRECTNESS


def clean_name(raw: str) -> str:
    """A name exactly as it may be stored, or a SetupError. It goes into every prompt
    and into a `key: value` line, so it is one line, without braces, of a sane length."""
    name = " ".join(raw.split())
    if not name:
        raise SetupError("name_empty")
    if len(name) > NAME_LIMIT:
        raise SetupError("name_too_long")
    if any(not ch.isprintable() or ch in "{}<>" for ch in name) or name[0] in "\"'-#":
        raise SetupError("name_not_allowed")
    return name


def clean(profile: Profile) -> Profile:
    zone = profile.timezone.strip()
    if zone and not _ZONE.match(zone):
        raise SetupError("timezone_not_allowed")
    if zone and (unknown := unknown_zone(zone)):
        raise unknown
    if profile.directness not in DIRECTNESS:
        raise SetupError("directness_not_allowed")
    return Profile(
        user_name=clean_name(profile.user_name),
        agent_name=clean_name(profile.agent_name),
        timezone=zone,
        directness=profile.directness,
    )


def load(directory: Path | None = None) -> Profile | None:
    """What onboarding wrote, or None if it has not happened. A hand edit that no
    longer passes the rules is treated as not done rather than half trusted."""
    d = directory or paths.persona_dir()
    user, identity = fields(d / "USER.md"), fields(d / "IDENTITY.md")
    if "user_name" not in user:
        return None
    raw = user.get("directness", "")
    directness = next((d for d in DIRECTNESS if d == raw), DEFAULT_DIRECTNESS)
    try:
        return clean(
            Profile(
                user_name=user["user_name"].strip("\"'"),
                agent_name=identity.get("agent_name", DEFAULT_AGENT_NAME).strip("\"'"),
                timezone=user.get("timezone", ""),
                directness=directness,
            )
        )
    except SetupError:
        return None


def _front_matter(pairs: dict[str, str], note: str) -> str:
    lines = [f"{key}: {value}" for key, value in pairs.items() if value]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + note + "\n"


def _replace(path: Path, text: str) -> None:
    """All of the new file or all of the old one, never half of either."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(profile: Profile, directory: Path | None = None) -> Profile:
    """Write both files and return what was written."""
    p = clean(profile)
    d = directory or paths.persona_dir()
    d.mkdir(parents=True, exist_ok=True)
    _replace(
        d / "USER.md",
        _front_matter(
            {"user_name": p.user_name, "timezone": p.timezone, "directness": p.directness},
            "Written when you introduced yourself in the app. Change it there, or edit the "
            "lines above and restart.",
        ),
    )
    _replace(
        d / "IDENTITY.md",
        _front_matter(
            {"agent_name": p.agent_name},
            "The name you gave your agent. Change it in the app, or edit the line above "
            "and restart.",
        ),
    )
    return p
