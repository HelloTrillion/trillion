"""The kit as tool input: the JSON schema for one widget, for the workspace tools.

Kept compact (the model reads it every turn) and loose on purpose: `widgets.validate_widget`
is what decides, and its messages tell the model what to fix.
"""

from __future__ import annotations

from typing import Any

from trillion.workspace.blocks import BLOCK_TONES, BLOCK_TYPES
from trillion.workspace.kit import KINDS, SIZES, TONES
from trillion.workspace.recipe_kit import MAX_ASKS
from trillion.workspace.widgets import TYPES

# One line per type: what she writes for it (`?` optional, `[]` a list). The workspace
# tool's description carries these, so the schema below can stay flat and short.
CARDS = {
    "stat": "label, value, delta?, hint?, tone?",
    "chart": "kind, x[], series[{name, values[]}], unit?",
    "list": "items[{title, subtitle?, meta?, done?}]",
    "table": "columns[], rows[][]",
    "note": "text",
    "timeline": "events[{title, start HH:MM, end?, meta?}]",
    "compare": "options[{name, metrics[{label, score 0-10}], tags[]?, note?}], pick?, reason?",
    "next_up": "event, start, end?, where?, people[]?, note?",
    "free_time": "slots[{start, end}] in local time, no offset",
    "inbox": "unread, needs_reply?, threads[{from, subject, when?, flag? (a tone)}]",
    "thread": "from, subject, request (what they want), due?, when?, excerpt?",
    "mentions": "groups[{channel, items[{from, text, when?}]}]",
    "files": "files[{name, kind?, edited?, by?}]",
    "quote": "text, by?, when?",
    "plan": "steps[{title, done?, detail?}]",
    "decision": "options[] names, criteria[], scores[][] 0-10 one row per option, pick?, reason?",
    "facts": "items[{label, value}]",
    "callout": "text, tone?",
    "goal": "label, value, target (numbers), unit?, by?",
    "sources": "items[{title, site, note?}]",
    "custom": "blocks[]",
}
# The blocks a custom widget arranges, with their fields.
BLOCK_CARDS = {
    "value": "value, label?, delta?, hint?, tone?",
    "sparkline": "values[]",
    "meter": "value, max, label?, shape? bar|ring",
    "time": "at, end?, label?",
    "chart": "as the chart widget",
    "table": "columns[], rows[][], highlight?",
    "timeline": "events[] as the timeline widget",
    "rows": "label?, items[{title, detail?, person?, status?, when?, meta?, chip?, done?}]",
    "person": "name, detail?",
    "people": "names[]",
    "status": "tone, text",
    "chips": "items[], tone?",
    "text": "text, tone?",
    "quote": "text, by?, when?",
    "facts": "items[{label, value}]",
    "columns": "columns[[blocks]]",
}


def type_cards() -> str:
    """The widget types, one line each, for the workspace tool's description."""
    types = "\n".join(f"{kind}: {CARDS[kind]}" for kind in TYPES)
    blocks = "; ".join(f"{name} {{{BLOCK_CARDS[name]}}}" for name in BLOCK_TYPES)
    return (
        "Widget types (field? optional, field[] a list, others required):\n"
        f"{types}\nAny widget: title?, size?, source? (where its facts came from), asks[]? "
        f"(at most {MAX_ASKS} short questions the user can tap to send). Times ISO 8601. "
        f"Tones: {', '.join(BLOCK_TONES)}.\n"
        f"Blocks, for custom only when no type fits; each {{type, ...}}: {blocks}."
    )


def widget_schema() -> dict[str, Any]:
    """One widget, as tool input. Flat (every type's fields side by side) because small
    local models follow a flat schema better than a oneOf, and it costs fewer tokens;
    `validate_widget` enforces which fields each type takes. Loose too: the router
    checks input against this before the kit sees it, so it must never refuse a widget
    the kit takes (a field shared by types takes every type's shape)."""
    s, n, o = {"type": "string"}, {"type": "number"}, {"type": "object"}
    # Her source is a name; {name} is taken too.
    so = {"type": ["string", "object"]}
    # Display text a model may well give as a number ("value": 26). The kit writes it
    # as text, so the schema must not refuse it first.
    sn = {"type": ["string", "number"]}

    def arr(items: dict[str, Any], text: str | None = None) -> dict[str, Any]:
        return {"type": "array", "items": items} | ({"description": text} if text else {})

    def obj(props: dict[str, Any], need: list[str] | None = None) -> dict[str, Any]:
        return {"type": "object", "properties": props} | ({"required": need} if need else {})

    def d(base: dict[str, Any], text: str) -> dict[str, Any]:
        return {**base, "description": text}

    # The cards in workspace_show's description say each type's shape, so the fields
    # here are mostly bare: the schema is sent twice (show and update), every turn.
    props: dict[str, Any] = {
        # No enums: a model's near miss ("line" for a chart) must reach the kit, which
        # reads what was meant or says exactly what to fix. The values are listed here.
        "type": d(s, f"One of {', '.join(TYPES)}; fields per type in workspace_show."),
        "size": d(s, f"One of {', '.join(SIZES)}."),
        "title": s,
        "source": d(so, "Where its facts came from, e.g. 'Calendar'."),
        "asks": {
            "type": ["array", "string"],
            "items": s,
            "description": f"At most {MAX_ASKS} questions the user can tap to send.",
        },
        "label": sn,
        "value": d(sn, "Stat: as shown, e.g. '$41.20'. Goal: a number."),
        "delta": sn,
        "hint": sn,
        "tone": d(s, f"One of {', '.join(TONES)}."),
        "kind": d(s, f"Chart: one of {', '.join(KINDS)}. Donut: one series."),
        "x": arr(sn),
        "series": arr(obj({"name": s, "values": arr(n)}, ["name", "values"])),
        "unit": s,
        "items": arr(
            o,
            "list: {title, subtitle?, meta?, done?}; facts: {label, value}; "
            "sources: {title, site, note?}.",
        ),
        "columns": arr(s),
        "rows": arr(arr(sn)),
        "text": d(s, "Blank lines, '- ' bullets, **bold**."),
        "events": arr(o, "Times HH:MM, one day."),
        "options": arr(
            {"type": ["string", "number", "object"]},
            "decision: names. compare: {name, metrics[{label, score}], tags[]?, note?}.",
        ),
        "pick": d(s, "The recommended option's name."),
        "reason": s,
        "event": s,
        "start": s,
        "end": s,
        "where": s,
        "people": arr(sn),
        "note": s,
        "slots": arr(o),
        "unread": n,
        "needs_reply": n,
        "threads": arr(o),
        "from": s,
        "subject": s,
        "request": s,
        "due": s,
        "when": s,
        "excerpt": s,
        "groups": arr(o),
        "files": arr(o),
        "by": s,
        "steps": arr(o),
        "criteria": arr(sn),
        "scores": arr(arr(n), "One row per option, a score 0-10 per criterion."),
        "target": n,
        "blocks": arr(obj({"type": s}, ["type"]), "custom: see workspace_show."),
    }
    # Every field may be null: small local models send null for a field they leave out,
    # and the kit reads None as absent. New dicts: the shapes above are shared.
    return {"type": "object", "properties": {k: _nullable(v) for k, v in props.items()}}


def _nullable(prop: dict[str, Any]) -> dict[str, Any]:
    kinds = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
    return {**prop, "type": [*kinds, "null"]}
