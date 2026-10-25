import math

import jsonschema
import pytest

from trillion.workspace import board as b
from trillion.workspace.board import BoardError
from trillion.workspace.kit import MAX_WIDGETS, validate_widget, widget_schema

STAT = {"type": "stat", "label": "Total", "value": "$41.20"}
CHART = {
    "type": "chart",
    "title": "Daily spend",
    "kind": "line",
    "x": ["Oct 1", "Oct 2"],
    "series": [{"name": "Spend", "values": [1.2, 3.4]}],
}
LIST = {"type": "list", "items": [{"title": "Milk"}]}
TABLE = {"type": "table", "columns": ["Day", "Spend"], "rows": [["Mon", 3]]}
NOTE = {"type": "note", "text": "One thing."}
TIMELINE = {"type": "timeline", "events": [{"title": "Standup", "start": "09:30"}]}
COMPARE = {
    "type": "compare",
    "options": [
        {"name": "Hike", "metrics": [{"label": "Fun", "score": 8}]},
        {"name": "Museum", "metrics": [{"label": "Fun", "score": 6}]},
    ],
}
MINIMAL = [STAT, CHART, LIST, TABLE, NOTE, TIMELINE, COMPARE]


def refused(raw: object, *parts: str) -> str:
    with pytest.raises(BoardError) as err:
        validate_widget(raw, 1)
    message = str(err.value)
    for part in parts:
        assert part in message, message
    return message


# The kit


@pytest.mark.parametrize("raw", MINIMAL, ids=lambda w: w["type"])
def test_each_type_accepted_minimal(raw):
    out = validate_widget(raw, 1)
    assert out["type"] == raw["type"]
    assert out["size"] == "medium"
    assert "id" not in out


def test_full_fields_kept_and_extras_dropped():
    stat = validate_widget(
        {**STAT, "delta": "+12%", "hint": "vs September", "tone": "up", "size": "small", "x": 1},
        1,
    )
    assert stat == {
        "type": "stat",
        "size": "small",
        "label": "Total",
        "value": "$41.20",
        "delta": "+12%",
        "hint": "vs September",
        "tone": "up",
    }
    chart = validate_widget({**CHART, "kind": "bar", "unit": "$", "size": "wide"}, 1)
    assert chart["unit"] == "$" and chart["series"] == [{"name": "Spend", "values": [1.2, 3.4]}]
    items = validate_widget(
        {
            "type": "list",
            "title": "Shop",
            "items": [{"title": "Milk", "subtitle": "2 l", "meta": "Tue", "done": True}],
        },
        1,
    )
    assert items["items"] == [{"title": "Milk", "subtitle": "2 l", "meta": "Tue", "done": True}]
    timeline = validate_widget(
        {
            "type": "timeline",
            "events": [{"title": "Lunch", "start": "12:00", "end": "13:15", "meta": "Cafe"}],
        },
        1,
    )
    assert timeline["events"][0]["end"] == "13:15"
    compare = validate_widget(
        {
            **COMPARE,
            "options": [{**o, "note": "n", "tags": ["outside"]} for o in COMPARE["options"]],
            "pick": "Hike",
            "reason": "Sunny.",
        },
        1,
    )
    assert compare["pick"] == "Hike" and compare["options"][0]["tags"] == ["outside"]
    assert validate_widget({**NOTE, "title": "Plan"}, 1)["title"] == "Plan"


def test_null_optional_fields_are_absent():
    assert "delta" not in validate_widget({**STAT, "delta": None}, 1)


def test_strings_trimmed_and_capped():
    out = validate_widget({**STAT, "label": "  " + "L" * 100 + "  ", "value": " 3 "}, 1)
    assert out["value"] == "3"
    assert len(out["label"]) == 60 and out["label"].endswith("…")
    chart = validate_widget({**CHART, "title": "T" * 200}, 1)
    assert len(chart["title"]) == 80


def test_note_keeps_its_lines():
    text = "First.\n\n- one\n- **two**"
    assert validate_widget({**NOTE, "text": f"  {text}\n"}, 1)["text"] == text


def test_unknown_type():
    refused({"type": "map"}, "widget 1", "unknown type 'map'", "stat")


def test_not_an_object():
    refused("chart", "widget 1", "object")


def test_missing_and_blank_required():
    refused({"type": "stat", "value": "1"}, "widget 1 (stat)", "label")
    refused({**STAT, "label": "   "}, "label")


