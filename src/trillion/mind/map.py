"""The Living Mind's map, assembled from explicit sources (GAL-10, decisions 3 to 6).

`assemble(sources)` is pure: it is handed each source as a function to call, never the
runtime, so it is tested without a server. Each region is built in its own try: a
source that fails empties its region alone, sets `stats.sources.<region>` to "error"
and is logged by its exception class only. Edges between regions are drawn last, and
only between nodes that are both on the map: nothing is drawn that isn't true.

No bodies: a memory's label is its first 80 characters, a chat's is its title. Detail
loads per node (`api/mind.py`).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from trillion.memory.meaning import FLOOR
from trillion.memory.types import Memory

logger = logging.getLogger(__name__)

# ── The numbers, in one place ────────────────────────────────────────────
# Similarity edges: each memory's best neighbours by meaning, at or above the floor.
# bge-small scores run high, so 0.6 is meaning's own floor (`meaning.FLOOR`).
SIMILAR_TOP = 3
SIMILAR_FLOOR = FLOOR
# The newest memories drawn; the rest are counted in `stats.memory_total`.
MEMORY_CAP = 500
# Memories `recall` edges reach from the core.
RECALL_EDGES = 3
# The most recent chats drawn.
CHAT_CAP = 10
# Freshness halves every 30 days.
HALF_LIFE_MS = 30 * 24 * 60 * 60 * 1000
LABEL_CHARS = 80
SIZE_MIN, SIZE_MAX = 0.5, 3.0

Region = Literal["core", "memory", "working", "agents", "knowledge", "rim"]
REGIONS: tuple[tuple[Region, str], ...] = (
    ("core", "Core"),
    ("memory", "Memory"),
    ("working", "Recent chats"),
    ("agents", "Helpers"),
    ("knowledge", "Knowledge"),
    ("rim", "Tools"),
)
SourceState = Literal["ok", "empty", "error", "no_meaning"]


# ── What the sources give ────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class CoreInfo:
    """Her, and the two blocks her prompt is made of: names of parts and sizes only."""

    stable_chars: int
    stable_parts: tuple[str, ...]
    turn_chars: int
    turn_parts: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Chat:
    id: str
    title: str | None
    kind: str
    last_ms: int


@dataclass(frozen=True, kw_only=True)
class HelperInfo:
    """A helper as the snapshot lists it (`helpers/build.py`), the fields the map needs."""

    id: str
    name: str
    kind: str
    state: str
    last_ms: int | None = None
    # Catalog ids of the connections it lists.
    connections: tuple[str, ...] = ()
    color: str | None = None
    icon: str | None = None


@dataclass(frozen=True, kw_only=True)
class KnowledgeInfo:
    # `SOUL.md`, `prompts/rules.md` or `skill:<name>`.
    key: str
    label: str
    kind: Literal["persona", "prompt", "skill"]
    modified_ms: int | None = None


@dataclass(frozen=True, kw_only=True)
class ToolInfo:
    name: str
    label: str
    category: str
    risk: str


@dataclass(frozen=True, kw_only=True)
class ConnectorInfo:
    # `mcp:<server>`, `google` or `slack`.
    key: str
    label: str
    status: str
    # The tools it serves, by name, and the catalog ids a helper may name it by.
    tools: tuple[str, ...] = ()
    catalog: frozenset[str] = frozenset()


@dataclass(frozen=True, kw_only=True)
class Sources:
    """Each source is a function, so its failure is caught in its own region."""

    agent: str
    now_ms: int
    core: Callable[[], CoreInfo]
    # Current memories (retracted and superseded ones are dropped here too).
    memories: Callable[[], Sequence[Memory]]
    # Each memory's unit vector for the current model; None when meaning is not on.
    vectors: Callable[[], Mapping[str, Sequence[float]] | None]
    chats: Callable[[], Sequence[Chat]]
    helpers: Callable[[], Sequence[HelperInfo]]
    knowledge: Callable[[], Sequence[KnowledgeInfo]]
    tools: Callable[[], Sequence[ToolInfo]]
    connectors: Callable[[], Sequence[ConnectorInfo]]


# ── What the page gets ───────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class Node:
    id: str
    type: str
    region: Region
    label: str
    size: float = 1.0
    freshness: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "region": self.region,
            "label": self.label,
            "size": self.size,
            "freshness": self.freshness,
            "extra": self.extra,
        }


@dataclass(frozen=True, kw_only=True)
class Edge:
    source: str
    target: str
    kind: Literal["similarity", "recall", "handoff", "uses", "feeds", "serves"]
    weight: float = 1.0

    def to_json(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "kind": self.kind,
                "weight": self.weight}  # fmt: skip


@dataclass(frozen=True, kw_only=True)
class MindMap:
    agent: str
    nodes: list[Node]
    edges: list[Edge]
    sources: dict[Region, SourceState]
    memory_total: int = 0
    memory_shown: int = 0

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def to_json(self) -> dict[str, Any]:
        counts = {r: 0 for r, _ in REGIONS}
        for n in self.nodes:
            counts[n.region] += 1
        return {
            "agent": self.agent,
            "regions": [{"id": r, "label": label, "count": counts[r]} for r, label in REGIONS],
            "nodes": [n.to_json() for n in self.nodes],
            "edges": [e.to_json() for e in self.edges],
            "stats": {
                "memory_total": self.memory_total,
                "memory_shown": self.memory_shown,
                "sources": dict(self.sources),
            },
        }


# ── Small pieces ─────────────────────────────────────────────────────────


def freshness(at_ms: int | None, now_ms: int) -> float:
    """1 now, 0.5 after 30 days, 0.25 after 60; 1 for anything with no date."""
    if at_ms is None:
        return 1.0
    age = max(0, now_ms - at_ms)
    return round(math.pow(0.5, age / HALF_LIFE_MS), 3)


def _size(value: float) -> float:
    return round(min(SIZE_MAX, max(SIZE_MIN, value)), 2)


def label_of(text: str) -> str:
    """The first 80 characters, on one line."""
    flat = " ".join(text.split())
    return flat if len(flat) <= LABEL_CHARS else flat[: LABEL_CHARS - 1].rstrip() + "…"


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b, strict=False))


def similar_pairs(
    vectors: Mapping[str, Sequence[float]],
    *,
    top: int = SIMILAR_TOP,
    floor: float = SIMILAR_FLOOR,
) -> dict[tuple[str, str], float]:
    """Each memory's `top` nearest at or above `floor`, as unordered pairs (a < b) with
    their score: a pair two memories both chose is one edge. Vectors are unit length,
    so cosine is the dot product. numpy when it is there (it comes with meaning);
    plain Python otherwise."""
    ids = sorted(vectors)
    if len(ids) < 2:
        return {}
    scores = _scores(ids, vectors)
    pairs: dict[tuple[str, str], float] = {}
    for i, a in enumerate(ids):
        ranked = sorted(
            ((s, b) for j, (b, s) in enumerate(zip(ids, scores[i], strict=True)) if j != i),
            key=lambda sb: (-sb[0], sb[1]),
        )
        for score, b in ranked[:top]:
            if score < floor:
                break
            pairs[(a, b) if a < b else (b, a)] = round(score, 3)
    return pairs


def _scores(ids: Sequence[str], vectors: Mapping[str, Sequence[float]]) -> list[list[float]]:
    try:
        import numpy as np
    except ImportError:
        rows = [vectors[i] for i in ids]
        return [[_dot(a, b) for b in rows] for a in rows]
    m = np.asarray([vectors[i] for i in ids], dtype=np.float32)
    matrix: list[list[float]] = (m @ m.T).astype(float).tolist()
    return matrix


# ── The regions ──────────────────────────────────────────────────────────


@dataclass
class _Built:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    state: SourceState = "ok"
    memory_total: int = 0


def _core(s: Sources) -> _Built:
    info = s.core()
    return _Built(
        nodes=[
            Node(id="core", type="core", region="core", label=s.agent, size=SIZE_MAX),
            Node(
                id="ring:stable",
                type="ring",
                region="core",
                label="Who she is",
                size=2.0,
                extra={"chars": info.stable_chars},
            ),
            Node(
                id="ring:turn",
                type="ring",
                region="core",
                label="This turn",
                size=1.6,
                extra={"chars": info.turn_chars},
            ),
        ]
    )


def _memory(s: Sources) -> _Built:
    current = [m for m in s.memories() if m.current]
    newest = sorted(current, key=lambda m: (m.as_of, m.id), reverse=True)[:MEMORY_CAP]
    vectors = s.vectors()
    shown = {m.id for m in newest}
    pairs = (
        similar_pairs({i: v for i, v in vectors.items() if i in shown})
        if vectors is not None
        else {}
    )
    degree: dict[str, int] = dict.fromkeys(shown, 0)
    for a, b in pairs:
        degree[a] += 1
        degree[b] += 1
    nodes = [
        Node(
            id=f"mem:{m.id}",
            type="memory",
            region="memory",
            label=label_of(m.text),
            size=_size(0.8 + 0.3 * degree[m.id]),
            freshness=freshness(m.as_of, s.now_ms),
            extra={"kind": m.type, "source": m.source},
        )
        for m in newest
    ]
    edges = [
        Edge(source=f"mem:{a}", target=f"mem:{b}", kind="similarity", weight=w)
        for (a, b), w in sorted(pairs.items())
    ]
    state: SourceState = "empty" if not newest else "no_meaning" if vectors is None else "ok"
    return _Built(nodes=nodes, edges=edges, state=state, memory_total=len(current))


def _working(s: Sources) -> _Built:
    chats = sorted(s.chats(), key=lambda c: (-c.last_ms, c.id))[:CHAT_CAP]
    nodes = [
        Node(
            id=f"chat:{c.id}",
            type="chat",
            region="working",
            label=label_of(c.title) if c.title else "Untitled",
            freshness=freshness(c.last_ms, s.now_ms),
            extra={"kind": c.kind, "last_ms": c.last_ms},
        )
        for c in chats
    ]
    return _Built(nodes=nodes, state="ok" if nodes else "empty")


def _agents(s: Sources) -> _Built:
    nodes = []
    for h in s.helpers():
        extra: dict[str, Any] = {"kind": h.kind, "state": h.state}
        if h.color is not None:
            extra["color"] = h.color
        if h.icon is not None:
            extra["icon"] = h.icon
        nodes.append(
            Node(
                id=f"helper:{h.id}",
                type="helper",
                region="agents",
                label=h.name,
                size=1.4,
                freshness=freshness(h.last_ms, s.now_ms),
                extra=extra,
            )
        )
    return _Built(nodes=nodes, state="ok" if nodes else "empty")


def _knowledge(s: Sources) -> _Built:
    nodes = [
        Node(
            id=f"know:{k.key}",
            type="knowledge",
            region="knowledge",
            label=k.label,
            freshness=freshness(k.modified_ms, s.now_ms),
            extra={"kind": k.kind},
        )
        for k in s.knowledge()
    ]
    return _Built(nodes=nodes, state="ok" if nodes else "empty")


def _rim(s: Sources) -> _Built:
    tools = sorted(s.tools(), key=lambda t: (t.category, t.name))
    connectors = sorted(s.connectors(), key=lambda c: c.key)
    nodes = [
        Node(
            id=f"tool:{t.name}",
            type="tool",
            region="rim",
            label=t.label,
            size=0.6,
            extra={"category": t.category, "risk": t.risk},
        )
        for t in tools
    ]
    names = {t.name for t in tools}
    edges: list[Edge] = []
    for c in connectors:
        nodes.append(
            Node(
                id=f"conn:{c.key}",
                type="connector",
                region="rim",
                label=c.label,
                size=1.6,
                extra={"status": c.status},
            )
        )
        edges += [
            Edge(source=f"conn:{c.key}", target=f"tool:{t}", kind="serves")
            for t in sorted(set(c.tools))
            if t in names
        ]
    return _Built(nodes=nodes, edges=edges, state="ok" if nodes else "empty")


_BUILDERS: tuple[tuple[Region, Callable[[Sources], _Built]], ...] = (
    ("core", _core),
    ("memory", _memory),
    ("working", _working),
    ("agents", _agents),
    ("knowledge", _knowledge),
    ("rim", _rim),
)


def assemble(sources: Sources) -> MindMap:
    built: dict[Region, _Built] = {}
    for region, region_of in _BUILDERS:
        try:
            built[region] = region_of(sources)
        except Exception as e:
            logger.warning("the mind's %s region is empty: %s", region, type(e).__name__)
            built[region] = _Built(state="error")
    nodes = [n for b in built.values() for n in b.nodes]
    edges = [e for b in built.values() for e in b.edges]
    edges += _between(sources, built, {n.id for n in nodes})
    memory = built["memory"]
    return MindMap(
        agent=sources.agent,
        nodes=nodes,
        edges=edges,
        sources={region: b.state for region, b in built.items()},
        memory_total=memory.memory_total,
        memory_shown=len(memory.nodes),
    )


def _between(sources: Sources, built: Mapping[Region, _Built], on_map: set[str]) -> list[Edge]:
    """The edges across regions, each only when both its ends are drawn."""
    found: list[Edge] = []
    if "core" in on_map:
        found += [Edge(source="core", target=m, kind="recall") for m in _recalled(built["memory"])]
        found += [Edge(source="core", target=n.id, kind="handoff") for n in built["agents"].nodes]
    if "ring:stable" in on_map:
        found += [
            Edge(source=n.id, target="ring:stable", kind="feeds") for n in built["knowledge"].nodes
        ]
    found += _uses(sources, built)
    return [e for e in found if e.source in on_map and e.target in on_map]


def _recalled(memory: _Built) -> list[str]:
    """The best-connected memories, or the newest when none is connected."""
    if not memory.nodes:
        return []
    degree: dict[str, int] = {}
    for e in memory.edges:
        degree[e.source] = degree.get(e.source, 0) + 1
        degree[e.target] = degree.get(e.target, 0) + 1
    if not degree:
        return [n.id for n in memory.nodes[:RECALL_EDGES]]  # nodes are newest first
    order = {n.id: i for i, n in enumerate(memory.nodes)}
    ranked = sorted(degree, key=lambda i: (-degree[i], order.get(i, 0)))
    return ranked[:RECALL_EDGES]


def _uses(sources: Sources, built: Mapping[Region, _Built]) -> list[Edge]:
    """A helper to each connector it lists, by catalog id. Read again here from the
    sources, inside its own try: a failure costs these edges, never a region."""
    if not built["agents"].nodes or not any(n.type == "connector" for n in built["rim"].nodes):
        return []
    try:
        by_catalog: dict[str, str] = {}
        for c in sources.connectors():
            for cid in c.catalog:
                by_catalog.setdefault(cid, f"conn:{c.key}")
        out: list[Edge] = []
        for h in sources.helpers():
            targets = _unique(by_catalog[c] for c in h.connections if c in by_catalog)
            out += [Edge(source=f"helper:{h.id}", target=t, kind="uses") for t in targets]
        return out
    except Exception as e:
        logger.warning("the mind's helper connections were not drawn: %s", type(e).__name__)
        return []


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def nearest(
    vectors: Mapping[str, Sequence[float]],
    memory_id: str,
    *,
    top: int = 5,
    floor: float = SIMILAR_FLOOR,
) -> list[tuple[str, float]]:
    """The memories nearest one by meaning, best first, at or above `floor`."""
    mine = vectors.get(memory_id)
    if mine is None:
        return []
    scored = [(i, round(_dot(mine, v), 3)) for i, v in vectors.items() if i != memory_id]
    scored.sort(key=lambda iv: (-iv[1], iv[0]))
    return [(i, s) for i, s in scored if s >= floor][:top]
