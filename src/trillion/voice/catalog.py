"""Every way she can speak, in one list (VOI-11).

The settings panel is generated from this list and the core builds providers
through it, so adding a provider is one adapter file and one entry here. Each
entry says plainly what it costs and where the audio goes (ONB-9, §7 data flow).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from trillion.secrets.store import SecretStore
from trillion.voice.providers.elevenlabs import ElevenLabs
from trillion.voice.providers.system import SystemVoice
from trillion.voice.types import TTSProvider, Where


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
    build: Callable[[SecretStore], TTSProvider]

    @property
    def secret(self) -> str:
        return f"{self.kind}/api_key"


def _elevenlabs(secrets: SecretStore) -> TTSProvider:
    return ElevenLabs(api_key=lambda: secrets.get("elevenlabs/api_key") or "")


SPEECH: tuple[SpeechOption, ...] = (
    SpeechOption(
        kind="system",
        title="This computer's voice",
        note="Free, works at once, and nothing leaves this machine. Sounds synthetic.",
        where="client",
        needs_key=False,
        free=True,
        key_link=None,
        build=lambda _secrets: SystemVoice(),
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
)


def speech_option(kind: str) -> SpeechOption | None:
    return next((o for o in SPEECH if o.kind == kind), None)
