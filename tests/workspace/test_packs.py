"""Which widgets she is told about: core always, a pack while a connector of its kind
is connected (the widget packs spec, D1)."""

from __future__ import annotations

import pytest

from trillion.connectors.mcp.catalog import BY_ID
from trillion.workspace.packs import PACK_OF, PACK_OF_TYPE, PACKS, told
from trillion.workspace.widgets import TYPES, validate_widget

PHASE_1 = (
    "stat chart list table note timeline compare next_up free_time inbox thread mentions"
    " files quote plan decision facts callout goal sources custom"
).split()


def test_every_type_is_in_exactly_one_pack() -> None:
    listed = [kind for kinds in PACKS.values() for kind in kinds]
    assert sorted(listed) == sorted(TYPES)
    assert len(listed) == len(set(listed))
    assert set(PHASE_1) <= set(PACKS["core"])
    assert {"kpis", "tasks"} <= set(PACKS["core"])
    assert PACK_OF_TYPE["transactions"] == "money" and PACK_OF_TYPE["custom"] == "core"


def test_the_packs_and_their_connectors() -> None:
    assert list(PACKS) == ["core", "money", "customers", "building", "work"]
    by_pack: dict[str, set[str]] = {}
    for catalog_id, pack in PACK_OF.items():
        by_pack.setdefault(pack, set()).add(catalog_id)
    assert by_pack == {
        "money": {"stripe", "paypal", "square"},
        "customers": {"hubspot", "intercom", "nitrosend", "resend"},
        "building": {"github", "sentry", "vercel", "supabase"},
        "work": {"linear", "atlassian", "todoist", "clickup", "monday", "notion", "airtable"},
    }


def test_every_member_is_a_catalogue_entry() -> None:
    assert set(PACK_OF) <= set(BY_ID)


def test_nothing_connected_is_core_only() -> None:
    assert told([]) == list(PACKS["core"])
    # A connector in no pack, or one the catalogue doesn't have, tells her nothing more.
    assert told(["gmail", "slack", "no-such-server"]) == list(PACKS["core"])


@pytest.mark.parametrize(
    ("member", "pack"),
    [("stripe", "money"), ("hubspot", "customers"), ("sentry", "building"), ("linear", "work")],
)
def test_one_connector_adds_its_pack(member: str, pack: str) -> None:
    assert told([member]) == [*PACKS["core"], *PACKS[pack]]


def test_everything_connected_is_every_pack_in_order() -> None:
    every = [kind for kinds in PACKS.values() for kind in kinds]
    assert told(reversed(list(PACK_OF))) == every
    # Two members of one pack tell it once.
    assert told(["stripe", "square"]) == [*PACKS["core"], *PACKS["money"]]


def test_a_pack_widget_is_taken_with_nothing_connected() -> None:
    raw = {"type": "checks", "items": [{"title": "Unit tests", "state": "passing"}]}
    assert validate_widget(raw, 1)["type"] == "checks"
