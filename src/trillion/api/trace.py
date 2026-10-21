"""How long one turn took, measured while it happens (ONB-11, EVL-3).

A turn starts when the user finished: at the end of their speech for a spoken turn
(not when listening noticed, which is later by the quiet it waited for), and when
the message arrived for anything else. From there, each mark is the first time
something happened: the words were ready, her first words were on screen, her first
sentence went to the voice, its first audio went to the page, the page reported it
audible, the reply was saved.

Only numbers are kept. Nothing anyone said is part of a trace.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from trillion.brain.loop import CallTimed

Via = Literal["voice", "text", "tap", "greet"]
MARKS = ("heard", "first_text", "first_sentence", "first_audio", "played", "saved")
# The page says how far ahead the audio it just scheduled will be heard. Anything
# beyond this is not a schedule, it is a mistake.
MAX_AHEAD_MS = 5000


@dataclass(kw_only=True)
class Trace:
    via: Via
    # The start of the turn, in the event loop's clock (seconds).
    t0: float
    # Spoken turns: how long the quiet lasted before listening decided they had finished.
    endpoint_s: float | None = None
    turn: int = 0
    tools: int = 0
    marks: dict[str, float] = field(default_factory=dict)
    calls: list[dict[str, int | None]] = field(default_factory=list)
    # The stored row, once the turn is over: a late `played` updates it.
    row_id: int | None = None

    def mark(self, name: str, at: float) -> bool:
        """The first time only. Returns whether this was it."""
        if name in self.marks:
            return False
        self.marks[name] = at
        return True

    def ms(self, at: float) -> int:
        return max(0, round((at - self.t0) * 1000))

    def call(self, ev: CallTimed) -> None:
        u = ev.usage
        self.calls.append(
            {
                "start_ms": self.ms(ev.started),
                "first_token_ms": self.ms(ev.first_token) if ev.first_token is not None else None,
                "end_ms": self.ms(ev.ended),
                "input": u.input_tokens,
                "output": u.output_tokens,
                "cache_read": u.cache_read_tokens,
                "cache_write": u.cache_write_tokens,
            }
        )

    def row(
        self,
        *,
        conversation_id: str,
        first_in_session: bool,
        provider: str,
        model: str,
        voice: str | None,
        ears: str | None,
        stop: str,
    ) -> dict[str, Any]:
        """The row `turn_timings` keeps (migration 007)."""
        out: dict[str, Any] = {
            "conversation_id": conversation_id,
            "turn": self.turn,
            "via": self.via,
            "first_in_session": int(first_in_session),
            "provider": provider,
            "model": model,
            "voice": voice,
            "ears": ears,
            "stop": stop,
            "tools": self.tools,
            "endpoint_ms": round(self.endpoint_s * 1000) if self.endpoint_s is not None else None,
            "calls_json": json.dumps(self.calls, separators=(",", ":")),
        }
        for name in MARKS:
            at = self.marks.get(name)
            out[f"{name}_ms"] = self.ms(at) if at is not None else None
        return out


def ahead_ms(raw: object) -> float:
    """How far ahead the page says its audio will be heard, made safe to use."""
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        return 0.0
    return min(max(float(raw), 0.0), MAX_AHEAD_MS)
