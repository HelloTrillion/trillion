# The Living Mind

**Date:** 2026-11-08 · **Requirements:** GAL-10, with §8.3 (`/ws/observe`), SEC-4, ONB-8 · **Builds on:** the July "Neural Map v2" design in the original project, [memory screen](2026-11-07-memory-screen.md), [helpers panel](2026-11-05-helpers-panel.md), [your helpers](2026-11-05-your-helpers.md)

## Why

Everything that makes her who she is (her persona, what she remembers, the helpers working for her, the tools she can reach) is invisible today: files, rows and entry points. The Living Mind is one page that shows all of it as a 3D map you can orbit, search and click into, and that fires as she works. Its only value is that it is true, so the rule is: **draw nothing that isn't true.** No placeholder nodes, no demo pulses, no invented edges. An empty region stays empty and says why.

## Decisions

1. **A separate page, `mind.html`**, the third Vite entry beside `index.html` and `orb.html`, served by the existing static mount at `/mind.html`. It needs the whole viewport and must not fight the chat page. A small feature file (`apps/web/src/features/mind.ts`) adds one way in: a "Her mind" button in Settings that opens `/mind.html` in a new tab, passing the token in the fragment as the main page does. The page reads the token the same way (`#token=`, then the `trillion-token` key).
2. **The renderer lives in `@hellotrillion/galaxy`** as `packages/galaxy/src/mind/`, exported as `createMind(canvas, options)`. The page (`apps/web/src/mind.ts`, `mind.css`) owns the HUD, inspector, search and data fetching; galaxy owns three.js. three is already 0.186.1; the addons (`EffectComposer`, `UnrealBloomPass`, `OrbitControls`, `CSS2DRenderer`) come from `three/examples/jsm`, so nothing new is installed.
3. **Six regions, each mapped to a real source:**

   | Region | Colour token | Source | Layout |
   |---|---|---|---|
   | core | `--mind-core` | her (the agent name from settings, never hard-coded), with two rings: the stable prompt block (`persona/prompt.py:system_blocks`) and the per-turn block (`volatile_context`) | origin |
   | memory | `--mind-memory` | active rows of `memories` (status `active`), newest 500 | force-directed web |
   | working | `--mind-working` | the 10 most recent chats (`list_chats`) | small ring |
   | agents | `--mind-agents` | `helpers/build.py:snapshot`: her own helpers, routines, timed asks, watchers | arc |
   | knowledge | `--mind-knowledge` | the persona files in use (`SOUL.md`, `IDENTITY.md`, `USER.md`), `persona/prompts/*.md` that go into the stable block, and skills that are on | flat lattice |
   | rim | `--mind-rim` (new token, slate `#8B93A1`) | every tool in `rt.registry.definitions()` grouped by `category`, plus connectors: each MCP server (`rt.mcp.states`), Google and Slack when set up | clustered shell |

   Light-theme values for all six tokens are added to `tokens.css`.
4. **Edges are real, and each kind says what it is.**
   - `similarity` between memories: cosine over the stored vectors in `memory_vectors` for the current model, top 3 neighbours per memory at or above **0.6** (bge-small scores run high; 0.6 is `meaning.FLOOR`). Both numbers are constants in one place. Without vectors (meaning not installed), there are **no** similarity edges, and `stats.sources.memory` says `no_meaning`; the web clusters by type.
   - `recall` from core to the 3 best-connected memories (or the 3 newest when there are no edges).
   - `handoff` from core to each helper; `uses` from a helper to a connector it lists in `connections`. No tool moons on helpers: helpers have no tool allow-list, and drawing one would be false.
   - `feeds` from each knowledge file to the stable ring.
   - `serves` from a connector to each of its tools (MCP tools; Google and Slack tools by category).
