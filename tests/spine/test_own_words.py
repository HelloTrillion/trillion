"""SPN-20: the owner's own words as the yes. The pure check, exhaustively; the matcher;
what the registry allows; and exactly which tools opt in.

`own_words` takes no I/O, so every combination of what the session can say about a
turn is tried here against an oracle written from the spec's three conditions."""

from __future__ import annotations

import dataclasses
import itertools
import re
from pathlib import Path
from typing import Any, get_args

import pytest

from trillion.spine.confirm import (
    MIN_OWN_CHARS,
    MIN_OWN_WORDS,
    Refusal,
    Rejection,
    own_words,
    own_words_eligible,
    said_words,
    verbatim,
)
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Risk,
    Speaker,
    ToolContext,
    ToolDefinition,
    Via,
)
from trillion.spine.registry import RegistryError, ToolRegistry, validate

SRC = Path(__file__).resolve().parents[2] / "src" / "trillion"

SAID = "keep a rule: never book anything before nine, please"
RULE = "never book anything before nine"


def definition(**kw: Any) -> ToolDefinition:
    base = ToolDefinition(
        name="keep_words",
        description="Keep some words (test).",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}, "id": {"type": "string"}},
            "additionalProperties": False,
        },
        risk=Risk.IRREVERSIBLE,
        category="test",
        timeout_s=5.0,
        recoverable=True,
        own_words=("text",),
    )
    return dataclasses.replace(base, **kw)


def ctx(**kw: Any) -> ToolContext:
    base = ToolContext(
        owner_id="local",
        session_id="s1",
        turn=2,
        call_id="c1",
        via="voice",
        speaker="owner",
        heard=SAID,
    )
    return dataclasses.replace(base, **kw)


ELIGIBLE = definition()
# Every way a tool can fail to be one whose words may approve it.
NOT_ELIGIBLE = {
    "no_opt_in": definition(own_words=()),
    "spend": definition(risk=Risk.SPEND),
    "write": definition(risk=Risk.WRITE),
    "read": definition(risk=Risk.READ),
    "unrecoverable": definition(recoverable=False),
    "spend_unrecoverable": definition(risk=Risk.SPEND, recoverable=False),
}


# ── The oracle: the spec's conditions, written independently of the code ────────


def expected(
    d: ToolDefinition,
    args: dict[str, Any],
    c: ToolContext,
    enabled: bool,
) -> Refusal | None:
    if not enabled:
        return Refusal.OWN_WORDS_OFF
    spoken = c.via == "voice" and c.heard is not None and not c.host_turn
    if not spoken:
        return Refusal.NOT_SPOKEN
    if c.speaker != "owner":
        return Refusal.NOT_OWNER
    if c.helper_id is not None or c.routine_target is not None:
        return Refusal.NOT_ELIGIBLE
    if c.read_outside:
        return Refusal.READ_OUTSIDE
    if not (d.own_words and d.risk is Risk.IRREVERSIBLE and d.recoverable):
        return Refusal.NOT_ELIGIBLE
    assert c.heard is not None
    if set(args) != set(d.own_words):
        return Refusal.NOT_VERBATIM
    if not all(isinstance(args[n], str) and RULE_IN(args[n], c.heard) for n in d.own_words):
        return Refusal.NOT_VERBATIM
    return None


def RULE_IN(arg: str, heard: str) -> bool:
    """The spec's matcher, written a second way: as a regular expression over the
    normalised text, with word boundaries made of single spaces."""
    a, h = " ".join(said_words(arg)), " ".join(said_words(heard))
    if len(a.split()) < 2 or len(a) < 8:
        return False
    return re.search(rf"(?:^| ){re.escape(a)}(?: |$)", h) is not None


VIAS: tuple[Via, ...] = get_args(Via)
SPEAKERS: tuple[Speaker | None, ...] = (*get_args(Speaker), None)
HEARD = (SAID, None, "", "keep a rule never book anything")
ARGS: tuple[dict[str, Any], ...] = (
    {"text": RULE},
    {"text": "Never book anything before nine."},
    {"text": "never book anything before 9"},
    {"text": RULE, "id": "r1"},
    {"id": "r1"},
    {},
    {"text": 9},
)
DEFINITIONS = (ELIGIBLE, *NOT_ELIGIBLE.values())


def test_every_combination_matches_the_spec() -> None:
    n = 0
    for (
        d,
        args,
        enabled,
        via,
        speaker,
        heard,
        host_turn,
        helper,
        routine,
        outside,
    ) in itertools.product(
        DEFINITIONS,
        ARGS,
        (True, False),
        VIAS,
        SPEAKERS,
        HEARD,
        (False, True),
        (None, "h1"),
        (None, "s1"),
        (False, True),
    ):
        c = ctx(
            via=via,
            speaker=speaker,
            heard=heard,
            host_turn=host_turn,
            helper_id=helper,
            routine_target=routine,
            read_outside=outside,
        )
        got = own_words(d, args, c, enabled)
        want = expected(d, args, c, enabled)
        assert (got.reason if got is not None else None) is want, (d, args, c, enabled)
        n += 1
    assert n > 100_000  # every case, not a sample


