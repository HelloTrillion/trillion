from typing import Any

import pytest

from trillion.workspace.blocks import strip_emoji, validate_blocks
from trillion.workspace.kit import BoardError

WHERE = "widget 1 (custom)"

MINIMAL: list[dict[str, Any]] = [
    {"type": "value", "value": "$41.20"},
    {"type": "sparkline", "values": [1, 2.5]},
    {"type": "meter", "value": 2, "max": 5},
    {"type": "time", "at": "2026-10-25T14:30"},
    {
        "type": "chart",
        "kind": "line",
        "x": ["Mon", "Tue"],
        "series": [{"name": "Spend", "values": [1, 2]}],
    },
    {"type": "table", "columns": ["Day", "Spend"], "rows": [["Mon", 3]]},
    {"type": "timeline", "events": [{"title": "Standup", "start": "9:30"}]},
    {"type": "rows", "items": [{"title": "Milk"}]},
    {"type": "person", "name": "Sam"},
    {"type": "people", "names": ["Sam", "Jo"]},
    {"type": "status", "tone": "good", "text": "On track"},
    {"type": "chips", "items": ["Maple Park"]},
    {"type": "text", "text": "One thing."},
    {"type": "quote", "text": "See you there."},
    {"type": "facts", "items": [{"label": "Due", "value": "Fri"}]},
    {
        "type": "columns",
        "columns": [[{"type": "value", "value": "1"}], [{"type": "text", "text": "a"}]],
    },
]

FULL: list[dict[str, Any]] = [
    {
        "type": "value",
        "value": "41",
        "label": "Spend",
        "delta": "+3",
        "hint": "vs May",
        "tone": "up",
    },
    {"type": "sparkline", "values": [1, 2, 3], "tone": "bad"},
    {"type": "meter", "value": 3, "max": 4, "label": "Done", "shape": "ring", "tone": "good"},
    {"type": "time", "at": "2026-10-25T14:30", "end": "2026-10-25T15:00", "label": "Review"},
    {
        "type": "chart",
        "kind": "bar",
        "x": ["Mon"],
        "series": [{"name": "Spend", "values": [1]}],
        "unit": "$",
    },
    {"type": "table", "columns": ["A"], "rows": [["x"], ["y"]], "highlight": 1},
    {
        "type": "timeline",
        "events": [{"title": "Standup", "start": "09:30", "end": "10:00", "meta": "Room 2"}],
    },
    {
        "type": "rows",
        "label": "Tasks",
        "items": [
            {
                "title": "Milk",
                "detail": "2 litres",
                "person": "Alex",
                "status": "attention",
                "when": "2026-10-26",
                "meta": "shop",
                "chip": "home",
                "done": True,
            }
        ],
    },
    {"type": "person", "name": "Sam", "detail": "Maple Park"},
    {"type": "chips", "items": ["a"], "tone": "neutral"},
    {"type": "text", "text": "Warm.", "tone": "attention"},
    {"type": "quote", "text": "Hi.", "by": "Jo", "when": "2026-10-25T14:30:00Z"},
]


def refused(raw: object, where: str = WHERE) -> str:
    with pytest.raises(BoardError) as err:
        validate_blocks(raw, where)
    return str(err.value)


def one(block: dict[str, Any]) -> dict[str, Any]:
    return validate_blocks([block], WHERE)[0]


def test_every_block_type_accepted_with_minimal_fields() -> None:
    out = validate_blocks(MINIMAL[:8], WHERE) + validate_blocks(MINIMAL[8:], WHERE)
    assert [b["type"] for b in out] == [b["type"] for b in MINIMAL]


def test_full_fields_are_kept() -> None:
    for block in FULL:
        got = one(block)
        for key in block:
            assert key in got, (block["type"], key)


def test_timeline_time_is_padded_like_the_kit() -> None:
    assert one(MINIMAL[6])["events"][0]["start"] == "09:30"


