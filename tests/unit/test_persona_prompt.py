from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trillion.persona.prompt import (
    DEFAULT_AGENT_NAME,
    DEFAULT_USER_NAME,
    Identity,
    PendingNote,
    load_identity,
    load_soul,
    system_blocks,
    volatile_context,
)


def test_defaults_when_onboarding_has_not_run(tmp_path: Path) -> None:
    assert load_identity(tmp_path) == Identity(
        agent_name=DEFAULT_AGENT_NAME, user_name=DEFAULT_USER_NAME
    )


def test_names_come_from_the_identity_files(tmp_path: Path) -> None:
    (tmp_path / "IDENTITY.md").write_text("---\nagent_name: Nova\n---\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text('---\nuser_name: "Sam"\ntimezone: UTC\n---\n', "utf-8")
    assert load_identity(tmp_path) == Identity(agent_name="Nova", user_name="Sam")


def test_a_name_cannot_smuggle_a_prompt(tmp_path: Path) -> None:
    hostile = "Nova{user_name}" + "x" * 200
    (tmp_path / "IDENTITY.md").write_text(f"---\nagent_name: {hostile}\n---\n", encoding="utf-8")
    name = load_identity(tmp_path).agent_name
    assert "{" not in name and len(name) <= 40


def test_fields_outside_front_matter_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "IDENTITY.md").write_text("agent_name: Mallory\n", encoding="utf-8")
    assert load_identity(tmp_path).agent_name == DEFAULT_AGENT_NAME


def test_the_users_soul_wins_over_the_shipped_one(tmp_path: Path) -> None:
    assert "{agent_name}" in load_soul(tmp_path)  # the shipped default
    (tmp_path / "SOUL.md").write_text("You are {agent_name}, edited.", encoding="utf-8")
    assert load_soul(tmp_path) == "You are {agent_name}, edited."


def test_the_stable_block_uses_the_chosen_names_and_no_placeholders(tmp_path: Path) -> None:
    identity = Identity(agent_name="Nova", user_name="Sam")
    shared = Path("/shared/notes")
    (block,) = system_blocks(identity, load_soul(tmp_path), (shared,))
    assert block.cache
    assert "You are Nova" in block.text and "Sam" in block.text
    assert "{agent_name}" not in block.text and "{user_name}" not in block.text
    assert "<!--" not in block.text  # maintainer comments never reach the model
    assert "confirmation_required" in block.text  # the rules
    assert str(shared) in block.text  # in this platform's own spelling
    # PER-1a: a user who named her Nova never hears the brand as her name.
    assert "Trillion" not in block.text


def test_the_stable_block_is_identical_between_turns(tmp_path: Path) -> None:
    identity, soul = Identity(), load_soul(tmp_path)
    assert system_blocks(identity, soul) == system_blocks(identity, soul)
    assert "(none" in system_blocks(identity, soul)[0].text


def test_a_folder_name_is_never_treated_as_a_placeholder() -> None:
    folder = Path("/x/{agent_name}")
    (block,) = system_blocks(Identity(agent_name="Nova"), "soul", (folder,))
    assert str(folder) in block.text


def test_volatile_context_carries_the_time_and_what_is_waiting() -> None:
    now = datetime(2026, 10, 7, 9, 30, tzinfo=UTC)
    text = volatile_context(now, [PendingNote(pending_id="p1", summary="Create a.txt")])
    assert text.startswith("<context>") and text.endswith("</context>")
    assert "Wednesday 2026-10-07 09:30 UTC" in text
    assert "p1: Create a.txt" in text


def test_a_spoken_message_says_so_and_a_typed_one_does_not() -> None:
    now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    who = Identity(agent_name="Nova", user_name="Sam")
    spoken = volatile_context(now, [], spoken=who)
    assert "THIS MESSAGE WAS SPOKEN" in spoken and "transcript of Sam's voice" in spoken
    assert "may be misheard" in spoken and "No markdown" in spoken
    assert spoken.startswith("<context>") and spoken.endswith("</context>")
    assert "SPOKEN" not in volatile_context(now, [])


def test_skills_that_are_on_are_listed_and_capped() -> None:
    ident = Identity(agent_name="Nova", user_name="Sam")
    none = system_blocks(ident, "soul")[0].text
    assert "skill_open" not in none
    one = system_blocks(ident, "soul", skills=[("email-replies", "Draft replies.")])[0].text
    assert "- email-replies: Draft replies." in one and "skill_open" in one
    many = [(f"s{i}", "d" * 200) for i in range(80)]
    text = system_blocks(ident, "soul", skills=many)[0].text
    listed = [line for line in text.splitlines() if line.startswith("- s")]
    assert len(listed) <= 50 and sum(len(line) for line in listed) <= 6000
    assert f"{80 - len(listed)} more skills are turned on" in text


def test_the_skills_list_is_identical_between_turns() -> None:
    ident = Identity(agent_name="Nova", user_name="Sam")
    skills = [("a", "first"), ("b", "second")]
    assert system_blocks(ident, "soul", skills=skills) == system_blocks(
        ident, "soul", skills=skills
    )


def test_a_skill_description_is_never_treated_as_a_placeholder() -> None:
    ident = Identity(agent_name="Nova", user_name="Sam")
    text = system_blocks(ident, "soul", skills=[("x", "Write as {user_name}.")])[0].text
    assert "- x: Write as {user_name}." in text


def test_the_time_carries_the_users_zone_by_name() -> None:
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 24, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    text = volatile_context(now, [], zone="America/New_York")
    assert "now: Saturday 2026-10-24 16:00 EDT (America/New_York)" in text
    # With no zone known, the line is as it always was.
    assert "now: Saturday 2026-10-24 16:00 EDT\n" in volatile_context(now, [])


def test_a_timed_ask_is_told_the_user_is_away() -> None:
    from trillion.persona.prompt import scheduled

    text = scheduled(Identity(agent_name="Nova", user_name="Sam"))
    assert "Sam asked for this earlier" in text and "{user_name}" not in text
    assert "say plainly what you would do" in text


def test_a_zone_whose_abbreviation_is_its_name_is_said_once() -> None:
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 24, 16, 0, tzinfo=ZoneInfo("UTC"))
    text = volatile_context(now, [], zone="UTC")
    assert "now: Saturday 2026-10-24 16:00 UTC\n" in text and "(UTC)" not in text


def test_she_is_told_a_reminder_is_a_schedule_never_a_memory() -> None:
    text = system_blocks(Identity(user_name="Sam"), "soul")[0].text
    assert "call reminder_set; never save a reminder as a memory" in text
    assert "ask_schedule" in text and "YYYY-MM-DDTHH:MM" in text
