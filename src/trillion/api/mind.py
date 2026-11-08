"""The Living Mind's data (GAL-10, docs/specs/2026-11-08-living-mind.md), behind the
session token like every /api/ route.

- `GET /api/mind`: the map: regions, nodes, edges and stats (`mind/map.py`). Each
  source is read in its own try, so one that fails empties its region alone.
- `GET /api/mind/node/{id}`: one node's detail, or 404 for a node not on the map.
- `/ws/observe`: the pulses, read only. The same door as `/ws` (`app.admit`): our Host
  and Origin, then a `hello` with the token. It answers `observe.ready`, then sends
  each event as `{"type": kind, ...ids}`; anything the page sends after is ignored.

No message content, no tool arguments or results, and no memory text but in a
memory's own detail ever leave here (SEC-4). Nothing here writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from trillion import paths
from trillion.api.app import admit
from trillion.api.runtime import OWNER
from trillion.helpers.build import snapshot
from trillion.helpers.model import Helper
from trillion.helpers.wiring import RuntimeSources
from trillion.mind.map import (
    CHAT_CAP,
    Chat,
    ConnectorInfo,
    CoreInfo,
    HelperInfo,
    KnowledgeInfo,
    Sources,
    ToolInfo,
    assemble,
    label_of,
    nearest,
)
from trillion.persona.prompt import TURN_PARTS, stable_parts, volatile_context
from trillion.skills.parse import SKILL_MD
from trillion.spine import labels

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime

logger = logging.getLogger(__name__)


# A knowledge file's detail shows this much of it.
KNOWLEDGE_CHARS = 2000
SIMILAR_SHOWN = 5
PERSONA_FILES = ("SOUL.md", "IDENTITY.md", "USER.md")
# Google's services as the catalog names them: a helper listing one uses Google.
GOOGLE_CATALOG = frozenset({"gmail", "google-calendar", "google-drive"})


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


# ── Reading each source ──────────────────────────────────────────────────


async def _fetch[T](make: Callable[[], Awaitable[T]]) -> Callable[[], T]:
    """Read a source now; what `assemble` calls gives it back, or raises what reading
    raised, so the failure lands in that region's own try."""
    try:
        value = await make()
    except Exception as e:
        failure = e

        def failed() -> T:
            raise failure

        return failed
    return lambda: value


def _take[T](read: Callable[[], T]) -> Callable[[], T]:
    """`_fetch` for a source read on the loop: read once, now."""
    try:
        value = read()
    except Exception as e:
        failure = e

        def failed() -> T:
            raise failure

        return failed
    return lambda: value


def _soul_file(rt: Runtime) -> Path:
    """The user's soul if onboarding made one, else the shipped default (`load_soul`)."""
    mine = rt.persona_dir / "SOUL.md"
    if mine.is_file():
        return mine
    bundled = Path(str(files("trillion.persona") / "default" / "SOUL.md"))
    if bundled.is_file():
        return bundled
    return paths.package_root().parents[1] / "persona" / "SOUL.md"


def _parts(rt: Runtime) -> list[tuple[str, str | None]]:
    return stable_parts(
        rt.identity,
        helpers=rt.helpers is not None,
        skills=bool(rt.skills_on),
        standing=bool(rt.rules.all()),
    )


def _core(rt: Runtime) -> CoreInfo:
    """Blocking: builds the stable block as a turn would (skills read from disk)."""
    stable = sum(len(b.text) for b in rt.system())
    zone = rt.zone()
    now = datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone()
    # The per-turn block as it is with nothing else to add: the time alone.
    turn = len(volatile_context(now, [], zone=zone or ""))
    return CoreInfo(
        stable_chars=stable,
        stable_parts=tuple(name for name, _ in _parts(rt)),
        turn_chars=turn,
        turn_parts=TURN_PARTS,
    )


def _knowledge(rt: Runtime) -> list[tuple[KnowledgeInfo, Path]]:
    """The files that go into the stable block, and where each is. Blocking (stats)."""
    found: list[tuple[str, str, Any, Path]] = []
    for name in PERSONA_FILES:
        path = _soul_file(rt) if name == "SOUL.md" else rt.persona_dir / name
        if path.is_file():
            found.append((name, name, "persona", path))
    prompts = files("trillion.persona") / "prompts"
    for _, prompt in _parts(rt):
        if prompt is not None:
            found.append((f"prompts/{prompt}", prompt, "prompt", Path(str(prompts / prompt))))
    for entry in rt.skills_on or []:
        found.append((f"skill:{entry.name}", entry.name, "skill", entry.folder / SKILL_MD))
    out: list[tuple[KnowledgeInfo, Path]] = []
    for key, label, kind, path in found:
        try:
            modified: int | None = int(path.stat().st_mtime * 1000)
        except OSError:
            modified = None
        out.append((KnowledgeInfo(key=key, label=label, kind=kind, modified_ms=modified), path))
    return out