def test_unknown_fields_are_dropped() -> None:
    got = one({"type": "status", "tone": "good", "text": "ok", "color": "red", "size": 9})
    assert got == {"type": "status", "tone": "good", "text": "ok"}


def test_strings_are_trimmed_and_capped() -> None:
    got = one({"type": "value", "value": "  41  ", "label": "x" * 100, "hint": "y" * 500})
    assert got["value"] == "41"
    assert len(got["label"]) == 60 and got["label"].endswith("…")
    assert len(got["hint"]) == 400


def test_value_takes_a_number_as_text() -> None:
    assert one({"type": "value", "value": 41.0, "delta": 2.5})["value"] == "41"


def test_type_is_case_insensitive_and_unknown_is_refused() -> None:
    assert one({"type": " Value ", "value": "1"})["type"] == "value"
    assert "unknown type 'gauge'" in refused([{"type": "gauge"}])
    assert "no type" in refused([{"value": 1}])


@pytest.mark.parametrize(("given", "want"), [("good", "good"), ("up", "good"), ("down", "bad")])
def test_tones_and_the_old_names(given: str, want: str) -> None:
    assert one({"type": "status", "tone": given, "text": "x"})["tone"] == want


@pytest.mark.parametrize("tone", ["bad", "attention", "neutral"])
def test_other_tones_pass(tone: str) -> None:
    assert one({"type": "chips", "items": ["a"], "tone": tone})["tone"] == tone


def test_a_bad_tone_names_the_block() -> None:
    msg = refused([{"type": "value", "value": "1"}, {"type": "status", "tone": "red", "text": "x"}])
    assert msg.startswith("widget 1 (custom): block 2 (status)")
    assert "tone" in msg and "'red'" in msg


def test_status_needs_its_tone() -> None:
    assert "tone is required" in refused([{"type": "status", "text": "x"}])


def test_message_names_widget_block_and_problem() -> None:
    msg = refused(
        [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
            {"type": "meter", "value": 1, "max": 0},
        ]
    )
    assert msg.startswith("widget 1 (custom): block 3 (meter): ")
    assert "max must be above 0" in msg and msg.endswith(".")


def test_blocks_must_be_a_list_of_objects() -> None:
    assert "blocks must be a list" in refused({"type": "text"})
    assert "must be an object" in refused(["text"])
    assert "blocks is empty" in refused([])


@pytest.mark.parametrize("n", [1, 61])
def test_sparkline_needs_2_to_60_values(n: int) -> None:
    msg = refused([{"type": "sparkline", "values": [1.0] * n}])
    assert "block 1 (sparkline)" in msg and "values" in msg


def test_sparkline_values_must_be_finite_numbers() -> None:
    assert "not a number" in refused([{"type": "sparkline", "values": [1, "2"]}])
    assert "finite" in refused([{"type": "sparkline", "values": [1, float("inf")]}])


def test_meter_checks() -> None:
    assert "max must be above 0" in refused([{"type": "meter", "value": 1, "max": -2}])
    assert "value is required" in refused([{"type": "meter", "max": 5}])
    assert "shape" in refused([{"type": "meter", "value": 1, "max": 5, "shape": "pie"}])
    # Clamping is for drawing; the value is kept as written.
    assert one({"type": "meter", "value": 9, "max": 5})["value"] == 9


def test_people_chips_and_facts_limits() -> None:
    assert "9 names, 8 at most" in refused([{"type": "people", "names": list("abcdefghi")}])
    assert "7 items, 6 at most" in refused([{"type": "chips", "items": list("abcdefg")}])
    facts = [{"label": "a", "value": "b"}] * 11
    assert "11 items, 10 at most" in refused([{"type": "facts", "items": facts}])
    assert len(one({"type": "people", "names": list("abcdefgh")})["names"]) == 8


