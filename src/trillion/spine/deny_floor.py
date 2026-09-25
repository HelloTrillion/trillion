"""SPN-4: a pure floor under every path a tool touches, on the normal AND the confirmed path.

Case-insensitive (macOS volumes usually are), symlinks resolved before any
comparison, and fail-closed on anything that is not a usable path string.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trillion import paths
from trillion.spine.contract import JSON, ToolDefinition

PROTECTED_SEGMENTS: frozenset[str] = frozenset({".git", ".ssh", ".aws", ".gnupg"})


@dataclass(frozen=True)
class Denial:
    arg: str
    reason: str


@dataclass(frozen=True)
class DenyPolicy:
    protected_roots: tuple[Path, ...]

    @classmethod
    def default(cls) -> DenyPolicy:
        return cls(
            protected_roots=(
                paths.home().expanduser().resolve(),
                paths.package_root(),
                (Path.home() / "Library" / "Keychains").resolve(),
            )
        )


def _lower_parts(p: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in p.parts)


def _is_under(p: Path, root: Path) -> bool:
    rp, rr = _lower_parts(p), _lower_parts(root)
    return rp[: len(rr)] == rr


def _resolve(raw: str) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


def check(
    defn: ToolDefinition, args: JSON, policy: DenyPolicy, granted_roots: tuple[Path, ...]
) -> Denial | None:
    for name in defn.path_args:
        if name not in args:
            continue
        raw = args[name]
        if not isinstance(raw, str) or not raw.strip():
            return Denial(name, "a path must be a non-empty string")
        try:
            p = _resolve(raw)
        except (OSError, RuntimeError, ValueError):
            return Denial(name, "the path could not be resolved")

        lowered = _lower_parts(p)
        hit = PROTECTED_SEGMENTS.intersection(lowered)
        if hit:
            return Denial(name, f"the path touches a protected directory ({sorted(hit)[0]})")
        leaf = lowered[-1] if lowered else ""
        if leaf == ".env" or leaf.startswith(".env."):
            return Denial(name, "environment files are protected")
        for root in policy.protected_roots:
            if _is_under(p, root):
                return Denial(name, "Trillion's own files are protected")
        if not any(_is_under(p, g.expanduser().resolve()) for g in granted_roots):
            return Denial(name, "the path is outside the folders the user granted")
    return None