def _helper(h: Helper) -> HelperInfo:
    return HelperInfo(
        id=h.id,
        name=h.name,
        kind=h.kind,
        state=h.state,
        last_ms=h.last_run.at_ms if h.last_run is not None else None,
        connections=tuple(c.id for c in h.connections),
        color=h.color,
        icon=h.icon,
    )


def _tools(rt: Runtime) -> list[ToolInfo]:
    reg = rt.registry
    return [
        ToolInfo(
            name=d.name,
            label=labels.tool_label(d.name, labels.connected(d, reg.get(d.name))),
            category=d.category,
            risk=str(d.risk),
        )
        for d in reg.definitions()
    ]


def _connectors(rt: Runtime) -> list[ConnectorInfo]:
    """Each MCP server, and Google and Slack when set up (their tools are registered
    only then)."""
    out: list[ConnectorInfo] = []
    if rt.mcp is not None:
        for name, state in rt.mcp.states.items():
            catalog = frozenset({state.config.catalog}) if state.config.catalog else frozenset()
            out.append(
                ConnectorInfo(
                    key=f"mcp:{name}",
                    label=state.config.title or name,
                    status=state.status,
                    tools=tuple(t.name for t in state.tools),
                    catalog=catalog,
                )
            )
    by_category: dict[str, list[str]] = {}
    for d in rt.registry.definitions():
        by_category.setdefault(d.category, []).append(d.name)
    if rt.google is not None and rt.google.saved.accounts:
        out.append(
            ConnectorInfo(
                key="google",
                label="Google",
                status="connected",
                tools=tuple(by_category.get("google", ())),
                catalog=GOOGLE_CATALOG,
            )
        )
    if by_category.get("slack"):
        out.append(
            ConnectorInfo(
                key="slack",
                label="Slack",
                status="connected",
                tools=tuple(by_category["slack"]),
                catalog=frozenset({"slack"}),
            )
        )
    return out


async def _helpers(rt: Runtime) -> list[Helper]:
    found, _ = await snapshot(RuntimeSources(rt))
    return found


async def _chats(rt: Runtime) -> list[Chat]:
    rows, _ = await rt.conversations.list_chats(OWNER, limit=CHAT_CAP)
    return [Chat(id=r.id, title=r.title, kind=r.kind, last_ms=r.last_message_at) for r in rows]


async def gather(rt: Runtime) -> tuple[Sources, list[tuple[KnowledgeInfo, Path]]]:
    """Every source, each read in its own try. The core first: it reads which skills
    are on, which the knowledge region lists."""
    core = await _fetch(lambda: asyncio.to_thread(_core, rt))
    knowledge = await _fetch(lambda: asyncio.to_thread(_knowledge, rt))
    helpers = await _fetch(lambda: _helpers(rt))

    sources = Sources(
        agent=rt.identity.agent_name,
        now_ms=rt.clock(),
        core=core,
        memories=_take(rt.memory.current),
        vectors=_take(rt.memory.vectors),
        chats=await _fetch(lambda: _chats(rt)),
        helpers=lambda: [_helper(h) for h in helpers()],
        knowledge=lambda: [k for k, _ in knowledge()],
        tools=_take(lambda: _tools(rt)),
        connectors=_take(lambda: _connectors(rt)),
    )
    try:
        files_known = knowledge()
    except Exception:
        files_known = []
    return sources, files_known


# ── Detail ───────────────────────────────────────────────────────────────