def test_the_one_way_through() -> None:
    assert own_words(ELIGIBLE, {"text": RULE}, ctx(), True) is None


def test_off_is_off_whatever_else_holds() -> None:
    r = own_words(ELIGIBLE, {"text": RULE}, ctx(), False)
    assert isinstance(r, Rejection) and r.reason is Refusal.OWN_WORDS_OFF


@pytest.mark.parametrize("via", [v for v in VIAS if v != "voice"])
def test_only_a_spoken_turn(via: Via) -> None:
    # Typed, a tap, a channel message, a widget's ask, a timed ask, a refresh, a routine.
    r = own_words(ELIGIBLE, {"text": RULE}, ctx(via=via), True)
    assert r is not None and r.reason is Refusal.NOT_SPOKEN


def test_a_spoken_turn_with_no_transcript_or_a_host_turn() -> None:
    assert own_words(ELIGIBLE, {"text": RULE}, ctx(heard=None), True) == Rejection(
        Refusal.NOT_SPOKEN
    )
    assert own_words(ELIGIBLE, {"text": RULE}, ctx(host_turn=True), True) == Rejection(
        Refusal.NOT_SPOKEN
    )


@pytest.mark.parametrize("speaker", ["unsure", "other", "replay", None])
def test_only_the_enrolled_owners_voice(speaker: Speaker | None) -> None:
    # None is nobody enrolled: the path never opens without a voiceprint.
    r = own_words(ELIGIBLE, {"text": RULE}, ctx(speaker=speaker), True)
    assert r is not None and r.reason is Refusal.NOT_OWNER


def test_a_helpers_chat_or_a_routine_never() -> None:
    for c in (ctx(helper_id="h1"), ctx(routine_target="s1")):
        r = own_words(ELIGIBLE, {"text": RULE}, c, True)
        assert r is not None and r.reason is Refusal.NOT_ELIGIBLE


def test_after_reading_anything_from_outside() -> None:
    r = own_words(ELIGIBLE, {"text": RULE}, ctx(read_outside=True), True)
    assert r is not None and r.reason is Refusal.READ_OUTSIDE


@pytest.mark.parametrize("kind", sorted(NOT_ELIGIBLE))
def test_each_tool_that_may_not(kind: str) -> None:
    d = NOT_ELIGIBLE[kind]
    assert not own_words_eligible(d)
    r = own_words(d, {"text": RULE}, ctx(), True)
    assert r is not None and r.reason is Refusal.NOT_ELIGIBLE


def test_an_id_or_a_missing_argument_keeps_the_card() -> None:
    for args in ({"text": RULE, "id": "r1"}, {"id": "r1"}, {}):
        r = own_words(ELIGIBLE, args, ctx(), True)
        assert r is not None and r.reason is Refusal.NOT_VERBATIM


def test_every_listed_argument_must_match() -> None:
    d = definition(
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}, "about": {"type": "string"}},
            "additionalProperties": False,
        },
        own_words=("text", "about"),
    )
    heard = "keep a rule never book anything before nine about the early mornings"
    both = {"text": RULE, "about": "the early mornings"}
    assert own_words(d, both, ctx(heard=heard), True) is None
    one_wrong = {"text": RULE, "about": "the late evenings"}
    r = own_words(d, one_wrong, ctx(heard=heard), True)
    assert r is not None and r.reason is Refusal.NOT_VERBATIM


def test_the_reasons_are_never_said() -> None:
    # They are codes for the record; she is told nothing, and the card appears.
    for r in (
        Refusal.OWN_WORDS_OFF,
        Refusal.NOT_SPOKEN,
        Refusal.READ_OUTSIDE,
        Refusal.NOT_ELIGIBLE,
        Refusal.NOT_VERBATIM,
    ):
        assert r.value == r.value.lower() and " " not in r.value


# ── The matcher ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("arg", "heard", "match"),
    [
        (RULE, SAID, True),
        # Case and punctuation do not matter, on either side.
        ("Never Book Anything Before Nine!", SAID, True),
        (RULE, "KEEP A RULE: NEVER, BOOK... ANYTHING; BEFORE (NINE)", True),
        # Curly quotes and apostrophes are straight ones.
        ("don't book before nine", "keep this: don\u2019t book before nine", True),
        ("don\u2019t book before nine", "keep this: don't book before nine", True),
        ("never book anything", "say \u201cnever book anything\u201d", True),
        # Quotation marks are not part of a word.
        ("'never book' anything", "never book anything", True),
        # Fullwidth and compatibility forms fold (NFKC).
        ("never book anything", "\uff4eever book anything", True),
        # On word boundaries only.
        ("ever book anything", "never book anything", False),
        ("never book anything before nin", SAID, False),
        ("book anything before nine", "rebook anything before nine", False),
        # Numbers are not rewritten.
        ("never book anything before 9", SAID, False),
        ("never book anything before nine", "never book anything before 9", False),
        # One extra word in the middle breaks the run.
        ("never book anything before nine", "never book just anything before nine", False),
        # A word missing breaks it too.
        ("never book anything before nine", "never book before nine", False),
        # Words out of order.
        ("before nine never book", SAID, False),
        # Too short: one word, or under eight characters.
        ("nine", SAID, False),
        ("before", "before", False),
        ("a rule", SAID, False),
        ("", SAID, False),
        ("...", SAID, False),
        # Exactly at the limits.
        ("book it", "please book it", False),  # 7 characters
        ("book this", "please book this now", True),  # 9 characters, 2 words
        # Nothing heard.
        (RULE, "", False),
        # An underscore is not a letter.
        ("snake case rule", "snake_case rule", True),
    ],
)
def test_word_for_word(arg: str, heard: str, match: bool) -> None:
    assert verbatim(arg, heard) is match
    assert RULE_IN(arg, heard) is match  # the oracle agrees


