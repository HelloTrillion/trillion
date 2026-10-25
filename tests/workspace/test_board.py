import math
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest

from trillion.workspace import board as b
from trillion.workspace.board import BoardError
from trillion.workspace.kit import MAX_WIDGETS
from trillion.workspace.schema import widget_schema
from trillion.workspace.widgets import validate_widget

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


def refused(raw: object, at: int | str = 1) -> str:
    with pytest.raises(BoardError) as err:
        validate_widget(raw, at)
    return str(err.value)


def error(call: Callable[[], object]) -> str:
    with pytest.raises(BoardError) as err:
        call()
    return str(err.value)


def series(*lengths: int) -> list[dict[str, object]]:
    return [{"name": f"S{i}", "values": [1] * n} for i, n in enumerate(lengths, 1)]


def compare(*names: str, **extra: object) -> dict[str, object]:
    return {"type": "compare", "options": [{"name": n} for n in names], **extra}


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


def test_strings_go_on_one_line():
    out = validate_widget({**STAT, "label": "Total\n  spent\tin  October"}, 1)
    assert out["label"] == "Total spent in October"
    items = validate_widget({"type": "list", "items": [{"title": "a\n\nb"}]}, 1)
    assert items["items"][0]["title"] == "a b"


def test_display_strings_take_numbers():
    stat = validate_widget({**STAT, "label": 2026, "value": 41.2, "delta": 3.0, "hint": -1}, 1)
    assert (stat["label"], stat["value"], stat["delta"], stat["hint"]) == (
        "2026",
        "41.2",
        "3",
        "-1",
    )
    chart = validate_widget({**CHART, "x": [1, 2.5]}, 1)
    assert chart["x"] == ["1", "2.5"]
    items = validate_widget({"type": "list", "items": [{"title": "a", "meta": 3}]}, 1)
    assert items["items"][0]["meta"] == "3"
    metric = validate_widget(
        compare(
            "A",
            "B",
            options=[{"name": "A", "metrics": [{"label": 2024, "score": 5}]}, {"name": "B"}],
        ),
        1,
    )
    assert metric["options"][0]["metrics"] == [{"label": "2024", "score": 5}]


def test_display_strings_still_refuse_bools_and_nan():
    assert refused({**STAT, "value": True}) == "widget 1 (stat): value must be a string."
    assert refused({**STAT, "value": math.nan}) == "widget 1 (stat): value must be a string."


def test_unknown_type():
    assert refused({"type": "map"}) == (
        "widget 1: unknown type 'map'; "
        "use one of stat, chart, list, table, note, timeline, compare, next_up, free_time, "
        "inbox, thread, mentions, files, quote, plan, decision, facts, callout, goal, sources, "
        "custom."
    )


def test_not_an_object():
    assert refused("chart") == "widget 1: must be an object."


def test_missing_and_blank_required():
    assert refused({"type": "stat", "value": "1"}) == "widget 1 (stat): label is required."
    assert refused({**STAT, "label": "   "}) == "widget 1 (stat): label is blank."


def test_strict_fields_are_not_coerced():
    assert refused({**CHART, "title": 7}) == "widget 1 (chart): title must be a string."
    assert refused({**LIST, "items": [{"title": "a", "done": "yes"}]}) == (
        "widget 1 (list): item 1: done must be true or false."
    )


def test_bad_size_and_tone():
    assert refused({**STAT, "size": "huge"}) == (
        "widget 1 (stat): size must be one of small, medium, wide, full, not 'huge'."
    )
    assert refused({**STAT, "tone": "sideways"}) == (
        "widget 1 (stat): tone must be one of good, bad, attention, neutral, up, down, "
        "not 'sideways'."
    )


def test_chart_kind():
    assert refused({**CHART, "kind": "pie"}) == (
        "widget 1 (chart): kind must be one of line, bar, area, donut, not 'pie'."
    )


def test_chart_series_count():
    assert validate_widget({**CHART, "series": series(2, 2, 2, 2, 2)}, 1)
    assert refused({**CHART, "series": series(2, 2, 2, 2, 2, 2)}) == (
        "widget 1 (chart): 6 series, 5 at most."
    )
    assert refused({**CHART, "series": []}) == "widget 1 (chart): series is empty."