def test_wrong_kinds_are_not_coerced():
    refused({**STAT, "value": 41.2}, "value", "string")
    refused({**LIST, "items": [{"title": "a", "done": "yes"}]}, "done", "true or false")


def test_bad_size_and_tone():
    refused({**STAT, "size": "huge"}, "size", "small")
    refused({**STAT, "tone": "sideways"}, "tone")


def test_chart_limits():
    refused({**CHART, "kind": "pie"}, "kind")
    six = [{"name": f"S{i}", "values": [1, 2]} for i in range(6)]
    refused({**CHART, "series": six}, "6 series", "5")
    refused(
        {**CHART, "series": [{"name": "Spend", "values": [1] * 12}]},
        "widget 1 (chart)",
        "series 'Spend' has 12 values but x has 2",
    )
    refused(
        {
            **CHART,
            "x": [str(i) for i in range(201)],
            "series": [{"name": "s", "values": [1] * 201}],
        },
        "200",
    )
    refused({**CHART, "series": [{"name": "s", "values": [1, math.nan]}]}, "finite")
    refused({**CHART, "series": [{"name": "s", "values": [1, math.inf]}]}, "finite")
    refused({**CHART, "series": [{"name": "s", "values": [1, True]}]}, "number")
    two = [{"name": "a", "values": [1, 2]}, {"name": "b", "values": [1, 2]}]
    refused({**CHART, "kind": "donut", "series": two}, "donut", "one series")
    refused({**CHART, "series": []}, "series")


def test_list_limit():
    refused({"type": "list", "items": [{"title": "x"}] * 31}, "31 items", "30")


def test_table_limits():
    refused({**TABLE, "rows": [["Mon", 3, "extra"]]}, "row 1 has 3 cells but there are 2 columns")
    refused({**TABLE, "columns": [str(i) for i in range(9)], "rows": []}, "9 columns", "8")
    refused({**TABLE, "rows": [["Mon", 1]] * 51}, "51 rows", "50")
    refused({**TABLE, "rows": [["Mon", None]]}, "cell")
    refused({**TABLE, "rows": [["Mon", math.nan]]}, "finite")


def test_note_limit():
    refused({**NOTE, "text": "x" * 2001}, "widget 1 (note)", "2001", "2000")
    assert len(validate_widget({**NOTE, "text": "x" * 2000}, 1)["text"]) == 2000


def test_timeline_limits():
    refused({"type": "timeline", "events": [{"title": "a", "start": "9am"}]}, "event 1", "HH:MM")
    refused({"type": "timeline", "events": [{"title": "a", "start": "24:00"}]}, "HH:MM")
    refused(
        {"type": "timeline", "events": [{"title": "a", "start": "10:00", "end": "09:00"}]}, "before"
    )
    refused(
        {"type": "timeline", "events": [{"title": "a", "start": "10:00"}] * 21}, "21 events", "20"
    )


def test_compare_limits():
    refused({**COMPARE, "options": COMPARE["options"][:1]}, "1 option", "2 to 4")
    refused({**COMPARE, "options": COMPARE["options"] * 3}, "6 options", "2 to 4")
    five = [{"label": str(i), "score": 1} for i in range(6)]
    refused(
        {**COMPARE, "options": [{"name": "a", "metrics": five}, {"name": "b"}]}, "6 metrics", "5"
    )
    for score in (-1, 11, math.nan):
        bad = [{"name": "a", "metrics": [{"label": "Fun", "score": score}]}, {"name": "b"}]
        refused({**COMPARE, "options": bad}, "score", "0 to 10")
    refused({**COMPARE, "pick": "Beach"}, "pick 'Beach'", "Hike")


def test_existing_widget_named_by_id():
    with pytest.raises(BoardError, match=r"^chart w2: series 'Spend' has 1 values but x has 2"):
        validate_widget({**CHART, "series": [{"name": "Spend", "values": [1]}]}, "w2")


def test_schema_lists_the_kit():
    schema = widget_schema()
    assert schema["type"] == "object"
    assert set(schema["properties"]["type"]["enum"]) == {w["type"] for w in MINIMAL}
    assert schema["required"] == ["type"]
    jsonschema.Draft202012Validator.check_schema(schema)
    for raw in MINIMAL:
        jsonschema.validate(raw, schema)


# Board edits


