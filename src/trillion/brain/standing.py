"""Standing rules: what the user told her to always or never do (BRN-5).

They live in one plain file, `rules.md` in the data folder, and reach the model in
the system block on every turn. Nothing that shortens a conversation can touch
them: summarizing old turns rewrites the history, and the rules are not in it.

A rule enters only through the `rule_keep` tool, which asks the user first, or by
the user editing the file. Each rule is one line, and its id is a hash of its text,
so a hand-written bullet needs no id and the same rule is never kept twice.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from trillion.memory.present import inert

MAX_RULES = 20
MAX_RULE_CHARS = 300
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_HEADER = (
    "# Standing rules\n\n"
    "What you told your agent to always or never do. She reads every line on every\n"
    "turn, however long ago you said it. Add or remove a line here and restart, or\n"
    "ask her in the app. One rule per line, starting with a dash.\n\n"
)


class RuleError(ValueError):
    """`kind` is safe to show: empty, too_long, full, not_found, disk."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, kw_only=True)
class Rule:
    id: str
    text: str


def rule_id(text: str) -> str:
    return "rule_" + hashlib.sha256(text.casefold().encode("utf-8")).hexdigest()[:12]


def clean(text: str) -> str:
    """One line, nothing in it that reads as markup, within the limit."""
    line = inert(text).strip()
    if not line:
        raise RuleError("empty")
    if len(line) > MAX_RULE_CHARS:
        raise RuleError("too_long")
    return line


def parse(text: str) -> list[Rule]:
    out: dict[str, Rule] = {}
    for line in text.splitlines():
        m = _BULLET.match(line)
        if not m:
            continue
        try:
            said = clean(m.group(1))
        except RuleError:
            continue  # a line too long to keep whole is not kept in part
        out.setdefault(rule_id(said), Rule(id=rule_id(said), text=said))
    return list(out.values())[:MAX_RULES]


def render(rules: list[Rule]) -> str:
    return _HEADER + "".join(f"- {r.text}\n" for r in rules)


class StandingRules:
    """The rules file, read at start and rewritten whole on every change."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._rules: list[Rule] = []
        self.reload()

    def reload(self) -> None:
        try:
            self._rules = parse(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._rules = []

    def all(self) -> list[Rule]:
        return list(self._rules)

    def get(self, rid: str) -> Rule | None:
        return next((r for r in self._rules if r.id == rid), None)

    def find(self, text: str) -> Rule | None:
        try:
            return self.get(rule_id(clean(text)))
        except RuleError:
            return None

    def keep(self, text: str) -> Rule:
        said = clean(text)
        found = self.get(rule_id(said))
        if found is not None:
            return found
        if len(self._rules) >= MAX_RULES:
            raise RuleError("full")
        rule = Rule(id=rule_id(said), text=said)
        self._write([*self._rules, rule])
        return rule

    def drop(self, rid: str) -> Rule:
        rule = self.get(rid)
        if rule is None:
            raise RuleError("not_found")
        self._write([r for r in self._rules if r.id != rid])
        return rule

    def _write(self, rules: list[Rule]) -> None:
        """All of the new file or all of the old one, read back to confirm (OPS-2)."""
        text = render(rules)
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self._path)
            if self._path.read_text(encoding="utf-8") != text:
                raise RuleError("disk")
        except OSError as e:
            raise RuleError("disk") from e
        finally:
            temporary.unlink(missing_ok=True)
        self._rules = rules
