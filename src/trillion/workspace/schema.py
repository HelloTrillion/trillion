"""The kit as tool input: the JSON schema for one widget, for the workspace tools.

Kept compact (the model reads it every turn) and loose on purpose: `kit.validate_widget`
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
    TYPES,
)


def widget_schema() -> dict[str, Any]:
    """One widget, as tool input. Flat (every type's fields side by side) because small
    local models follow a flat schema better than a oneOf, and it costs fewer tokens;
    `validate_widget` enforces which fields each type takes."""
    s, n = {"type": "string"}, {"type": "number"}

    def arr(items: dict[str, Any], d: str) -> dict[str, Any]:
        return {"type": "array", "items": items, "description": d}

    def obj(props: dict[str, Any], need: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": need}

    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": list(TYPES),
                "description": (
                    "stat: label, value. chart: kind, x, series. list: items. "
                    "table: columns, rows. note: text. timeline: events. compare: options."
                ),
            },
            "size": {"type": "string", "enum": list(SIZES), "description": "Default medium."},
            "title": {**s, "description": "Not for stat."},
            "label": {**s, "description": "Stat: what the number is."},
            "value": {**s, "description": "Stat: the number as shown, e.g. '$41.20'."},
            "delta": {**s, "description": "Stat change, e.g. '+12%'."},
            "hint": {**s, "description": "Stat: small print, e.g. 'vs September'."},
            "tone": {"type": "string", "enum": list(TONES), "description": "Stat."},
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "Chart. Donut: one series.",
            },
            "x": arr(s, f"Chart labels, at most {MAX_POINTS}."),
            "series": arr(
                obj({"name": s, "values": arr(n, "One per x.")}, ["name", "values"]),
                f"At most {MAX_SERIES}.",
            ),
            "unit": {**s, "description": "Chart: unit of the values, e.g. '$'."},
            "items": arr(
                obj({"title": s, "subtitle": s, "meta": s, "done": {"type": "boolean"}}, ["title"]),
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
                            obj({"label": s, "score": n}, ["label", "score"]),
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
        "required": ["type"],
    }