def test_chart_series_length():
    assert refused({**CHART, "series": [{"name": "Spend", "values": [1] * 12}]}) == (
        "widget 1 (chart): series 'Spend' has 12 values but x has 2."
    )


def test_chart_points_boundary():
    x200 = [str(i) for i in range(200)]
    assert len(validate_widget({**CHART, "x": x200, "series": series(200)}, 1)["x"]) == 200
    assert refused({**CHART, "x": [*x200, "201"], "series": series(201)}) == (
        "widget 1 (chart): 201 points, 200 at most."
    )


def test_chart_values_finite_numbers():
    for bad in (math.nan, math.inf):
        assert refused({**CHART, "series": [{"name": "s", "values": [1, bad]}]}) == (
            "widget 1 (chart): series 's': values must be finite numbers."
        )
    assert refused({**CHART, "series": [{"name": "s", "values": [1, True]}]}) == (
        "widget 1 (chart): series 's': True is not a number."
    )


def test_donut_takes_one_series():
    assert refused({**CHART, "kind": "donut", "series": series(2, 2)}) == (
        "widget 1 (chart): a donut takes one series, not 2."
    )


def test_list_limit():
    assert refused({"type": "list", "items": [{"title": "x"}] * 31}) == (
        "widget 1 (list): 31 items, 30 at most."
    )


def test_table_limits():
    assert refused({**TABLE, "rows": [["Mon", 3, "extra"]]}) == (
        "widget 1 (table): row 1 has 3 cells but there are 2 columns."
    )
    assert refused({**TABLE, "columns": [str(i) for i in range(9)], "rows": []}) == (
        "widget 1 (table): 9 columns, 8 at most."
    )
    assert refused({**TABLE, "rows": [["Mon", 1]] * 51}) == "widget 1 (table): 51 rows, 50 at most."
    assert refused({**TABLE, "rows": [["Mon", None]]}) == (
        "widget 1 (table): row 1: a cell must be a string or a number, not None; "
        'use "" for an empty cell.'
    )
    assert refused({**TABLE, "rows": [["Mon", math.nan]]}) == (
        "widget 1 (table): row 1: values must be finite numbers."
    )


def test_table_cells_capped_at_label_length():
    cell = validate_widget({**TABLE, "rows": [["M" * 100, 1]]}, 1)["rows"][0][0]
    assert len(cell) == 60 and cell.endswith("…")


def test_note_limit():
    assert refused({**NOTE, "text": "x" * 2001}) == (
        "widget 1 (note): text is 2001 characters, 2000 at most."
    )
    assert len(validate_widget({**NOTE, "text": "x" * 2000}, 1)["text"]) == 2000


def test_timeline_times():
    assert refused({"type": "timeline", "events": [{"title": "a", "start": "9am"}]}) == (
        "widget 1 (timeline): event 1: start '9am' must be HH:MM (00:00 to 23:59)."
    )
    assert refused({"type": "timeline", "events": [{"title": "a", "start": "24:00"}]}) == (
        "widget 1 (timeline): event 1: start '24:00' must be HH:MM (00:00 to 23:59)."
    )
    assert (
        refused({"type": "timeline", "events": [{"title": "a", "start": "10:00", "end": "9:00"}]})
        == "widget 1 (timeline): event 1: end 09:00 is before start 10:00 (one day only)."
    )


def test_timeline_pads_single_digit_hours():
    out = validate_widget(
        {"type": "timeline", "events": [{"title": "a", "start": "9:30", "end": "10:05"}]}, 1
    )
    assert out["events"][0]["start"] == "09:30" and out["events"][0]["end"] == "10:05"


def test_timeline_limit():
    assert refused({"type": "timeline", "events": [{"title": "a", "start": "10:00"}] * 21}) == (
        "widget 1 (timeline): 21 events, 20 at most."
    )


def test_compare_option_count():
    assert validate_widget(compare("A", "B", "C", "D"), 1)
    assert refused(compare("A")) == "widget 1 (compare): 1 option; a compare takes 2 to 4."
    assert refused(compare("A", "B", "C", "D", "E")) == (
        "widget 1 (compare): 5 options; a compare takes 2 to 4."
    )


