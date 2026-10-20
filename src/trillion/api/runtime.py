"""Everything one running Trillion process shares: the database, the spine, the model."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from trillion import paths
from trillion.brain import compact
from trillion.brain.config import ModelSettings, Source, resolve
from trillion.brain.standing import StandingRules
from trillion.brain.types import ModelProvider
from trillion.memory.book import MemoryBook
from trillion.persona import setup
from trillion.persona.prompt import Identity, load_identity, load_soul
from trillion.secrets.store import KeychainStore, SecretStore
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore, SqlitePreferences
from trillion.storage.sqlite.memory_store import SqliteMemoryStore
from trillion.storage.sqlite.spine_store import SqliteSpineStore
from trillion.tools.describe import NAME as DESCRIBE_SELF
from trillion.tools.describe import DescribeSelf
from trillion.tools.memory import FORGET, RECALL, WRITE, MemoryForget, MemoryRecall, MemoryWrite
from trillion.tools.record import RECORD, DecisionRecord
from trillion.tools.rules import DROP, KEEP, RuleDrop, RuleKeep
from trillion.tools.web import FETCH, WebFetch
from trillion.voice.catalog import listen_option, speech_option
from trillion.voice.types import STTProvider, TTSProvider

OWNER = "local"
SPEECH_PREF = "speech"
LISTEN_PREF = "listening"
# The last choice made, kept when the feature is switched off so that switching it
# back on does not mean choosing everything again.
SPEECH_LAST_PREF = "speech_last"
LISTEN_LAST_PREF = "listening_last"


@dataclass(frozen=True, kw_only=True)
class SpeechSettings:
    kind: str
    voice: str = ""
    # Checked when saved; only for an option that takes an address.
    base_url: str | None = None

    def to_json(self) -> dict[str, str]:
        out = {"kind": self.kind, "voice": self.voice}
        if self.base_url:
            out["base_url"] = self.base_url
        return out

    @classmethod
    def from_json(cls, raw: Any) -> SpeechSettings | None:
        if not isinstance(raw, dict) or speech_option(str(raw.get("kind"))) is None:
            return None
        return cls(
            kind=str(raw["kind"]),
            voice=str(raw.get("voice") or ""),
            base_url=str(raw["base_url"]) if raw.get("base_url") else None,
        )


def build_speech(settings: SpeechSettings | None, secrets: SecretStore) -> TTSProvider | None:
    """The chosen voice, or None when speech is off or its key has been removed."""
    if settings is None:
        return None
    option = speech_option(settings.kind)
    if option is None or (option.needs_key and not secrets.get(option.secret)):
        return None
    if option.takes_url and not settings.base_url:
        return None
    return option.build(secrets, settings.base_url)


@dataclass(frozen=True, kw_only=True)
class ListenSettings:
    kind: str
    # What the user picked from the provider: a recognition model, or a language.
    choice: str = ""

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "choice": self.choice}

    @classmethod
    def from_json(cls, raw: Any) -> ListenSettings | None:
        if not isinstance(raw, dict) or listen_option(str(raw.get("kind"))) is None:
            return None
        return cls(kind=str(raw["kind"]), choice=str(raw.get("choice") or ""))


def build_listening(settings: ListenSettings | None, secrets: SecretStore) -> STTProvider | None:
    """The chosen recognizer, or None when listening is off or its key has been removed."""
    option = listen_option(settings.kind) if settings is not None else None
    if option is None or (option.needs_key and not secrets.get(option.secret)):
        return None
    return option.build(secrets)


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
    memory: MemoryBook
    # What the user told her to always or never do, outside every conversation (BRN-5).
    rules: StandingRules
    # Rough tokens of history before the oldest turns are summarized, and how much of
    # the newest is kept word for word (BRN-5).
    compact_at: int = compact.COMPACT_AT
    compact_keep: int = compact.KEEP
    # The web tool, told what the user says so it knows which addresses they gave.
    web: WebFetch | None = None
    provider: ModelProvider | None
    # Where the model configuration came from: the environment wins and is read-only.
    source: Source
    settings: ModelSettings | None
    speech: SpeechSettings | None
    tts: TTSProvider | None
    listening: ListenSettings | None = None
    stt: STTProvider | None = None
    speech_last: SpeechSettings | None = None
    listening_last: ListenSettings | None = None
    identity: Identity
    soul: str
    # Where the identity files live, and what onboarding last wrote there (PER-6).
    persona_dir: Path
    profile: setup.Profile | None = None
    # The user put onboarding off. It is offered again only when they ask.
    onboarding_skipped: bool = False
    granted_roots: tuple[Path, ...]
    env: Mapping[str, str] | None = None
    # Issued fresh at every launch and held only in memory (SPN-11a).
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    clock: Callable[[], int] = _now_ms
    # Live sessions, told when the model changes so every open tab follows.
    sessions: set[Any] = field(default_factory=set)
    by_conversation: dict[str, Any] = field(default_factory=dict)
    _connections: list[aiosqlite.Connection] = field(default_factory=list)

    def facts(self) -> dict[str, Any]:
        """What is true of this running process, for `describe_self` (BRN-6)."""
        p = self.provider
        return {
            "model": {"provider": p.name, "model": p.model} if p is not None else None,
            "speech": (
                f"your replies are spoken aloud ({self.tts.name})"
                if self.tts is not None
                else "none: your replies are read, not heard"
            ),
            "hearing": (
                f"the user can talk to you through the microphone ({self.stt.name}); what you "
                "receive is a transcript, which can mishear a word"
                if self.stt is not None
                else "none: you cannot hear, the user types"
            ),
            "memory": (
                f"you remember across conversations: {self.memory.count()} saved so far. "
                "memory_write saves a fact, memory_recall looks one up, memory_forget asks "
                "the user first. The user can read the files in their Trillion data folder"
            ),
            "standing rules": (
                f"{len(self.rules.all())} kept. They are in your instructions on every turn; "
                "rule_keep and rule_drop change them, and both ask the user first"
            ),
            "decision record": (
                "every action that asked for the user's yes is recorded permanently: when it "
                "was asked, whether its card was shown, who answered and how, and the outcome. "
                "decision_record reads it, whatever this conversation says or has forgotten"
            ),
            "internet": (
                "web_fetch reads one page, only at an address the user gave in this "
                "conversation or a link on a page you already read there; it refuses this "
                "computer and its local network"
                if self.web is not None
                else "none: you have no tool that reaches the web"
            ),
        }

    async def use_model(self, settings: ModelSettings | None) -> None:
        """Adopt saved settings (or none) and tell every open session."""
        self.settings = settings
        self.provider, self.source = resolve(settings, self.secrets, self.env)
        for session in list(self.sessions):
            await session.settings_changed()

    async def use_speech(self, settings: SpeechSettings | None) -> None:
        # Remember the choice being made, or the one being switched off.
        last = settings or self.speech
        if last is not None and last != self.speech_last:
            self.speech_last = last
            await self.preferences.put(OWNER, SPEECH_LAST_PREF, last.to_json(), self.clock())
        self.speech = settings
        self.tts = build_speech(settings, self.secrets)
        for session in list(self.sessions):
            await session.settings_changed()

    async def use_listening(self, settings: ListenSettings | None) -> None:
        last = settings or self.listening
        if last is not None and last != self.listening_last:
            self.listening_last = last
            await self.preferences.put(OWNER, LISTEN_LAST_PREF, last.to_json(), self.clock())
        self.listening = settings
        self.stt = build_listening(settings, self.secrets)
        for session in list(self.sessions):
            await session.settings_changed()

    def onboarding(self) -> str:
        """`done` once the user has introduced themselves, `skipped` if they put it off."""
        if self.profile is not None:
            return "done"
        return "skipped" if self.onboarding_skipped else "due"

    async def use_profile(self, profile: setup.Profile) -> None:
        """Write the identity files and adopt them. The next turn is hers by that name."""
        self.profile = await asyncio.to_thread(setup.save, profile, self.persona_dir)
        self.identity = load_identity(self.persona_dir)
        await self.preferences.put(OWNER, ONBOARDING_PREF, None, self.clock())
        self.onboarding_skipped = False
        for session in list(self.sessions):
            await session.identity_changed()

    async def skip_onboarding(self) -> None:
        self.onboarding_skipped = True
        await self.preferences.put(OWNER, ONBOARDING_PREF, "skipped", self.clock())

    async def close(self) -> None:
        for conn in self._connections:
            await conn.close()
        self._connections.clear()


MODEL_PREF = "model"
# For trying compaction without a long conversation: a smaller budget, in tokens.
COMPACT_AT_ENV = "TRILLION_COMPACT_AT"


def _compact_at(env: Mapping[str, str] | None) -> int:
    raw = (os.environ if env is None else env).get(COMPACT_AT_ENV, "").strip()
    return max(int(raw), 500) if raw.isdigit() else compact.COMPACT_AT


ONBOARDING_PREF = "onboarding"


async def open_runtime(
    *,
    granted_roots: tuple[Path, ...] = (),
    db: Path | None = None,
    persona: Path | None = None,
    memory: Path | None = None,
    rules: Path | None = None,
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
    speech = SpeechSettings.from_json(await preferences.get(OWNER, SPEECH_PREF))
    book = MemoryBook(SqliteMemoryStore(chat_conn), memory or paths.memory_dir(), OWNER)
    # The files are the truth: whatever changed on disk since last time is adopted
    # now, and anything that does not add up is logged, never silently repaired.
    await book.sync(_now_ms())
    listening = ListenSettings.from_json(await preferences.get(OWNER, LISTEN_PREF))
    standing = StandingRules(rules or paths.rules_path())
    rt = Runtime(
        router=router,
        registry=registry,
        spine_store=store,
        conversations=SqliteConversationStore(chat_conn),
        preferences=preferences,
        secrets=keychain,
        memory=book,
        rules=standing,
        compact_at=(budget := _compact_at(env)),
        compact_keep=min(compact.KEEP, budget // 4),
        provider=provider,
        source=source,
        settings=settings,
        speech=speech,
        tts=build_speech(speech, keychain),
        listening=listening,
        stt=build_listening(listening, keychain),
        speech_last=SpeechSettings.from_json(await preferences.get(OWNER, SPEECH_LAST_PREF))
        or speech,
        listening_last=ListenSettings.from_json(await preferences.get(OWNER, LISTEN_LAST_PREF))
        or listening,
        identity=load_identity(persona),
        soul=load_soul(persona),
        persona_dir=persona or paths.persona_dir(),
        profile=setup.load(persona),
        onboarding_skipped=await preferences.get(OWNER, ONBOARDING_PREF) == "skipped",
        granted_roots=roots,
        env=env,
        _connections=[spine_conn, chat_conn],
    )
    describe = registry.get(DESCRIBE_SELF)
    if isinstance(describe, DescribeSelf):
        describe.bind(registry.definitions, rt.facts)
    web = registry.get(FETCH)
    if isinstance(web, WebFetch):
        rt.web = web
    record = registry.get(RECORD)
    if isinstance(record, DecisionRecord):
        record.bind(store, OWNER, rt.clock)
    for name in (KEEP, DROP):
        rule_tool = registry.get(name)
        if isinstance(rule_tool, RuleKeep | RuleDrop):
            rule_tool.bind(standing, OWNER)
    for name in (WRITE, RECALL, FORGET):
        tool = registry.get(name)
        if isinstance(tool, MemoryWrite | MemoryRecall | MemoryForget):
            tool.bind(book)
    return rt