def test_quote_and_text_limits() -> None:
    assert "600" in refused([{"type": "quote", "text": "q" * 601}])
    assert len(one({"type": "quote", "text": "q" * 600})["text"]) == 600
    assert "2000" in refused([{"type": "text", "text": "t" * 2001}])
    assert len(one({"type": "text", "text": "t" * 2000})["text"]) == 2000


def test_text_keeps_its_lines() -> None:
    assert one({"type": "text", "text": "  a\n- b\n**c**  "})["text"] == "a\n- b\n**c**"


def test_columns_limits() -> None:
    col = [{"type": "text", "text": "a"}]
    assert "needs at least 2 columns" in refused([{"type": "columns", "columns": [col]}])
    assert "5 columns, 4 at most" in refused([{"type": "columns", "columns": [col] * 5}])
    assert len(one({"type": "columns", "columns": [col] * 4})["columns"]) == 4
    assert "column 2 must be a list" in refused([{"type": "columns", "columns": [col, "x"]}])


def test_columns_inside_columns_refused() -> None:
    col = [{"type": "text", "text": "a"}]
    inner = {"type": "columns", "columns": [col, col]}
    msg = refused([{"type": "columns", "columns": [[inner], col]}])
    assert "block 1 (columns)" in msg and "column 1" in msg
    assert "columns cannot hold columns" in msg


def test_block_errors_inside_a_column_are_located() -> None:
    bad = [{"type": "meter", "value": 1, "max": 0}]
    msg = refused([{"type": "text", "text": "a"}, {"type": "columns", "columns": [bad, bad]}])
    assert "block 2 (columns): column 1: block 1 (meter)" in msg


def test_card_and_column_block_counts() -> None:
    text = {"type": "text", "text": "a"}
    assert len(validate_blocks([text] * 8, WHERE)) == 8
    assert "9 blocks, 8 at most" in refused([text] * 9)
    col = [text] * 9
    assert "9 blocks, 8 at most" in refused([{"type": "columns", "columns": [col, [text]]}])
    ok = [text] * 8
    assert one({"type": "columns", "columns": [ok, ok]})["type"] == "columns"


def test_rows_limit_is_across_the_card() -> None:
    items = [{"title": f"i{n}"} for n in range(15)]
    two = [{"type": "rows", "items": items}, {"type": "rows", "items": items}]
    assert len(validate_blocks(two, WHERE)) == 2
    msg = refused([*two, {"type": "rows", "items": items[:1]}])
    assert "block 3 (rows)" in msg and "31 items across the card, 30 at most" in msg
    assert "31 items, 30 at most" in refused([{"type": "rows", "items": items * 2 + items[:1]}])


def test_rows_inside_columns_count_too() -> None:
    items = [{"title": "i"}] * 16
    cols = {"type": "columns", "columns": [[{"type": "rows", "items": items}]] * 2}
    assert "across the card" in refused([cols])


def test_rows_item_checks() -> None:
    assert "item 1" in refused([{"type": "rows", "items": [{"detail": "x"}]}])
    assert "done must be true or false" in refused(
        [{"type": "rows", "items": [{"title": "a", "done": "yes"}]}]
    )
    assert "status" in refused([{"type": "rows", "items": [{"title": "a", "status": "red"}]}])
    got = one({"type": "rows", "items": [{"title": "a", "status": "down", "x": 1}]})
    assert got["items"][0] == {"title": "a", "status": "bad"}


def test_table_highlight_must_be_a_row() -> None:
    table = {"type": "table", "columns": ["A"], "rows": [["x"], ["y"]]}
    assert one({**table, "highlight": 0})["highlight"] == 0
    for bad in (2, -1, True, "1", 1.5):
        msg = refused([{**table, "highlight": bad}])
        assert "block 1 (table)" in msg and "highlight" in msg


