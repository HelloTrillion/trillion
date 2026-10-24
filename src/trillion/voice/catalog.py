"""Every way she can speak, and every way she can hear, in two lists (VOI-11).

The settings panel is generated from this list and the core builds providers
through it, so adding a provider is one adapter file and one entry here. Each
entry says plainly what it costs and where the audio goes (ONB-9, §7 data flow).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from trillion.secrets.store import SecretStore
from trillion.voice.providers.deepgram import Deepgram
from trillion.voice.providers.elevenlabs import ElevenLabs
from trillion.voice.providers.openai_compat import CompatibleSpeech
from trillion.voice.providers.system import SystemVoice
from trillion.voice.providers.whisper import LocalWhisper
from trillion.voice.types import STTProvider, TTSProvider, Where


@dataclass(frozen=True, kw_only=True)
class SpeechOption:
    kind: str
    title: str
    # One honest sentence: quality, cost, and where the words go.
    note: str
    where: Where
    needs_key: bool
    free: bool
    key_link: str | None
    # The user gives the address, checked like a compatible model's.
    takes_url: bool = False
    # The keychain, and the checked address for an option that takes one.
    build: Callable[[SecretStore, str | None], TTSProvider]

    @property
    def secret(self) -> str:
        return f"{self.kind}/api_key"


def _elevenlabs(secrets: SecretStore, _url: str | None) -> TTSProvider:
    return ElevenLabs(api_key=lambda: secrets.get("elevenlabs/api_key") or "")


def _compatible(_secrets: SecretStore, url: str | None) -> TTSProvider:
    return CompatibleSpeech(base_url=url or "")


SPEECH: tuple[SpeechOption, ...] = (
    SpeechOption(
        kind="system",
        title="This computer's voice",
        note="Free, works at once, and nothing leaves this machine. Sounds synthetic.",
        where="client",
        needs_key=False,
        free=True,
        key_link=None,
        build=lambda _secrets, _url: SystemVoice(),
    ),
    SpeechOption(
        kind="elevenlabs",
        title="ElevenLabs",
        note=(
            "The most natural voices. Needs your ElevenLabs key; you pay ElevenLabs for what "
            "you use, and each sentence she speaks is sent to them."
        ),
        where="core",
        needs_key=True,
        free=False,
        key_link="https://elevenlabs.io/app/settings/api-keys",
        build=_elevenlabs,
    ),
    SpeechOption(
        kind="custom",
        title="Another compatible service",
        note=(
            "A voice server that speaks OpenAI's speech API, such as Kokoro on a machine of "
            "your own. Free if you run it yourself; each sentence she speaks is sent there."
        ),
        where="core",
        needs_key=False,
        free=True,
        key_link=None,
        takes_url=True,
        build=_compatible,
    ),
)


def speech_option(kind: str) -> SpeechOption | None:
    return next((o for o in SPEECH if o.kind == kind), None)


# ── Listening ────────────────────────────────────────────────────────────
#
# The browser's own speech recognition is deliberately absent (VOI-11b): in some
# browsers it sends audio to a third party the user never chose.


@dataclass(frozen=True, kw_only=True)
class ListenOption:
    kind: str
    title: str
    note: str
    needs_key: bool
    free: bool
    key_link: str | None
    # What to tell someone whose install lacks this provider's optional part.
    install_hint: str | None
    # Whether this recognizer judges when a turn is over (VOI-12).
    turns: bool = False
    build: Callable[[SecretStore], STTProvider]

    @property
    def secret(self) -> str:
        return f"{self.kind}/api_key"


def _deepgram(secrets: SecretStore) -> STTProvider:
    return Deepgram(api_key=lambda: secrets.get("deepgram/api_key") or "")


LISTENING: tuple[ListenOption, ...] = (
    ListenOption(
        kind="whisper",
        title="On this computer (Whisper)",
        note=(
            "Free, and nothing you say leaves this machine. A one-time download. Slower: "
            "your words appear after you stop speaking, not while."
        ),
        needs_key=False,
        free=True,
        key_link=None,
        install_hint="uv sync --extra whisper",
        build=lambda _secrets: LocalWhisper(),
    ),
    ListenOption(
        kind="deepgram",
        title="Deepgram",
        note=(
            "Fast and accurate; your words appear as you speak. Needs your Deepgram key; you "
            "pay Deepgram for what you use, and what the microphone hears is sent to them."
        ),
        needs_key=True,
        free=False,
        key_link="https://console.deepgram.com/",
        install_hint=None,
        turns=True,
        build=_deepgram,
    ),
)


def listen_option(kind: str) -> ListenOption | None:
    return next((o for o in LISTENING if o.kind == kind), None)