def test_compare_names_differ():
    assert refused(compare("A", "A")) == (
        "widget 1 (compare): option name 'A' is used twice; names must differ."
    )


def test_compare_metrics():
    six = [{"label": str(i), "score": 1} for i in range(6)]
    assert refused(compare("A", "B", options=[{"name": "A", "metrics": six}, {"name": "B"}])) == (
        "widget 1 (compare): option 1: 6 metrics, 5 at most."
    )
    for score in (-1, 11, math.nan):
        bad = [{"name": "A", "metrics": [{"label": "Fun", "score": score}]}, {"name": "B"}]
        assert refused(compare("A", "B", options=bad)) == (
            "widget 1 (compare): option 1: "
            f"metric 'Fun' score must be a number from 0 to 10, not {score!r}."
        )
    nulls = validate_widget(
        compare("A", "B", options=[{"name": "A", "metrics": None}, {"name": "B"}]), 1
    )
    assert nulls["options"][0]["metrics"] == []


def test_compare_tags():
    assert refused(
        compare("A", "B", options=[{"name": "A", "tags": ["ok", " "]}, {"name": "B"}])
    ) == ("widget 1 (compare): option 1: tag 2: tag is blank.")


def test_compare_pick():
    assert refused({**COMPARE, "pick": "Beach"}) == (
        "widget 1 (compare): pick 'Beach' is not an option; use one of Hike, Museum."
    )


def test_existing_widget_named_by_id():
    assert refused({**CHART, "series": [{"name": "Spend", "values": [1]}]}, "w2") == (
        "chart w2: series 'Spend' has 1 values but x has 2."
    )


def test_schema_lists_the_kit():
    schema = widget_schema()
    assert schema["type"] == "object"
    # No enums and no required type: a near miss must reach the kit, which reads it.
    described = schema["properties"]["type"]["description"]
    assert all(w["type"] in described for w in MINIMAL)
    assert "required" not in schema
    near_misses = [
        {"type": "line", "x": [1, 2], "series": [{"name": "s", "values": [1, 2]}]},
        {"type": "text", "label": "Total", "value": 26},
    ]
    for raw in near_misses:
        jsonschema.validate(raw, schema)
        validate_widget(raw, 1)
    jsonschema.Draft202012Validator.check_schema(schema)
    for raw in MINIMAL:
        jsonschema.validate(raw, schema)
    for name, prop in schema["properties"].items():
        if "enum" in prop:
            assert prop["type"] == "string", name
    for name in ("label", "unit", "pick", "reason"):
        assert schema["properties"][name]["description"].split(":")[0] in (
            "Stat",
            "Chart",
            "Compare",
        )


# Board edits


def test_show_on_no_board_opens_it_with_ids():
    board = b.show(None, "October spend", [STAT, CHART])
    assert board["title"] == "October spend" and board["open"] is True
    assert [w["id"] for w in board["widgets"]] == ["w1", "w2"]
    assert board["next_id"] == 3


def test_show_takes_a_tuple():
    assert len(b.show(None, "A", (STAT, NOTE))["widgets"]) == 2


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


def test_twelve_widgets_fit_and_a_thirteenth_is_refused():
    board = b.show(None, "A", [STAT] * MAX_WIDGETS)
    assert len(board["widgets"]) == 12
    message = "that makes 13 widgets; a board holds 12 at most. Remove some or replace them."
    with pytest.raises(BoardError) as err:
        b.show(board, None, [NOTE], mode="add")
    assert str(err.value) == message
    with pytest.raises(BoardError) as err:
        b.show(None, "A", [STAT] * 13)
    assert str(err.value) == message


def test_show_names_the_bad_widget_by_position():
    with pytest.raises(BoardError, match=r"^widget 2 \(chart\)"):
        b.show(None, "A", [STAT, {**CHART, "kind": "pie"}])


def test_bad_mode_refused():
    bad: Any = "merge"
    assert error(lambda: b.show(None, "A", [STAT], mode=bad)) == (
        "mode must be replace or add, not 'merge'."
    )