def test_table_chart_timeline_reuse_the_kits_checks() -> None:
    assert "row 1 has 1 cells but there are 2 columns" in refused(
        [{"type": "table", "columns": ["A", "B"], "rows": [["x"]]}]
    )
    assert "donut takes one series" in refused(
        [
            {
                "type": "chart",
                "kind": "donut",
                "x": ["a"],
                "series": [{"name": "s", "values": [1]}, {"name": "t", "values": [1]}],
            }
        ]
    )
    assert "end 09:00 is before start 10:00" in refused(
        [{"type": "timeline", "events": [{"title": "a", "start": "10:00", "end": "09:00"}]}]
    )


def test_chart_table_timeline_take_no_title() -> None:
    got = one({**MINIMAL[5], "title": "Spend"})
    assert "title" not in got


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-10-25T14:30",
        "2026-10-25T14:30:00Z",
        "2026-10-25T14:30-07:00",
        "2026-10-25T14:30:15+05:30",
        "2026-10-25",
    ],
)
def test_iso_8601_accepted(stamp: str) -> None:
    assert one({"type": "time", "at": stamp})["at"] == stamp
    assert one({"type": "time", "at": "2026-10-25", "end": stamp})["end"] == stamp
    assert one({"type": "quote", "text": "x", "when": stamp})["when"] == stamp
    got = one({"type": "rows", "items": [{"title": "a", "when": stamp}]})
    assert got["items"][0]["when"] == stamp


@pytest.mark.parametrize(
    "stamp",
    [
        "14:30",
        "tomorrow",
        "10/25/2026",
        "2026-10-25 14:30",
        "2026-13-01",
        "2026-02-30",
        "2026-10-25T25:00",
    ],
)
def test_other_times_refused(stamp: str) -> None:
    assert "ISO 8601" in refused([{"type": "time", "at": stamp}])
    assert "block 1 (quote)" in refused([{"type": "quote", "text": "x", "when": stamp}])
    msg = refused([{"type": "rows", "items": [{"title": "a", "when": stamp}]}])
    assert "item 1" in msg and "ISO 8601" in msg


def test_time_end_and_at_checks() -> None:
    assert "at is required" in refused([{"type": "time"}])
    assert "end" in refused([{"type": "time", "at": "2026-10-25", "end": "later"}])


def test_strip_emoji_removes_pictographs() -> None:
    assert strip_emoji("Lunch 🍕 at noon") == "Lunch at noon"
    assert strip_emoji("Done ✅") == "Done "
    assert strip_emoji("👍🏽 ok") == " ok"
    assert strip_emoji("Team 👩‍💻 ships") == "Team ships"
    assert strip_emoji("Heart ❤️!") == "Heart !"
    assert strip_emoji("🇨🇦 Canada") == " Canada"
    assert strip_emoji("1️⃣ first") == "1 first"


def test_strip_emoji_keeps_data_symbols() -> None:
    kept = "$41.20 €5 £3 100% 20° a → b ✓ 3 \u00d7 4 \u2013 “q” é ñ 日本 Maple Park, Sam & Jo!"
    assert strip_emoji(kept) == kept


def test_strip_emoji_removes_leftover_selectors_and_joiners() -> None:
    assert strip_emoji("a️b‍c︎") == "abc"


def test_emoji_stripped_from_every_string_in_a_block() -> None:
    got = one(
        {
            "type": "rows",
            "label": "Today 🎉",
            "items": [{"title": "Milk 🥛", "detail": "2 L 🥛", "chip": "🏠 home"}],
        }
    )
    assert got["label"] == "Today"
    assert got["items"][0] == {"title": "Milk", "detail": "2 L", "chip": "home"}
    assert one({"type": "text", "text": "a 🎉\nb 🎉"})["text"] == "a\nb"
    assert one({"type": "chips", "items": ["🏠 home"]})["items"] == ["home"]
    assert one({"type": "table", "columns": ["Day 📅"], "rows": [["Mon 🙂"]]}) == {
        "type": "table",
        "columns": ["Day"],
        "rows": [["Mon"]],
    }


def test_a_string_of_only_emoji_is_blank() -> None:
    assert "blank" in refused([{"type": "status", "tone": "good", "text": "🎉"}])