def test_show_on_no_board_opens_it_with_ids():
    board = b.show(None, "October spend", [STAT, CHART])
    assert board["title"] == "October spend" and board["open"] is True
    assert [w["id"] for w in board["widgets"]] == ["w1", "w2"]
    assert board["next_id"] == 3


def test_show_replace_and_add_never_reuse_ids():
    board = b.show(None, "A", [STAT, CHART])
    board = b.show(board, None, [LIST], mode="replace")
    assert [w["id"] for w in board["widgets"]] == ["w3"]
    assert board["title"] == "A"
    board = b.show(board, "B", [NOTE], mode="add")
    assert [w["id"] for w in board["widgets"]] == ["w3", "w4"]
    assert board["title"] == "B"


def test_show_does_not_change_the_board_it_was_given():
    first = b.show(None, "A", [STAT])
    b.show(first, None, [CHART], mode="add")
    assert len(first["widgets"]) == 1


def test_thirteenth_widget_refused():
    board = b.show(None, "A", [STAT] * MAX_WIDGETS)
    with pytest.raises(BoardError, match="13 widgets"):
        b.show(board, None, [NOTE], mode="add")
    with pytest.raises(BoardError, match="13 widgets"):
        b.show(None, "A", [STAT] * 13)


def test_show_names_the_bad_widget_by_position():
    with pytest.raises(BoardError, match=r"^widget 2 \(chart\)"):
        b.show(None, "A", [STAT, {**CHART, "kind": "pie"}])


def test_bad_mode_refused():
    with pytest.raises(BoardError, match="mode"):
        b.show(None, "A", [STAT], mode="merge")


def test_update_keeps_id_and_position():
    board = b.show(None, "A", [STAT, CHART, LIST])
    board = b.close(board)
    board = b.update(board, "w2", {**CHART, "kind": "bar"})
    assert [w["id"] for w in board["widgets"]] == ["w1", "w2", "w3"]
    assert board["widgets"][1]["kind"] == "bar"
    assert board["open"] is True
    assert board["next_id"] == 4


def test_update_unknown_id():
    board = b.show(None, "A", [STAT])
    with pytest.raises(BoardError, match="no widget w9"):
        b.update(board, "w9", STAT)
    with pytest.raises(BoardError, match="no widget"):
        b.update(None, "w1", STAT)


def test_remove_some_and_all():
    board = b.show(None, "A", [STAT, CHART, LIST])
    board = b.remove(board, ["w2"])
    assert [w["id"] for w in board["widgets"]] == ["w1", "w3"] and board["open"]
    board = b.remove(board, ["w1", "w3"])
    assert board["widgets"] == [] and board["open"] is False
    board = b.show(board, None, [NOTE])
    assert [w["id"] for w in board["widgets"]] == ["w4"]
    assert b.remove(board, "all")["open"] is False


def test_remove_unknown_id_changes_nothing():
    board = b.show(None, "A", [STAT])
    with pytest.raises(BoardError, match="no widget w7"):
        b.remove(board, ["w1", "w7"])


def test_close_and_reopen():
    board = b.close(b.show(None, "A", [STAT]))
    assert board["open"] is False and len(board["widgets"]) == 1
    again = b.show(board, None, [])
    assert again["open"] is True and again["widgets"] == board["widgets"]


def test_reopen_empty_board_refused():
    with pytest.raises(BoardError, match="nothing"):
        b.show(None, None, [])
    with pytest.raises(BoardError, match="nothing"):
        b.show(b.remove(b.show(None, "A", [STAT]), "all"), None, [])


# Summary


def test_summary():
    board = b.show(None, "October spend", [STAT, CHART])
    assert b.summary(board) == (
        'Board (open): "October spend": w1 stat "Total", w2 line chart "Daily spend".'
    )
    assert b.summary(b.close(board)).startswith('Board (closed): "October spend": w1')
    assert b.summary(None) == ""


def test_summary_clips_titles_and_handles_untitled():
    board = b.show(None, "T" * 60, [LIST, {**NOTE, "title": "N" * 50}])
    line = b.summary(board)
    assert '"' + "T" * 39 + '…"' in line
    assert 'w1 list, w2 note "' + "N" * 39 + '…".' in line


def test_summary_of_emptied_board():
    board = b.remove(b.show(None, "A", [STAT]), "all")
    assert b.summary(board) == 'Board (closed): "A": empty.'
