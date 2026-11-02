"""The child process of the kill test (OPS-1): propose a send, confirm it, die mid-send.

Usage: python -I kill_child.py REPO_ROOT SERVER_URL [--die-after-any-answer]
TRILLION_HOME must point at the test's folder."""

from __future__ import annotations

import asyncio
import sys


async def main(url: str, die_after_any_answer: bool) -> None:
    from tests.spine.conftest import FakeClock
    from tests.spine.fakes import HttpSend
    from trillion import paths
    from trillion.spine.contract import ToolCall, ToolContext
    from trillion.spine.deny_floor import DenyPolicy
    from trillion.spine.registry import ToolRegistry
    from trillion.spine.router import Router
    from trillion.storage.db import connect
    from trillion.storage.migrate import migrate
    from trillion.storage.sqlite.spine_store import SqliteSpineStore

    conn = await connect(paths.db_path())
    await migrate(conn)
    registry = ToolRegistry()
    registry.register(
        HttpSend(url, die_after_accept=True, die_after_any_answer=die_after_any_answer)
    )
    router = Router(
        registry, SqliteSpineStore(conn), policy=DenyPolicy.default(), clock=FakeClock()
    )
    ctx = ToolContext(owner_id="local", session_id="s1", turn=1, call_id="c1", via="text")
    card = await router.execute(ToolCall(name="http_send", args={}), ctx)
    assert card.status == "confirmation_required", card
    tap = ToolContext(owner_id="local", session_id="s1", turn=2, call_id="c2", via="tap")
    await router.resolve(card.pending_id or "", "confirm", tap)
    sys.exit(3)  # reached only if the send did not kill the process


if __name__ == "__main__":
    sys.path.insert(0, sys.argv[1])
    asyncio.run(main(sys.argv[2], "--die-after-any-answer" in sys.argv[3:]))