def test_the_limits_are_the_spec() -> None:
    assert (MIN_OWN_WORDS, MIN_OWN_CHARS) == (2, 8)


def test_said_words() -> None:
    assert said_words("  Don\u2019t\u2014ever   use \u2018emojis\u2019!  ") == (
        "don't",
        "ever",
        "use",
        "emojis",
    )
    assert said_words("") == ()


# ── The registry holds the line ───────────────────────────────────────────


class WordsTool(BaseTool):
    def __init__(self, d: ToolDefinition) -> None:
        self.definition = d

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text="Keep the words")


def test_a_first_party_tool_may_opt_in() -> None:
    validate(WordsTool(ELIGIBLE), "trillion.tools.rules")


@pytest.mark.parametrize(
    "module",
    [
        "trillion.connectors.google",
        "trillion.connectors.slack",
        "trillion.connectors.mcp",
        "trillion.skills",
        "some_plugin",
        "trillion.toolsmith",  # not the package, only a name like it
    ],
)
def test_nothing_but_first_party(module: str) -> None:
    with pytest.raises(RegistryError, match="first-party"):
        validate(WordsTool(ELIGIBLE), module)
    with pytest.raises(RegistryError, match="first-party"):
        ToolRegistry().register(WordsTool(ELIGIBLE), module=module)


@pytest.mark.parametrize("risk", [Risk.SPEND, Risk.WRITE, Risk.READ])
def test_only_irreversible(risk: Risk) -> None:
    with pytest.raises(RegistryError, match="irreversible"):
        validate(WordsTool(definition(risk=risk)), "trillion.tools.x")


def test_only_recoverable() -> None:
    with pytest.raises(RegistryError, match="recoverable"):
        validate(WordsTool(definition(recoverable=False)), "trillion.tools.x")


def test_only_its_own_arguments() -> None:
    with pytest.raises(RegistryError, match="not in the schema"):
        validate(WordsTool(definition(own_words=("words",))), "trillion.tools.x")
    with pytest.raises(RegistryError, match="twice"):
        validate(WordsTool(definition(own_words=("text", "text"))), "trillion.tools.x")


def test_a_redefinition_cannot_opt_in() -> None:
    reg = ToolRegistry()
    plain = definition(own_words=())
    reg.register(WordsTool(plain), module="trillion.tools.x")
    with pytest.raises(RegistryError):
        reg.redefine(plain.name, definition())
    d = reg.definition(plain.name)
    assert d is not None and d.own_words == ()


def test_a_redefinition_cannot_drop_the_argument_it_names() -> None:
    reg = ToolRegistry()
    reg.register(WordsTool(ELIGIBLE), module="trillion.tools.x")
    shrunk = dataclasses.replace(
        ELIGIBLE,
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    with pytest.raises(RegistryError, match="not in the schema"):
        reg.redefine(ELIGIBLE.name, shrunk)


# ── Exactly these tools opt in ────────────────────────────────────────────

OPTED_IN = {
    "rule_keep": ("text",),
    "rule_drop": ("about",),
    "memory_forget": ("about",),
}


def test_exactly_these_tools_opt_in() -> None:
    reg = ToolRegistry.discover()
    found = {d.name: d.own_words for d in reg.definitions() if d.own_words}
    assert found == OPTED_IN
    for name in OPTED_IN:
        d = reg.definition(name)
        assert d is not None and own_words_eligible(d)


def test_no_other_source_sets_own_words() -> None:
    # Adding a tool to the list is a reviewed change to this test, never a side effect.
    setters = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if re.search(r"\bown_words\s*=\s*\(", p.read_text(encoding="utf-8"))
    )
    assert setters == ["tools/memory.py", "tools/rules.py"]


def test_sending_spending_and_connectors_never_have_it() -> None:
    from trillion.connectors.google import tools as google
    from trillion.connectors.slack import tools as slack

    classes = [c for group in google.TOOLS.values() for c in group] + list(slack.TOOLS)
    assert any(c.__name__ == "GmailSend" for c in classes)
    for c in classes:
        d = getattr(c, "definition", None)
        if isinstance(d, ToolDefinition):
            assert d.own_words == (), c.__name__
    for d in ToolRegistry.discover().definitions():
        if d.risk is Risk.SPEND or not d.recoverable:
            assert d.own_words == (), d.name
