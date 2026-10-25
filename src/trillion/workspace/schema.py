"""The kit as tool input: the JSON schema for one widget, for the workspace tools.

Kept compact (the model reads it every turn) and loose on purpose: `widgets.validate_widget`
is what decides, and its messages tell the model what to fix.
"""

from __future__ import annotations

from typing import Any

from trillion.workspace.kit import (
    KINDS,
    MAX_COLUMNS,
    MAX_EVENTS,
    MAX_ITEMS,
    MAX_METRICS,
    MAX_POINTS,
    MAX_ROWS,
    MAX_SERIES,
    MAX_TAGS,
    NOTE,
    SIZES,
    TONES,
)
from trillion.workspace.widgets import TYPES


def widget_schema() -> dict[str, Any]:
    """One widget, as tool input. Flat (every type's fields side by side) because small
    local models follow a flat schema better than a oneOf, and it costs fewer tokens;
    `validate_widget` enforces which fields each type takes."""
    s, n = {"type": "string"}, {"type": "number"}
    # Display text a model may well give as a number ("value": 26). The kit writes it
    # as text, so the schema must not refuse it first.
    sn = {"type": ["string", "number"]}

    def arr(items: dict[str, Any], d: str) -> dict[str, Any]:
        return {"type": "array", "items": items, "description": d}

    def obj(props: dict[str, Any], need: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": need}

    return {
        "type": "object",
        "properties": {
            # No enums: a model's near miss ("line" for a chart) must reach the kit, which
            # reads what was meant or says exactly what to fix. The values are listed here.
            "type": {
                "type": "string",
                "description": (
                    f"One of {', '.join(TYPES)}. stat: label, value. chart: kind, x, series. "
                    "list: items. table: columns, rows. note: text. timeline: events. "
                    "compare: options."
                ),
            },
            "size": {**s, "description": f"One of {', '.join(SIZES)}; default medium."},
            "title": {**s, "description": "Not for stat."},
            "label": {**sn, "description": "Stat: what the number is."},
            "value": {**sn, "description": "Stat: the number as shown, e.g. '$41.20'."},
            "delta": {**sn, "description": "Stat change, e.g. '+12%'."},
            "hint": {**sn, "description": "Stat: small print, e.g. 'vs September'."},
            "tone": {**s, "description": f"Stat: one of {', '.join(TONES)}."},
            "kind": {**s, "description": f"Chart: one of {', '.join(KINDS)}. Donut: one series."},
            "x": arr(sn, f"Chart labels, at most {MAX_POINTS}."),
            "series": arr(
                obj({"name": s, "values": arr(n, "One per x.")}, ["name", "values"]),
                f"At most {MAX_SERIES}.",
            ),
            "unit": {**s, "description": "Chart: unit of the values, e.g. '$'."},
            "items": arr(
                obj(
                    {"title": s, "subtitle": s, "meta": sn, "done": {"type": "boolean"}}, ["title"]
                ),
                f"At most {MAX_ITEMS}.",
            ),
            "columns": arr(s, f"At most {MAX_COLUMNS}."),
            "rows": arr(
                arr({"type": ["string", "number"]}, "One cell per column."), f"At most {MAX_ROWS}."
            ),
            "text": {
                **s,
                "description": f"Note, at most {NOTE} chars; blank lines, '- ' bullets, **bold**.",
            },
            "events": arr(
                obj({"title": s, "start": s, "end": s, "meta": s}, ["title", "start"]),
                f"Times HH:MM, one day; at most {MAX_EVENTS}.",
            ),
            "options": arr(
                obj(
                    {
                        "name": s,
                        "note": s,
                        "metrics": arr(
                            obj({"label": sn, "score": n}, ["label", "score"]),
                            f"Metric label and score 0-10; at most {MAX_METRICS}.",
                        ),
                        "tags": arr(s, f"At most {MAX_TAGS}."),
                    },
                    ["name"],
                ),
                "Compare: 2 to 4, distinct names.",
            ),
            "pick": {**s, "description": "Compare: the recommended option's name."},
            "reason": {**s, "description": "Compare: why the pick."},
        },
    }