def test_update_keeps_id_and_position():
    board = b.show(None, "A", [STAT, CHART, LIST])
    board = b.close(board)
    board = b.update(board, "w2", {**CHART, "kind": "bar"})
    assert [w["id"] for w in board["widgets"]] == ["w1", "w2", "w3"]
    assert board["widgets"][1]["kind"] == "bar"
    assert board["open"] is True
    assert board["next_id"] == 4


def test_update_unknown_id_names_the_ones_there():
    board = b.remove(b.show(None, "A", [STAT, STAT, STAT, STAT]), ["w3"])
    with pytest.raises(BoardError) as err:
        b.update(board, "w9", STAT)
    assert str(err.value) == "there is no widget w9 on the board; it has w1, w2, w4."
    with pytest.raises(BoardError) as err:
        b.update(None, "w1", STAT)
    assert str(err.value) == "there is no widget w1 on the board; it is empty."


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
    assert (
        error(lambda: b.remove(board, ["w1", "w7"]))
        == "there is no widget w7 on the board; it has w1."
    )


def test_remove_and_close_with_no_board():
    assert error(lambda: b.remove(None, "all")) == "there is no board to remove from."
    assert error(lambda: b.close(None)) == "there is no board to close."


def test_close_and_reopen():
    board = b.close(b.show(None, "A", [STAT]))
    assert board["open"] is False and len(board["widgets"]) == 1
    again = b.show(board, None, [])
    assert again["open"] is True and again["widgets"] == board["widgets"]


def test_reopen_empty_board_refused():
    message = "there is nothing on the board to show; give it widgets."
    assert error(lambda: b.show(None, None, [])) == message
    assert error(lambda: b.show(b.remove(b.show(None, "A", [STAT]), "all"), None, [])) == message


# Summary


def test_summary():
    board = b.show(None, "October spend", [STAT, CHART])
    assert b.summary(board) == (
        'Board (open): "October spend": w1 stat "Total", w2 line chart "Daily spend".'
    )
    assert b.summary(b.close(board)) == (
        'Board (closed): "October spend": w1 stat "Total", w2 line chart "Daily spend".'
    )
    assert b.summary(None) == ""


def test_summary_clips_titles_and_handles_untitled():
    board = b.show(None, "T" * 60, [LIST, {**NOTE, "title": "N" * 50}])
    assert b.summary(board) == (
        'Board (open): "' + "T" * 39 + '…": w1 list, w2 note "' + "N" * 39 + '…".'
    )


def test_summary_is_one_line():
    board = b.show(None, "Two\nlines", [{**NOTE, "title": "a\nb", "text": "x\n\ny"}])
    assert b.summary(board) == 'Board (open): "Two lines": w1 note "a b".'


def test_summary_of_emptied_board():
    board = b.remove(b.show(None, "A", [STAT]), "all")
    assert b.summary(board) == 'Board (closed): "A": empty.'


# ── What a model meant ──────────────────────────────────────────────────


def test_a_chart_kind_given_as_the_type_is_a_chart_of_that_kind() -> None:
    w = validate_widget(
        {"type": "line", "x": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]}, 1
    )
    assert w["type"] == "chart" and w["kind"] == "line"
    pie = validate_widget({"type": "Pie", "x": ["a"], "series": [{"name": "s", "values": [1]}]}, 1)
    assert pie["type"] == "chart" and pie["kind"] == "donut"


def test_near_synonyms_and_missing_types_are_read_from_the_fields() -> None:
    assert (
        validate_widget({"type": "text", "label": "Total", "value": "26 km"}, 1)["type"] == "stat"
    )
    assert validate_widget({"type": "text", "text": "Hello"}, 1)["type"] == "note"
    assert validate_widget({"type": "KPI", "label": "Total", "value": 26}, 1)["type"] == "stat"
    assert validate_widget({"items": [{"title": "Milk"}]}, 1)["type"] == "list"
    assert (
        validate_widget({"x": ["a"], "series": [{"name": "s", "values": [1]}], "kind": "bar"}, 1)[
            "type"
        ]
        == "chart"
    )


def test_what_cannot_be_read_is_still_refused() -> None:
    with pytest.raises(BoardError, match="widget 1: no type; use one of"):
        validate_widget({"title": "?"}, 1)
