"""The Living Mind's map from explicit sources (GAL-10, decisions 3 to 6): every region,
the real edges and only those, and one failing source emptying its own region alone."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from trillion.memory.types import Memory
from trillion.mind import map as mind
from trillion.mind.map import (
    Chat,
    ConnectorInfo,
    CoreInfo,
    HelperInfo,
    KnowledgeInfo,
    Sources,
    ToolInfo,
    assemble,
    similar_pairs,
)

NOW = 1_791_600_000_000
DAY = 24 * 60 * 60 * 1000


def memory(n: int, text: str | None = None, *, age_days: float = 0, **kw: Any) -> Memory:
    at = NOW - int(age_days * DAY)
    return Memory(
        id=f"mem_{n:012x}",
        type=kw.pop("type", "user"),
        text=text or f"Memory number {n}.",
        source=kw.pop("source", "user_statement"),
        moves=False,
        created_at=at,
        as_of=at,
        **kw,
    )


def unit(*xs: float) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in xs))
    return tuple(x / norm for x in xs)


def sources(**over: Any) -> Sources:
    base: dict[str, Any] = {
        "agent": "Nova",
        "now_ms": NOW,
        "core": lambda: CoreInfo(
            stable_chars=4000,
            stable_parts=("soul", "rules"),
            turn_chars=60,
            turn_parts=("now",),
        ),
        "memories": lambda: [],
        "vectors": lambda: None,
        "chats": lambda: [],
        "helpers": lambda: [],
        "knowledge": lambda: [],
        "tools": lambda: [],
        "connectors": lambda: [],
    }
    return Sources(**(base | over))


def broken() -> Any:
    raise RuntimeError("the source broke, and this text must never reach a log")


def ids(m: mind.MindMap, region: str) -> list[str]:
    return [n.id for n in m.nodes if n.region == region]


def edges(m: mind.MindMap, kind: str) -> list[tuple[str, str]]:
    return [(e.source, e.target) for e in m.edges if e.kind == kind]


# ── Each region ──────────────────────────────────────────────────────────


def test_the_core_is_her_with_two_rings_named_from_settings() -> None:
    m = assemble(sources())
    assert m.agent == "Nova"
    core = m.node("core")
    assert core is not None and core.label == "Nova" and core.type == "core"
    assert ids(m, "core") == ["core", "ring:stable", "ring:turn"]
    stable, turn = m.node("ring:stable"), m.node("ring:turn")
    assert stable is not None and stable.extra == {"chars": 4000} and stable.type == "ring"
    assert turn is not None and turn.extra == {"chars": 60}
    assert m.sources["core"] == "ok"


def test_an_empty_mind_has_empty_regions_and_says_so() -> None:
    m = assemble(sources())
    body = m.to_json()
    assert [r["id"] for r in body["regions"]] == [
        "core", "memory", "working", "agents", "knowledge", "rim",
    ]  # fmt: skip
    assert body["stats"]["sources"] == {
        "core": "ok", "memory": "empty", "working": "empty",
        "agents": "empty", "knowledge": "empty", "rim": "empty",
    }  # fmt: skip
    assert body["edges"] == [] and body["stats"]["memory_total"] == 0


def test_memories_are_labelled_by_their_first_80_characters_only() -> None:
    long = "I keep the spare key under the blue flowerpot by the back door, " * 3
    m = assemble(sources(memories=lambda: [memory(1, long, type="reference")]))
    (node,) = [n for n in m.nodes if n.type == "memory"]
    assert node.id == "mem:mem_000000000001" and node.region == "memory"
    assert len(node.label) == 80 and node.label.endswith("…")
    assert long not in str(m.to_json())
    assert node.extra == {"kind": "reference", "source": "user_statement"}


def test_retracted_and_superseded_memories_are_not_drawn() -> None:
    kept = memory(1)
    gone = memory(2, status="retracted")
    old = memory(3, status="superseded", superseded_by=kept.id)
    m = assemble(sources(memories=lambda: [kept, gone, old]))
    assert ids(m, "memory") == [f"mem:{kept.id}"]
    assert m.memory_total == 1


def test_memories_fade_with_a_30_day_half_life() -> None:
    m = assemble(
        sources(memories=lambda: [memory(1), memory(2, age_days=30), memory(3, age_days=60)])
    )
    fresh = {n.id: n.freshness for n in m.nodes if n.type == "memory"}
    assert fresh == {
        "mem:mem_000000000001": 1.0,
        "mem:mem_000000000002": 0.5,
        "mem:mem_000000000003": 0.25,
    }


def test_the_newest_500_are_drawn_and_the_rest_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    many = [memory(n, age_days=n / 100) for n in range(640)]
    m = assemble(sources(memories=lambda: many))
    body = m.to_json()
    assert body["stats"]["memory_total"] == 640 and body["stats"]["memory_shown"] == 500
    shown = ids(m, "memory")
    assert len(shown) == 500 and shown[0] == "mem:mem_000000000000"
    assert "mem:mem_0000000001f3" in shown and "mem:mem_0000000001f4" not in shown  # 499, 500
    (memory_region,) = [r for r in body["regions"] if r["id"] == "memory"]
    assert memory_region["count"] == 500


def test_chats_are_the_ten_newest_by_title_only() -> None:
    chats = [Chat(id=f"c{n}", title=f"Plans {n}", kind="chat", last_ms=NOW - n) for n in range(14)]
    chats.append(Chat(id="untitled", title=None, kind="day", last_ms=NOW + 1))
    m = assemble(sources(chats=lambda: chats))
    shown = [n for n in m.nodes if n.type == "chat"]
    assert len(shown) == 10 and shown[0].id == "chat:untitled" and shown[0].label == "Untitled"
    assert shown[1].label == "Plans 0" and shown[1].extra == {"kind": "chat", "last_ms": NOW}
    assert set(shown[1].extra) == {"kind", "last_ms"}


def test_helpers_are_handed_off_from_the_core_and_use_what_they_list() -> None:
    helpers = [
        HelperInfo(id="own:" + "a" * 32, name="Inbox", kind="own", state="idle",
                   connections=("gmail", "notion"), color="rose", icon="mail"),
        HelperInfo(id="routine:0123abcd", name="Morning", kind="routine", state="working",
                   last_ms=NOW - 30 * DAY),
    ]  # fmt: skip
    connectors = [
        ConnectorInfo(key="google", label="Google", status="connected",
                      catalog=frozenset({"gmail", "google-calendar"})),
    ]  # fmt: skip
    m = assemble(sources(helpers=lambda: helpers, connectors=lambda: connectors))
    own = m.node("helper:own:" + "a" * 32)
    assert own is not None and own.extra == {
        "kind": "own", "state": "idle", "color": "rose", "icon": "mail",
    }  # fmt: skip
    routine = m.node("helper:routine:0123abcd")
    assert routine is not None and routine.extra == {"kind": "routine", "state": "working"}
    assert routine.freshness == 0.5
    assert sorted(edges(m, "handoff")) == [
        ("core", "helper:own:" + "a" * 32), ("core", "helper:routine:0123abcd"),
    ]  # fmt: skip
    # Notion isn't connected: no edge to a connector that isn't there.
    assert edges(m, "uses") == [("helper:own:" + "a" * 32, "conn:google")]


def test_knowledge_feeds_the_stable_ring() -> None:
    files = [
        KnowledgeInfo(key="SOUL.md", label="SOUL.md", kind="persona", modified_ms=NOW),
        KnowledgeInfo(key="prompts/rules.md", label="rules.md", kind="prompt"),
        KnowledgeInfo(key="skill:Recipes", label="Recipes", kind="skill"),
    ]
    m = assemble(sources(knowledge=lambda: files))
    assert ids(m, "knowledge") == ["know:SOUL.md", "know:prompts/rules.md", "know:skill:Recipes"]
    assert m.node("know:skill:Recipes").extra == {"kind": "skill"}  # type: ignore[union-attr]
    assert edges(m, "feeds") == [
        ("know:SOUL.md", "ring:stable"),
        ("know:prompts/rules.md", "ring:stable"),
        ("know:skill:Recipes", "ring:stable"),
    ]


def test_the_rim_is_every_tool_and_each_connector_serves_its_own() -> None:
    tools = [
        ToolInfo(name="file_read", label="Read a file", category="files", risk="read"),
        ToolInfo(name="mcp_notes_find", label="notes: find", category="mcp", risk="write"),
        ToolInfo(name="gmail_send", label="Send email", category="google", risk="irreversible"),
    ]
    connectors = [
        ConnectorInfo(key="mcp:notes", label="Notes", status="connected",
                      tools=("mcp_notes_find", "mcp_notes_gone")),
        ConnectorInfo(key="google", label="Google", status="connected", tools=("gmail_send",)),
    ]  # fmt: skip
    m = assemble(sources(tools=lambda: tools, connectors=lambda: connectors))
    assert m.node("tool:gmail_send").extra == {  # type: ignore[union-attr]
        "category": "google", "risk": "irreversible",
    }  # fmt: skip
    assert m.node("conn:mcp:notes").extra == {"status": "connected"}  # type: ignore[union-attr]
    # A tool the server lists but the registry doesn't hold is not drawn, nor its edge.
    assert sorted(edges(m, "serves")) == [
        ("conn:google", "tool:gmail_send"), ("conn:mcp:notes", "tool:mcp_notes_find"),
    ]  # fmt: skip


# ── Similarity ───────────────────────────────────────────────────────────


def test_similarity_takes_the_top_three_at_or_above_the_floor_once_per_pair() -> None:
    vectors = {
        "a": unit(1, 0, 0),
        "b": unit(1, 0.1, 0),  # very near a
        "c": unit(1, 0.3, 0),
        "d": unit(1, 0.5, 0),
        "e": unit(1, 0.7, 0),
        "far": unit(0, 0, 1),  # near nothing
    }
    pairs = similar_pairs(vectors)
    # Each pair once, a < b.
    assert all(a < b for a, b in pairs)
    assert not any("far" in p for p in pairs)
    # a keeps its three best: b, c, d; e is a's fourth and is only there through others.
    assert {p for p in pairs if "a" in p} <= {("a", "b"), ("a", "c"), ("a", "d"), ("a", "e")}
    a_chose = sorted(((s, p) for p, s in pairs.items() if p[0] == "a"), reverse=True)[:3]
    assert [p for _, p in a_chose] == [("a", "b"), ("a", "c"), ("a", "d")]
    assert all(s >= mind.SIMILAR_FLOOR for s in pairs.values())


def test_nothing_below_the_floor_is_an_edge() -> None:
    cos = 0.59
    vectors = {"a": (1.0, 0.0), "b": (cos, math.sqrt(1 - cos * cos))}
    assert similar_pairs(vectors) == {}
    cos = 0.6
    vectors = {"a": (1.0, 0.0), "b": (cos, math.sqrt(1 - cos * cos))}
    assert similar_pairs(vectors) == {("a", "b"): 0.6}


def test_the_constants_live_in_one_place() -> None:
    assert (mind.SIMILAR_TOP, mind.SIMILAR_FLOOR, mind.MEMORY_CAP) == (3, 0.6, 500)


def test_memories_connected_by_meaning_grow_and_are_recalled_from_the_core() -> None:
    mems = [memory(n, age_days=n) for n in range(5)]
    near = {
        mems[0].id: unit(1, 0, 0),
        mems[1].id: unit(1, 0.2, 0),
        mems[2].id: unit(1, 0.3, 0),
        mems[3].id: unit(0, 1, 0),
        mems[4].id: unit(0, 0, 1),
    }
    m = assemble(sources(memories=lambda: mems, vectors=lambda: near))
    assert m.sources["memory"] == "ok"
    assert sorted(edges(m, "similarity")) == [
        (f"mem:{mems[0].id}", f"mem:{mems[1].id}"),
        (f"mem:{mems[0].id}", f"mem:{mems[2].id}"),
        (f"mem:{mems[1].id}", f"mem:{mems[2].id}"),
    ]
    sizes = {n.id: n.size for n in m.nodes if n.type == "memory"}
    assert sizes[f"mem:{mems[0].id}"] > sizes[f"mem:{mems[3].id}"]
    assert all(0.5 <= s <= 3 for s in sizes.values())
    assert sorted(edges(m, "recall")) == sorted(("core", f"mem:{mems[i].id}") for i in (0, 1, 2))


def test_without_meaning_there_are_no_similarity_edges_and_it_says_why() -> None:
    mems = [memory(n, age_days=n) for n in range(5)]
    m = assemble(sources(memories=lambda: mems, vectors=lambda: None))
    assert m.sources["memory"] == "no_meaning"
    assert edges(m, "similarity") == []
    # The three newest are recalled when nothing is connected.
    assert edges(m, "recall") == [("core", f"mem:{mems[i].id}") for i in (0, 1, 2)]


# ── One source failing ───────────────────────────────────────────────────

SOURCE_OF = {
    "core": "core",
    "memory": "memories",
    "working": "chats",
    "agents": "helpers",
    "knowledge": "knowledge",
    "rim": "tools",
}


def full() -> dict[str, Callable[[], Any]]:
    return {
        "memories": lambda: [memory(1)],
        "vectors": lambda: {memory(1).id: unit(1, 0)},
        "chats": lambda: [Chat(id="c1", title="Plans", kind="chat", last_ms=NOW)],
        "helpers": lambda: [HelperInfo(id="ask:0123abcd", name="Ask", kind="ask", state="idle")],
        "knowledge": lambda: [KnowledgeInfo(key="SOUL.md", label="SOUL.md", kind="persona")],
        "tools": lambda: [ToolInfo(name="file_read", label="Read", category="files", risk="read")],
    }


@pytest.mark.parametrize("region", list(SOURCE_OF))
def test_a_failing_source_empties_only_its_own_region(
    region: str, caplog: pytest.LogCaptureFixture
) -> None:
    m = assemble(sources(**(full() | {SOURCE_OF[region]: broken})))
    assert m.sources[region] == "error"  # type: ignore[index]
    assert ids(m, region) == []
    for other in SOURCE_OF:
        if other != region:
            assert m.sources[other] == "ok", other  # type: ignore[index]
            assert ids(m, other), other
    # Logged by its class only.
    assert "RuntimeError" in caplog.text and "must never reach" not in caplog.text


def test_failing_vectors_empty_the_memory_region() -> None:
    m = assemble(sources(memories=lambda: [memory(1)], vectors=broken))
    assert m.sources["memory"] == "error" and ids(m, "memory") == []
    assert m.sources["core"] == "ok"


def test_with_no_core_nothing_points_at_it() -> None:
    m = assemble(sources(**(full() | {"core": broken})))
    assert not any(e.source == "core" or e.target == "ring:stable" for e in m.edges)
    assert ids(m, "agents") and ids(m, "knowledge")


def test_a_failing_connector_source_empties_the_rim_and_costs_no_helper() -> None:
    helpers = [HelperInfo(id="own:" + "b" * 32, name="H", kind="own", state="idle",
                          connections=("gmail",))]  # fmt: skip
    m = assemble(sources(helpers=lambda: helpers, connectors=broken))
    assert m.sources["rim"] == "error" and m.sources["agents"] == "ok"
    assert edges(m, "uses") == []


def test_json_carries_the_contract_fields() -> None:
    m = assemble(sources(**full()))
    body = m.to_json()
    assert set(body) == {"agent", "regions", "nodes", "edges", "stats"}
    for node in body["nodes"]:
        assert set(node) == {"id", "type", "region", "label", "size", "freshness", "extra"}
        assert 0.5 <= node["size"] <= 3 and 0 <= node["freshness"] <= 1
        prefix = node["id"].split(":", 1)[0]
        assert prefix in {"core", "ring", "mem", "chat", "helper", "know", "tool", "conn"}
    for edge in body["edges"]:
        assert set(edge) == {"source", "target", "kind", "weight"}
    assert set(body["stats"]) == {"memory_total", "memory_shown", "sources"}


def test_a_memory_detail_label_is_one_line() -> None:
    m = assemble(sources(memories=lambda: [replace(memory(1), text="Two\nlines")]))
    assert m.node("mem:mem_000000000001").label == "Two lines"  # type: ignore[union-attr]
