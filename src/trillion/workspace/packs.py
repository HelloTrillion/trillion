"""Which widget types she is told about (the widget packs spec, D1 and D2).

Pure, no I/O. The core widgets are always listed in the workspace tools; a pack's are
listed only while a connector of that kind is connected, because the toolkit text is
read on every turn and small models do worse with more. Being told is all a pack
changes: the kit takes every type at all times, so a board keeps loading and numbers
she was told can still become a pipeline.
"""

from __future__ import annotations

from collections.abc import Iterable

# The widget types of each pack, in the order she is told them.
PACKS: dict[str, tuple[str, ...]] = {
    "core": (
        "stat",
        "chart",
        "list",
        "table",
        "note",
        "timeline",
        "compare",
        "next_up",
        "free_time",
        "inbox",
        "thread",
        "mentions",
        "files",
        "quote",
        "plan",
        "decision",
        "facts",
        "callout",
        "goal",
        "sources",
        "kpis",
        "tasks",
        "custom",
    ),
    "money": ("transactions", "invoices", "breakdown", "payouts"),
    "customers": ("pipeline", "deal", "contact", "campaign", "support_queue"),
    "building": ("checks", "errors", "deploys", "services"),
    "work": ("kanban", "sprint", "due_soon"),
}

# Each catalogue connector (connectors/mcp/catalog.py ids) whose data fills a pack.
# A connector in none of them (Gmail, Slack, Canva) changes nothing she is told.
PACK_OF: dict[str, str] = {
    "stripe": "money",
    "paypal": "money",
    "square": "money",
    "hubspot": "customers",
    "intercom": "customers",
    "nitrosend": "customers",
    "resend": "customers",
    "github": "building",
    "sentry": "building",
    "vercel": "building",
    "supabase": "building",
    "linear": "work",
    "atlassian": "work",
    "todoist": "work",
    "clickup": "work",
    "monday": "work",
    "notion": "work",
    "airtable": "work",
}

# The pack a widget type belongs to, for the style guide's captions.
PACK_OF_TYPE: dict[str, str] = {kind: pack for pack, kinds in PACKS.items() for kind in kinds}


def told(connected_catalog_ids: Iterable[str]) -> list[str]:
    """The widget types to list: core, then each pack with a connected member, in the
    order of `PACKS`."""
    packs = {PACK_OF[i] for i in connected_catalog_ids if i in PACK_OF}
    return [
        kind for pack, kinds in PACKS.items() if pack == "core" or pack in packs for kind in kinds
    ]
