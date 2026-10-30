"""Home (SUR-1a): the nine tiles and their states, decided by the core."""

from __future__ import annotations

from typing import Any

from trillion.connectors.mcp.catalog import BY_ID
from trillion.persona.day import DAY_READS, SetUp
from trillion.persona.prompt import Identity
from trillion.workspace.home import TILES, home
from trillion.workspace.samples import SAMPLES

SAM = Identity(agent_name="Nova", user_name="Sam")
NOTHING = SetUp()


def build(
    setup: SetUp = NOTHING,
    configured: dict[str, str] | None = None,
    *,
    google_ready: bool = True,
    briefing_built: bool = False,
    identity: Identity = SAM,
) -> dict[str, Any]:
    return home(
        setup,
        configured or {},
        google_ready=google_ready,
        briefing_built=briefing_built,
        identity=identity,
    )


def tile(out: dict[str, Any], tile_id: str) -> dict[str, Any]:
    (found,) = [t for t in out["tiles"] if t["id"] == tile_id]
    return found


def test_the_nine_tiles_in_order_with_their_text() -> None:
    out = build()
    assert [(t["id"], t["title"], t["what"]) for t in out["tiles"]] == [
        ("next_up", "Next up", "Your next meeting, and when to leave for it"),
        ("free_time", "Free time", "Open time left in your day"),
        ("inbox", "Inbox", "Mail that's waiting on your reply"),
        ("tasks", "Reminders", "Your reminders and timed asks for today"),
        ("mentions", "Mentions", "Where people need you in Slack"),
        ("due_soon", "Due soon", "What's due today and tomorrow"),
        ("kpis", "Revenue", "Yesterday's sales against the day before"),
        ("files", "Recent files", "Docs that changed since yesterday"),
        ("checks", "What's broken", "Failing checks and new errors, only when there are any"),
    ]
    assert all(t["type"] == t["id"] for t in out["tiles"])
    assert [s["id"] for s in tile(out, "due_soon")["services"]] == [
        "linear",
        "todoist",
        "notion",
        "atlassian",
        "clickup",
        "monday",
        "airtable",
    ]
    assert [s["id"] for s in tile(out, "kpis")["services"]] == ["stripe", "paypal", "square"]
    assert [s["id"] for s in tile(out, "checks")["services"]] == [
        "github",
        "sentry",
        "vercel",
        "supabase",
    ]


def test_tile_ids_are_sample_widget_types() -> None:
    types = {s["type"] for s in SAMPLES}
    assert {t.id for t in TILES} <= types


def test_every_service_named_is_in_the_catalogue() -> None:
    for t in TILES:
        for s in t.services:
            assert s in BY_ID, s
    # Every built-in source Your day reads has a tile.
    assert set(DAY_READS) <= {s for t in TILES for s in t.services}


def test_nothing_set_up_leaves_only_reminders() -> None:
    out = build(identity=Identity(agent_name="Nova"))
    assert out["user_name"] == "" and out["agent_name"] == "Nova"
    assert out["briefing"] == "build"
    assert (out["ready"], out["total"]) == (1, 9)
    assert tile(out, "tasks")["state"] == "built_in" and tile(out, "tasks")["services"] == []
    assert {t["state"] for t in out["tiles"] if t["id"] != "tasks"} == {"connect"}
    gmail = tile(out, "inbox")["services"][0]
    assert gmail == {
        "id": "gmail",
        "title": "Gmail",
        "color": BY_ID["gmail"].color,
        "glyph": BY_ID["gmail"].glyph,
        "how": "google",
        "category": "Email and calendar",
        "state": "connect",
        "settings_key": "",
    }