5. **One assembly function, every source isolated.** `trillion/mind/map.py:assemble(sources) -> MindMap` takes explicit sources (memory store, conversations, helpers snapshot, registry, MCP states, persona paths), not the runtime, so it is tested without a server. Each region is built in its own try: a failure empties that region and sets `stats.sources.<region> = "error"` (logged with the exception class only). The endpoint never fails because one source did.
6. **No bodies in the skeleton.** Memory labels are the first 80 characters of the text. Detail (full text, provenance, similar memories) loads when a node is clicked. Chat labels are titles only; no message content ever appears on the page. Tool arguments and results are never sent anywhere by this feature (SEC-4).
7. **Live pulses come from a new read-only `/ws/observe`**, never from `/ws`. A small in-process bus, `trillion/mind/observe.py:Observer` on the runtime (`rt.observe`), with `publish(kind, **ids)` and async subscribers, bounded queues that drop the oldest when a page is slow (so a stalled tab can never slow her). The socket checks Host and Origin and takes the same `hello` with token as `/ws`; it ignores anything the page sends after that. Published events carry ids and names only:

   | Event | Published where | Pulse |
   |---|---|---|
   | `turn{conversation_id}` | a turn completes | the per-turn ring flickers; the chat's node, if shown, brightens |
   | `tool.start{name}` / `tool.end{name, ok}` | the session's tool loop | pulse core → tool; red tint when not ok |
   | `memory.written{id}` | `MemoryBook.write` | spark from working → memory; a new node blooms (the page refetches the skeleton) |
   | `memory.recalled{ids}` | `MemoryBook.recall` / `search` when results are used in a turn | those nodes flare, pulse toward core |
   | `helper.started{id}` / `helper.done{id, ok}` | a helper or routine run starts and ends | halo on the helper; pulse core ↔ helper |
   | `alert{critical}` | a critical notice | one ripple across the membrane |

   An event naming a node that is not on the page is dropped, never faked.
8. **Nothing leaves the machine** (ONB-8): no fonts, textures or scripts from a CDN; everything is bundled. The airplane test still passes.
9. **Motion and speed.** Under reduced motion there is no auto-rotate, no drifting particles, and pulses are a brief brightening instead of travel. 60 fps target, `devicePixelRatio` capped at 2, instanced meshes for memories and tools, rendering stops when the tab is hidden, and a governor turns bloom off, then particles, if the frame rate stays under 30. No sound, ever.

## Contract

`GET /api/mind` (bearer token like every `/api/` route):

```json
{
  "agent": "Name",
  "regions": [{"id": "memory", "label": "Memory", "count": 212}],
  "nodes": [
    {"id": "mem:mem_0123456789ab", "type": "memory", "region": "memory",
     "label": "first 80 chars", "size": 1.4, "freshness": 0.82,
     "extra": {"kind": "project", "source": "user_statement"}}
  ],
  "edges": [{"source": "mem:a", "target": "mem:b", "kind": "similarity", "weight": 0.71}],
  "stats": {"memory_total": 640, "memory_shown": 500,
            "sources": {"core": "ok", "memory": "ok", "working": "ok",
                        "agents": "ok", "knowledge": "ok", "rim": "ok"}}
}
```

- Node ids are namespaced: `core`, `ring:stable`, `ring:turn`, `mem:<id>`, `chat:<id>`, `helper:<snapshot id>`, `know:<relative path or skill:name>`, `tool:<name>`, `conn:mcp:<server>`, `conn:google`, `conn:slack`.
- Node types: `core`, `ring`, `memory`, `chat`, `helper`, `knowledge`, `tool`, `connector`.
- `size` is 0.5 to 3 (memories grow with degree). `freshness` is 0 to 1 from age (a 30-day half-life), or 1 for nodes with no date.
- `extra` per type: memory `{kind, source}`; chat `{kind, last_ms}`; helper `{kind, state, color?, icon?}`; tool `{category, risk}`; connector `{status}`; knowledge `{kind: persona|prompt|skill}`; ring `{chars}`.
- `stats.sources.<region>` is `ok`, `empty`, `error`, or for memory `no_meaning`.
- Every edge carries `weight`: the cosine score for `similarity`, 1 for the other kinds. The core node's `extra` is `{}`. Region labels are Core, Memory, Recent chats, Helpers, Knowledge and Tools.
- `ring:turn`'s `chars` is the per-turn block with nothing to add but the time; what else may ride there is named in its detail.

