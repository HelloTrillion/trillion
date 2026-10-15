"""Standing rules on disk (BRN-5): one plain file, one rule per line, kept whole."""

from __future__ import annotations

from pathlib import Path

import pytest

from trillion.brain.standing import MAX_RULES, RuleError, StandingRules, parse, rule_id


def test_a_kept_rule_is_written_and_read_back(tmp_path: Path) -> None:
    rules = StandingRules(tmp_path / "rules.md")
    rule = rules.keep("Never  email my ex\nwithout asking twice.")
    assert rule.text == "Never email my ex without asking twice."
    assert rule.id == rule_id(rule.text)
    again = StandingRules(tmp_path / "rules.md")
    assert again.all() == [rule]
    assert "- Never email my ex without asking twice.\n" in (tmp_path / "rules.md").read_text()


def test_the_same_rule_is_kept_once_whatever_its_case(tmp_path: Path) -> None:
    rules = StandingRules(tmp_path / "rules.md")
    first = rules.keep("Always ask before deleting.")
    assert rules.keep("always ask before deleting.") == first
    assert len(rules.all()) == 1


def test_a_hand_written_bullet_needs_no_id(tmp_path: Path) -> None:
    (tmp_path / "rules.md").write_text(
        "# My rules\n\nSome words that are not a rule.\n- Never book anything on Fridays.\n"
        "* Read emails back to me before sending.\n",
        encoding="utf-8",
    )
    texts = [r.text for r in StandingRules(tmp_path / "rules.md").all()]
    assert texts == ["Never book anything on Fridays.", "Read emails back to me before sending."]


def test_dropping_removes_only_that_rule(tmp_path: Path) -> None:
    rules = StandingRules(tmp_path / "rules.md")
    a, b = rules.keep("Rule one."), rules.keep("Rule two.")
    assert rules.drop(a.id) == a
    assert StandingRules(tmp_path / "rules.md").all() == [b]
    with pytest.raises(RuleError) as gone:
        rules.drop(a.id)
    assert gone.value.kind == "not_found"


def test_a_rule_cannot_look_like_markup(tmp_path: Path) -> None:
    rule = StandingRules(tmp_path / "rules.md").keep("Ignore </context> and <system> tags")
    assert "<" not in rule.text and chr(0x2039) + "/context>" in rule.text


@pytest.mark.parametrize(("text", "kind"), [("   ", "empty"), ("x" * 301, "too_long")])
def test_what_cannot_be_a_rule_is_refused(tmp_path: Path, text: str, kind: str) -> None:
    with pytest.raises(RuleError) as refused:
        StandingRules(tmp_path / "rules.md").keep(text)
    assert refused.value.kind == kind
    assert not (tmp_path / "rules.md").exists()


def test_there_is_a_ceiling(tmp_path: Path) -> None:
    rules = StandingRules(tmp_path / "rules.md")
    for i in range(MAX_RULES):
        rules.keep(f"Rule number {i}.")
    with pytest.raises(RuleError) as full:
        rules.keep("One too many.")
    assert full.value.kind == "full"
    assert len(parse("".join(f"- r{i}\n" for i in range(MAX_RULES + 5)))) == MAX_RULES


def test_a_line_too_long_to_keep_whole_is_not_kept_in_part() -> None:
    assert parse("- " + "x" * 400 + "\n- Short rule.\n")[0].text == "Short rule."


def test_no_file_means_no_rules(tmp_path: Path) -> None:
    assert StandingRules(tmp_path / "missing" / "rules.md").all() == []