def test_each_state_from_the_setup() -> None:
    setup = SetUp(calendar=True, asks_first=("gmail",))
    configured = {"google-calendar": "google:calendar", "gmail": "google:gmail"}
    out = build(setup, configured, briefing_built=True)
    assert out["briefing"] == "open" and out["user_name"] == "Sam"
    assert tile(out, "next_up")["state"] == "ready"
    assert tile(out, "free_time")["state"] == "ready"
    assert tile(out, "next_up")["services"][0]["settings_key"] == "google:calendar"
    inbox = tile(out, "inbox")
    assert inbox["state"] == "asks_first"
    assert inbox["services"][0]["state"] == "asks_first"
    assert inbox["services"][0]["settings_key"] == "google:gmail"
    assert tile(out, "mentions")["state"] == "connect"
    assert out["ready"] == 3


def test_a_tile_takes_the_best_of_its_services() -> None:
    configured = {"linear": "linear", "notion": "notion-2"}
    out = build(SetUp(catalog=frozenset({"notion"}), asks_first=("linear",)), configured)
    due = tile(out, "due_soon")
    assert due["state"] == "ready"
    states = {s["id"]: (s["state"], s["settings_key"]) for s in due["services"]}
    assert states["linear"] == ("asks_first", "linear")
    assert states["notion"] == ("ready", "notion-2")
    assert states["todoist"] == ("connect", "")
    only_asks = build(SetUp(asks_first=("stripe",)), {"stripe": "stripe"})
    assert tile(only_asks, "kpis")["state"] == "asks_first"


def test_set_up_but_not_known_yet_is_still_to_connect() -> None:
    # A server still starting is configured, so its card can be revealed, but not read.
    out = build(SetUp(), {"github": "github"})
    checks = tile(out, "checks")
    assert checks["state"] == "connect"
    assert checks["services"][0]["settings_key"] == "github"


def test_more_counts_services_past_three() -> None:
    out = build()
    assert tile(out, "due_soon")["more"] == 4
    assert tile(out, "checks")["more"] == 1
    assert tile(out, "kpis")["more"] == 0
    assert tile(out, "inbox")["more"] == 0


def test_the_meter() -> None:
    setup = SetUp(
        calendar=True,
        gmail=True,
        slack=True,
        drive=True,
        catalog=frozenset({"linear", "stripe", "sentry"}),
    )
    out = build(setup)
    assert (out["ready"], out["total"]) == (9, 9)
    assert out["next"] is None


def test_next_is_a_one_click_sign_in_first() -> None:
    out = build()
    # Google's app is set up: Calendar signs in with one click and fills two tiles.
    assert out["next"] == {
        "tile": "next_up",
        "service": "google-calendar",
        "text": "Connect Google Calendar next: one sign-in adds Next up and Free time",
    }


def test_google_is_one_click_only_once_its_app_is_ready() -> None:
    out = build(google_ready=False)
    assert out["next"] == {
        "tile": "mentions",
        "service": "slack",
        "text": "Connect Slack next: one sign-in adds Mentions",
    }


def test_next_skips_a_key_for_a_sign_in() -> None:
    # Only What's broken is left; GitHub needs a key, Sentry signs in.
    setup = SetUp(
        calendar=True,
        gmail=True,
        slack=True,
        drive=True,
        catalog=frozenset({"linear", "stripe"}),
    )
    out = build(setup)
    assert out["next"] == {
        "tile": "checks",
        "service": "sentry",
        "text": "Connect Sentry next: one sign-in adds What's broken",
    }


def test_next_falls_back_to_the_first_locked_tile() -> None:
    setup = SetUp(slack=True, catalog=frozenset({"linear", "stripe", "sentry"}))
    out = build(setup, google_ready=False)
    assert out["next"] == {
        "tile": "next_up",
        "service": "google-calendar",
        "text": "Connect Google Calendar next to add Next up and Free time",
    }


def test_nothing_to_connect_when_the_rest_only_ask_first() -> None:
    setup = SetUp(
        calendar=True,
        slack=True,
        drive=True,
        catalog=frozenset({"linear", "stripe", "sentry"}),
        asks_first=("gmail",),
    )
    out = build(setup, {"gmail": "google:gmail"})
    assert out["ready"] == 8 and out["next"] is None
