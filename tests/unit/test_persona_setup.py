"""The privileged setup path: what onboarding may write, and what it refuses (PER-2, PER-6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trillion.persona import setup
from trillion.persona.prompt import Identity, load_identity, system_blocks
from trillion.persona.setup import Profile, SetupError
from trillion.spine.registry import ToolRegistry


def test_nothing_is_set_up_until_the_user_says_who_they_are(tmp_path: Path) -> None:
    assert setup.load(tmp_path) is None
    (tmp_path / "IDENTITY.md").write_text("---\nagent_name: Nova\n---\n", encoding="utf-8")
    assert setup.load(tmp_path) is None  # her name alone is not an introduction


def test_what_is_saved_is_what_is_read_back(tmp_path: Path) -> None:
    wrote = setup.save(
        Profile(
            user_name="  Sam   Okafor ",
            agent_name="Nova",
            timezone="America/Chicago",
            directness="gentle",
        ),
        tmp_path / "persona",  # made if it is not there
    )
    assert wrote.user_name == "Sam Okafor"
    assert setup.load(tmp_path / "persona") == wrote
    assert load_identity(tmp_path / "persona") == Identity(
        agent_name="Nova", user_name="Sam Okafor", directness="gentle"
    )


def test_the_files_are_plain_and_say_how_to_change_them(tmp_path: Path) -> None:
    setup.save(Profile(user_name="Sam", agent_name="Nova", timezone="Europe/Paris"), tmp_path)
    user = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert user.startswith(
        "---\nuser_name: Sam\ntimezone: Europe/Paris\ndirectness: balanced\n---\n"
    )
    assert "\r" not in user and "restart" in user
    assert (
        (tmp_path / "IDENTITY.md")
        .read_text(encoding="utf-8")
        .startswith("---\nagent_name: Nova\n---\n")
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["IDENTITY.md", "USER.md"]  # no temp left


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("", "name_empty"),
        ("   ", "name_empty"),
        ("x" * 41, "name_too_long"),
        ("Sam{agent_name}", "name_not_allowed"),
        ("Sam\x00", "name_not_allowed"),
        ("<system>", "name_not_allowed"),
        ("-Sam", "name_not_allowed"),
        ('"Sam"', "name_not_allowed"),
    ],
)
def test_a_name_that_could_bend_a_prompt_or_the_file_is_refused(
    tmp_path: Path, name: str, code: str
) -> None:
    for profile in (Profile(user_name=name), Profile(user_name="Sam", agent_name=name)):
        with pytest.raises(SetupError) as refused:
            setup.save(profile, tmp_path)
        assert refused.value.code == code
    assert list(tmp_path.iterdir()) == []  # nothing was written


def test_a_line_break_in_a_name_cannot_add_a_field(tmp_path: Path) -> None:
    # Whitespace collapses first, so what was two lines is stored as one harmless line.
    wrote = setup.save(Profile(user_name="Sam\nagent_name: Mallory"), tmp_path)
    assert wrote.user_name == "Sam agent_name: Mallory"
    assert load_identity(tmp_path).agent_name == "Trillion"


def test_real_names_are_let_through(tmp_path: Path) -> None:
    for name in ("Zoë", "O'Brien", "李雷", "Jean-Luc", "Dr. K"):
        assert setup.save(Profile(user_name=name, agent_name=name), tmp_path).user_name == name


@pytest.mark.parametrize("zone", ["Chicago; rm -rf", "../../etc/passwd", "a" * 80, "UTC\nx: y"])
def test_a_timezone_must_look_like_one(tmp_path: Path, zone: str) -> None:
    with pytest.raises(SetupError) as refused:
        setup.save(Profile(user_name="Sam", timezone=zone), tmp_path)
    assert refused.value.code == "timezone_not_allowed"


def test_an_unknown_directness_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SetupError):
        setup.save(Profile(user_name="Sam", directness="cruel"), tmp_path)  # type: ignore[arg-type]


def test_a_hand_edit_that_breaks_the_rules_reads_as_not_set_up(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text("---\nuser_name: {agent_name}\n---\n", encoding="utf-8")
    assert setup.load(tmp_path) is None


def test_saving_again_replaces_both_files(tmp_path: Path) -> None:
    setup.save(Profile(user_name="Sam", agent_name="Nova", directness="blunt"), tmp_path)
    setup.save(Profile(user_name="Samira", agent_name="Vega"), tmp_path)
    assert load_identity(tmp_path) == Identity(agent_name="Vega", user_name="Samira")


@pytest.mark.parametrize(
    ("directness", "marker"), [("blunt", "blunt"), ("gentle", "gentler"), ("balanced", None)]
)
def test_directness_adjusts_her_and_never_replaces_her(directness: str, marker: str | None) -> None:
    identity = Identity(agent_name="Nova", user_name="Sam", directness=directness)
    text = system_blocks(identity, "You are {agent_name}.")[0].text
    assert text.startswith("You are Nova.")  # the soul still comes first, whole
    assert ("HOW Sam ASKED YOU TO TALK" in text) is (marker is not None)
    if marker:
        assert f"TALK: {marker}" in text


def test_no_tool_can_write_who_she_is() -> None:
    """PER-2: the setup path is not model-facing. No registered tool names it."""
    names = {d.name for d in ToolRegistry.discover().definitions()}
    assert not {n for n in names if "persona" in n or "identity" in n or "onboard" in n}
