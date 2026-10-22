"""Who she answers on a channel: the allow-list, failing closed (SUR-5).

Someone not on the list gets no answer at all, and is noted for Settings as having
tried, by name and platform id only (never what they wrote), so the user can allow
them with one tap. In a group she answers only when @mentioned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from trillion.channels.base import Inbound, Platform

Verdict = Literal["answer", "not_allowed", "not_mentioned", "empty"]
MAX_KNOCKS = 20


@dataclass(kw_only=True)
class Knock:
    name: str
    at_ms: int
    times: int = 1


@dataclass
class Gate:
    allowed: dict[Platform, dict[str, str]] = field(default_factory=dict)
    knocks: dict[Platform, dict[str, Knock]] = field(default_factory=dict)

    def judge(self, m: Inbound, now_ms: int) -> Verdict:
        if not m.text.strip():
            return "empty"
        if m.sender not in self.allowed.get(m.platform, {}):
            seen = self.knocks.setdefault(m.platform, {})
            if m.sender in seen:
                seen[m.sender].times += 1
                seen[m.sender].at_ms = now_ms
            elif len(seen) < MAX_KNOCKS:
                seen[m.sender] = Knock(name=m.name[:80], at_ms=now_ms)
            return "not_allowed"
        if not (m.direct or m.mentioned):
            return "not_mentioned"
        return "answer"
