"""Everything one running Trillion process shares: the database, the spine, the model."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from trillion import paths
from trillion.brain.config import ModelSettings, Source, resolve
from trillion.brain.types import ModelProvider
from trillion.persona.prompt import Identity, load_identity, load_soul
from trillion.secrets.store import KeychainStore, SecretStore
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore, SqlitePreferences
from trillion.storage.sqlite.spine_store import SqliteSpineStore

OWNER = "local"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(kw_only=True)
class Runtime:
    router: Router
    registry: ToolRegistry
    spine_store: SqliteSpineStore
    conversations: SqliteConversationStore
    preferences: SqlitePreferences
    secrets: SecretStore
    provider: ModelProvider | None
    # Where the model configuration came from: the environment wins and is read-only.
    source: Source
    settings: ModelSettings | None
    identity: Identity
    soul: str
    granted_roots: tuple[Path, ...]
    env: Mapping[str, str] | None = None
    # Issued fresh at every launch and held only in memory (SPN-11a).
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    clock: Callable[[], int] = _now_ms
    # Live sessions, told when the model changes so every open tab follows.
    sessions: set[Any] = field(default_factory=set)
    _connections: list[aiosqlite.Connection] = field(default_factory=list)

    async def use_model(self, settings: ModelSettings | None) -> None:
        """Adopt saved settings (or none) and tell every open session."""
        self.settings = settings
        self.provider, self.source = resolve(settings, self.secrets, self.env)
        for session in list(self.sessions):
            await session.model_changed()

    async def close(self) -> None:
        for conn in self._connections:
            await conn.close()
        self._connections.clear()


MODEL_PREF = "model"


async def open_runtime(
    *,
    granted_roots: tuple[Path, ...] = (),
    db: Path | None = None,
    persona: Path | None = None,
    secrets: SecretStore | None = None,
    env: Mapping[str, str] | None = None,
) -> Runtime:
    path = db or paths.db_path()
    spine_conn = await connect(path)
    await migrate(spine_conn)
    # Conversations use their own connection: the spine store holds explicit
    # transactions on its connection, and a message must never ride inside one.
    chat_conn = await connect(path)
    registry = ToolRegistry.discover()
    store = SqliteSpineStore(spine_conn)
    router = Router(registry, store, policy=DenyPolicy.default())
    roots = tuple(r.expanduser().resolve() for r in granted_roots)
    # Finish what a crash interrupted before anything new can be proposed (SPN-17a).
    await router.recover(roots)
    await router.forget_settled_arguments()
    preferences = SqlitePreferences(chat_conn)
    keychain = secrets if secrets is not None else KeychainStore()
    settings = ModelSettings.from_json(await preferences.get(OWNER, MODEL_PREF))
    provider, source = resolve(settings, keychain, env)
    return Runtime(
        router=router,
        registry=registry,
        spine_store=store,
        conversations=SqliteConversationStore(chat_conn),
        preferences=preferences,
        secrets=keychain,
        provider=provider,
        source=source,
        settings=settings,
        identity=load_identity(persona),
        soul=load_soul(persona),
        granted_roots=roots,
        env=env,
        _connections=[spine_conn, chat_conn],
    )
