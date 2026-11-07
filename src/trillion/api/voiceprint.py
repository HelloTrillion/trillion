"""Settings → Your voice: teaching her the owner's voice (§5.3a). Behind the session
token like every /api/ route.

The page records each phrase and sends its audio here (16 kHz mono PCM16, base64).
Each is checked and embedded at once; the audio itself is dropped as soon as it has
been, and nothing about the voice goes back to the page: only "fine" or what was wrong
with it. The phrases' embeddings wait here, in memory, until the last one, when they
become the voiceprint (`voice/speaker.py`) or are thrown away.

The model is fetched only from here, when the user presses the button (ONB-7/8).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trillion.api.runtime import Runtime
from trillion.voice.providers import wespeaker
from trillion.voice.speaker import PHRASES, Voiceprints, enrol
from trillion.voice.types import SpeakerEmbedding, UnknownSpeaker, VoiceError

logger = logging.getLogger(__name__)

# What the page asks the user to read, one per phrase. Ordinary words, about four
# seconds each; the last has a yes in it, the way a yes to a card is said.
PROMPTS: tuple[str, ...] = (
    "The morning light came through the kitchen window while the kettle began to sing.",
    "Please remind me to water the plants, and to call back about the weekend plans.",
    "Yes, go ahead. That sounds right to me, so let's do it.",
)

# The most audio one phrase may be: 15 seconds at 16 kHz, 16-bit, base64.
MAX_PHRASE_B64 = 15 * 16_000 * 2 * 4 // 3 + 8


class PhraseBody(BaseModel):
    index: int = Field(ge=0, lt=PHRASES)
    pcm: str = Field(min_length=1, max_length=MAX_PHRASE_B64)


def _fail(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def create_voiceprint_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/voiceprint")
    # The phrases recorded so far, by index. Never leaves this process.
    draft: dict[int, SpeakerEmbedding] = {}

    def prints() -> Voiceprints:
        if rt.voiceprints is None:
            rt.voiceprints = Voiceprints()
        return rt.voiceprints

    async def state() -> dict[str, Any]:
        profile = await asyncio.to_thread(prints().get)
        installed = wespeaker.available()
        ready = installed and await asyncio.to_thread(wespeaker.ready)
        return {
            "installed": installed,
            "model": {
                "ready": ready,
                "size": wespeaker.PACK.size,
                "credit": wespeaker.CREDIT,
                "licence_url": wespeaker.LICENCE_URL,
            },
            "enrolled": profile is not None,
            "enrolled_ms": profile.enrolled_ms if profile is not None else None,
            # Enrolled, but the model or its runtime is gone: every voice is unsure.
            "working": profile is not None and ready,
            "prompts": list(PROMPTS),
            "recorded": sorted(draft),
        }

    @router.get("")
    async def get_state() -> Any:
        return await state()

    @router.post("/model")
    async def fetch_model() -> Any:
        """The one download, pressed by the user. Checked before it is kept (ONB-7)."""
        try:
            await wespeaker.prepare()
        except VoiceError as e:
            logger.info("voice model setup failed: %s", e.kind)
            return _fail(400 if e.kind == "not_installed" else 502, e.kind)
        return await state()

    @router.post("/phrase")
    async def phrase(body: PhraseBody) -> Any:
        if not wespeaker.available():
            return _fail(400, "not_installed")
        if not await asyncio.to_thread(wespeaker.ready):
            return _fail(400, "needs_download")
        try:
            pcm = base64.b64decode(body.pcm, validate=True)
        except (binascii.Error, ValueError):
            return _fail(400, "bad_audio")
        try:
            embedding, problem = await wespeaker.LocalSpeaker().phrase(pcm)
        except Exception as e:
            # The class name only: nothing about the voice is logged.
            logger.warning("voice phrase check failed: %s", type(e).__name__)
            return _fail(500, "model")
        finally:
            del pcm
        if problem is not None or embedding is None:
            draft.pop(body.index, None)
            return {"ok": False, "problem": problem or "short", "recorded": sorted(draft)}
        draft[body.index] = embedding
        return {"ok": True, "problem": None, "recorded": sorted(draft)}

    @router.post("/save")
    async def save() -> Any:
        if sorted(draft) != list(range(PHRASES)):
            return _fail(400, "phrases_missing")
        phrases = [draft[i] for i in range(PHRASES)]
        profile, odd = enrol(wespeaker.NAME, phrases, rt.clock())
        if profile is None:
            # One phrase doesn't sound like the others: that one is said again.
            if odd is not None:
                draft.pop(odd, None)
            return {
                "ok": False,
                "problem": "voices",
                "phrase": odd,
                "recorded": sorted(draft),
            }
        try:
            await asyncio.to_thread(prints().save, profile)
        except OSError as e:
            logger.warning("voiceprint not saved: %s", type(e).__name__)
            return _fail(500, "not_saved")
        draft.clear()
        rt.speakers = wespeaker.embedder(enrolled=True)
        return {"ok": True, **(await state())}

    @router.delete("/draft")
    async def discard() -> Any:
        draft.clear()
        return await state()

    @router.delete("")
    async def forget() -> Any:
        """Forget the voice: the voiceprint file goes, and with it the speaker check."""
        draft.clear()
        try:
            await asyncio.to_thread(prints().delete)
        except OSError as e:
            logger.warning("voiceprint not deleted: %s", type(e).__name__)
            return _fail(500, "not_deleted")
        rt.speakers = UnknownSpeaker()
        return await state()

    return router


# Mounted by `trillion.api.routes.plugged`: nothing to add to app.py.
ROUTES = (create_voiceprint_router,)