async def _detail(rt: Runtime, node_id: str) -> dict[str, Any] | None:
    kind, _, rest = node_id.partition(":")
    if node_id == "core":
        info = await asyncio.to_thread(_core, rt)
        return {
            "agent": rt.identity.agent_name,
            "blocks": {"stable": info.stable_chars, "turn": info.turn_chars},
        }
    if node_id in ("ring:stable", "ring:turn"):
        info = await asyncio.to_thread(_core, rt)
        if node_id == "ring:stable":
            return {"chars": info.stable_chars, "parts": list(info.stable_parts)}
        return {"chars": info.turn_chars, "parts": list(info.turn_parts)}
    if kind == "mem":
        return _memory(rt, rest)
    if kind == "chat":
        found = await rt.conversations.chat_detail(rest, OWNER)
        if found is None:
            return None
        row, count = found
        return {
            "title": row.title,
            "kind": row.kind,
            "last_ms": row.last_message_at,
            "messages": count,
        }
    if kind == "helper":
        h = next((h for h in await _helpers(rt) if h.id == rest), None)
        if h is None:
            return None
        return {
            "name": h.name,
            "job": h.job,
            "state": h.state,
            "doing": h.doing,
            "last_run": h.last_run.to_json() if h.last_run is not None else None,
            "next_ms": h.next_ms,
            "connections": [c.to_json() for c in h.connections],
        }
    if kind == "know":
        return await asyncio.to_thread(_knowledge_detail, rt, rest)
    if kind == "tool":
        d = rt.registry.definition(rest)
        if d is None:
            return None
        return {
            "label": labels.tool_label(d.name, labels.connected(d, rt.registry.get(d.name))),
            "description": d.description,
            "risk": str(d.risk),
            "category": d.category,
            "recoverable": d.recoverable,
        }
    if kind == "conn":
        c = next((c for c in _connectors(rt) if c.key == rest), None)
        if c is None:
            return None
        return {"label": c.label, "status": c.status, "tools": list(c.tools)}
    return None


def _memory(rt: Runtime, memory_id: str) -> dict[str, Any] | None:
    m = rt.memory.peek(memory_id)
    if m is None or not m.current:
        return None
    vectors = rt.memory.vectors() or {}
    similar = []
    for other, score in nearest(vectors, m.id, top=SIMILAR_SHOWN):
        o = rt.memory.peek(other)
        if o is not None and o.current:
            similar.append({"id": f"mem:{o.id}", "label": label_of(o.text), "score": score})
    return {
        "text": m.text,
        "kind": m.type,
        "source": m.source,
        "created_ms": m.created_at,
        "as_of_ms": m.as_of,
        "version": m.version,
        "similar": similar,
    }


def _knowledge_detail(rt: Runtime, key: str) -> dict[str, Any] | None:
    _core(rt)  # reads which skills are on, as the map did
    found = next(((k, p) for k, p in _knowledge(rt) if k.key == key), None)
    if found is None:
        return None
    k, path = found
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    shown = f"skills/{path.parent.name}/{path.name}" if k.kind == "skill" else key
    return {
        "path": shown,
        "kind": k.kind,
        "modified_ms": k.modified_ms,
        "chars": len(text),
        "text": text[:KNOWLEDGE_CHARS],
    }


# ── The router ───────────────────────────────────────────────────────────


def create_mind_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/mind")
    async def mind() -> Any:
        sources, _ = await gather(rt)
        # The similarity edges are hundreds of thousands of products: off the loop.
        built = await asyncio.to_thread(assemble, sources)
        return built.to_json()

    @router.get("/api/mind/node/{node_id:path}")
    async def node(node_id: str) -> Any:
        try:
            body = await _detail(rt, node_id)
        except Exception as e:
            logger.warning("a mind node's detail was not read: %s", type(e).__name__)
            return JSONResponse({"error": "unavailable"}, status_code=503)
        if body is None:
            return _not_found()
        types = {"mem": "memory", "chat": "chat", "helper": "helper", "know": "knowledge",
                 "tool": "tool", "conn": "connector", "ring": "ring", "core": "core"}  # fmt: skip
        return {"id": node_id, "type": types[node_id.partition(":")[0]], **body}

    @router.websocket("/ws/observe")
    async def observe(socket: WebSocket) -> None:
        if await admit(socket, rt) is None:
            return
        with rt.observe.subscribe() as watching:
            await socket.send_text(json.dumps({"type": "observe.ready"}))

            async def ignore() -> None:
                # Read only: what the page sends is dropped. A close ends the watch.
                while (await socket.receive())["type"] != "websocket.disconnect":
                    pass

            async def tell() -> None:
                while True:
                    event = await watching.get()
                    await socket.send_text(json.dumps(event, ensure_ascii=False))

            tasks = {asyncio.ensure_future(ignore()), asyncio.ensure_future(tell())}
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
                for t in tasks:
                    with contextlib.suppress(
                        asyncio.CancelledError, WebSocketDisconnect, RuntimeError, OSError
                    ):
                        await t
                with contextlib.suppress(RuntimeError, WebSocketDisconnect, OSError):
                    await socket.close()

    return router


ROUTES = (create_mind_router,)
