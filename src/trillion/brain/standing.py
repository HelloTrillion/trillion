"""Standing rules: what the user told her to always or never do (BRN-5).

They live in one plain file, `rules.md` in the data folder, and reach the model in
the system block on every turn. Nothing that shortens a conversation can touch
them: summarizing old turns rewrites the history, and the rules are not in it.

A rule enters through the `rule_keep` tool, which asks the user first; through
`rule_never`, for a rule in the user's own words that only stops her doing
something (`narrows` decides, and anything it is unsure of asks first); or by the
user editing the file. Each rule is one line, and its id is a hash of its text,
so a hand-written bullet needs no id and the same rule is never kept twice.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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


# How a rule that only stops her doing something opens, once "please" and the like
# are taken off. Anything else is not plainly a "never", so it asks first.
_OPENERS = ("never", "don't ever", "do not ever", "dont ever", "don't", "do not", "dont", "stop")
_LEAD = re.compile(r"^(?:(?:please|from now on|and|also|ok|okay)\b[\s,:]*)+")
_LEAD_ANY_CASE = re.compile(_LEAD.pattern, re.IGNORECASE)
_PLAIN = re.compile(r"^[a-z0-9 ,.'-]+$")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
MAX_NEVER_WORDS = 24
# Any one of these and the rule asks first (BRN-5). Each one means the rule might
# loosen a check rather than add one ("never ask me first"), change what reaches the
# user ("never tell me about the alarm"), touch what only a card may decide (deleting,
# money, sending, secrets, safety), or point at something the rule does not name
# ("never do that again"). The list is deliberately wide: a rule caught here is not
# lost, it is kept the way every rule was kept before, on a card.
_ASKS_FIRST = frozenset(
    # Loosening a check, or turning a "never" round.
    "not no nor never nothing none nobody without unless except until only but however "
    "instead rather otherwise if when whenever always may can could might allow allowed "
    "allowing let letting permit permitted permission free auto automatic automatically "
    "yourself own ask asking asked asks confirm confirming "
    "confirmation check checking checked approve approving approved approval consent "
    "verify verifying double first wait waiting pause pausing prompt hesitate hold skip "
    "skipping refuse refusing decline declining deny block blocking prevent avoid stop "
    "stopping quit cease fail failing forget forgetting miss missing neglect ignore "
    "ignoring bypass override careful carefully cautious caution strict strictly guess "
    "question questioning questions argue arguing challenge doubt object protest back "
    # What reaches the user, and how she speaks to them.
    "tell telling told notify notifying notification notifications alert alerts alerting "
    "alarm alarms warn warning warnings remind reminding reminder reminders mention "
    "mentioning show showing inform informing report reporting reports update updates "
    "bother bothering interrupt interrupting disturb ping wake brief briefing summary "
    "summaries call calling calls text texts texting message messages messaging email "
    "emails emailing mail send sending sent post posting reply replies replying respond "
    "responding say saying speak speaking talk talking discuss bring raise share sharing "
    # What only a card decides.
    "delete deleting deletion remove removing erase erasing trash wipe destroy discard "
    "overwrite pay paying payment payments money spend spending buy buying purchase order "
    "orders transfer trade trading sell selling invest bank password passwords key keys "
    "secret secrets token tokens login credential credentials account accounts firewall "
    "network publish upload emergency smoke fire gas leak water health medical medicine "
    "doctor hospital police safety safe lock locks unlock door doors keep keeping log "
    "logs logging record records recording history save saving store backup backups "
    # The rules and the cards themselves.
    "rule rules card cards gate spine guard guardrail limit limits setting settings "
    "instructions persona soul identity "
    # Pointing at something the rule does not name.
    "it that this them those these again".split()
)
_ASKS_FIRST_PHRASES = ("let me know", "on your own", "go ahead", "make sure", "read back")
_MINE = {"me": "you", "my": "your", "mine": "yours", "myself": "yourself", "i": "you"}


def _plain(text: str) -> str:
    said = " ".join(text.replace("\u2019", "'").casefold().split()).strip(" .!")
    return _LEAD.sub("", said)


def _opener(said: str) -> str | None:
    return next((o for o in _OPENERS if said == o or said.startswith(o + " ")), None)


def narrows(text: str) -> bool:
    """Whether a rule, as said, only stops her doing something: kept at once if so.

    It fails safe. A rule stands without a card only if it opens with a plain "never",
    "don't" or "stop", is one short plain sentence, and has no word that could loosen
    a check, change what reaches the user, or touch what only a card decides. Anything
    else returns False and is kept the way it always was, on a card the user approves."""
    said = _plain(text)
    opener = _opener(said)
    if opener is None or not _PLAIN.match(said):
        return False
    rest = said[len(opener) :].strip(" ,.")
    words = _WORD.findall(rest)
    if not words or len(words) > MAX_NEVER_WORDS:
        return False
    if any(w in _ASKS_FIRST or w.endswith("n't") for w in words):
        return False
    padded = f" {' '.join(words)} "
    return not any(f" {p} " in padded for p in _ASKS_FIRST_PHRASES)


def _yours(word: str) -> str:
    core = word.rstrip(",")
    return _MINE.get(core.casefold(), core) + word[len(core) :]


def read_back(text: str) -> str:
    """Her one sentence for a rule just kept: "Got it: I'll never use emojis."

    The rule in the user's own words and case, turned to face them ("me" becomes "you")."""
    said = _LEAD_ANY_CASE.sub("", " ".join(text.replace("\u2019", "'").split()).strip(" .!"))
    opener = _opener(said.lower()) or ""
    words = [_yours(w) for w in said[len(opener) :].strip(" ,.").split()]
    verb = "stop" if opener == "stop" else "never"
    return f"Got it: I'll {verb} {' '.join(words)}."


# Words in "drop the rule about X" that say nothing about which rule.
_NOT_ABOUT = frozenset(
    "a an the my me i you your rule rules one about on of to for in that this drop "
    "remove forget delete stop following standing please".split()
)


def about_words(about: str) -> list[str]:
    """The words of "the rule about X" worth matching, without a plural's "s"."""
    found = [w for w in _WORD.findall(_plain(about)) if w not in _NOT_ABOUT]
    return list(dict.fromkeys(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in found))


class RuleError(ValueError):
    """`kind` is safe to show: empty, too_long, full, not_found, disk."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, kw_only=True)
class Rule:
    id: str
    text: str


@dataclass(frozen=True, kw_only=True)
class RuleEvent:
    """One line of the rules' permanent history: a rule kept or dropped, and how."""

    rule_id: str
    text: str
    event: Literal["kept", "dropped"]
    # "said": kept at once in the user's words, because it only narrows what she does.
    # "card": the user approved it on a card.
    how: Literal["said", "card"]
    session_id: str
    at: int


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

    def about(self, about: str) -> list[Rule]:
        """The rules that hold every word of "the rule about X", oldest first."""
        words = about_words(about)
        if not words:
            return []
        return [r for r in self._rules if all(w in r.text.casefold() for w in words)]

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