`GET /api/mind/node/{id}`: detail for one node, or 404 (503 if its source can't be read). Every answer carries the node's `id` and `type` beside the fields below.
- memory: `text`, `kind`, `source`, `created_ms`, `as_of_ms`, `version`, `similar: [{id, label, score}]` (up to 5; `id` is a node id, `mem:<id>`, so a click hops straight to it).
- chat: `title`, `kind`, `last_ms`, `messages` (a count).
- helper: the snapshot fields (`name`, `job`, `state`, `doing`, `last_run`, `next_ms`, `connections`).
- knowledge: `path` (relative: `SOUL.md`, `prompts/rules.md`, `skills/<folder>/SKILL.md`), `kind`, `modified_ms`, `chars`, and `text`, the first 2,000 characters.
- tool: `label`, `description`, `risk`, `category`, `recoverable`.
- connector: `label`, `status` and `tools` (its tool names).
- core: `agent` and `blocks: {stable, turn}` (characters). rings: `chars` and `parts` (names of parts, not their text).

`/ws/observe`: the client sends `{"type":"hello","token":…}`; the server sends `{"type":"observe.ready"}`, then events as `{"type": "<event>", …ids}` from the table above.
- Events carry raw ids; the page adds the prefix: `turn.conversation_id` is `chat:<id>`, a tool `name` is `tool:<name>`, `memory.*` ids are `mem:<id>`, a helper `id` is `helper:<id>` (as the helpers panel names it: `ask:…`, `routine:…`, `own:…`). `alert` carries `critical: true`.
- Only the fields in the table pass the bus, and only as strings, numbers, flags or short lists of strings; anything else is dropped before it is queued.
- `helper.started`/`helper.done` come from the scheduler's runs (timed asks, routines, a helper's handoffs). A watcher's checks send none yet. A skipped run ends `ok: true`; only a failure is `ok: false`.
- A wrong token gets `{"type":"error","code":"unauthenticated"}` and close code 4401; another site's Origin is closed with 4403, as `/ws`.

## How

- `mind/map.py:assemble(sources)` takes each source as a function and calls it inside its region's try. All numbers (top 3, 0.6, 500, 10 chats, the half-life) are constants at its top. Similarity uses numpy when it is there (it comes with meaning) and plain Python otherwise, off the event loop.
- `api/mind.py` reads each source in its own try and ends with `ROUTES = (create_mind_router,)`. A plugged-in router can carry a websocket route as it is; `create_app` keeps its hosts and origins on `app.state`, and `/ws` and `/ws/observe` share one door, `app.admit`.
- `MemoryBook` gained `current()`, `vectors()` and `recalled()`; a chat's message count is one new storage query (`chat_detail`). The bus is `rt.observe`; the memory book, the door and the scheduler are handed `rt.observe.publish` in `open_runtime`, as `helpers_changed` is.


## What the user sees

Settings → "Her mind" opens a full-window dark scene: her star at the centre with two rings, a violet web of memories wired by meaning, a cyan ring of recent chats, rose helpers in an arc, amber knowledge crystals feeding the rings, and the tools and connectors as a slate shell at the edge, all inside a faint membrane. Hover shows a label and lights a node's links; click flies to it and opens a side panel with its detail (a memory lists similar memories; clicking one hops along the web). Search flies to the best match. Legend chips hide and show regions. Esc or a double-click on empty space returns to the overview; after 30 s idle it turns slowly. `#node=<id>` opens on that node. When she uses a tool, recalls or writes a memory, or a helper runs, the map fires along those links. A stats line names any region that is empty and why ("No memories by meaning: install the meaning extra").

## Not now

- Editing or forgetting from the map (the Memories screen does that).
- A phone-polished view. It loads on a phone with effects reduced.
- History ("her mind last month").
- The desktop app opening it in its own window.
- Message content in the map.
