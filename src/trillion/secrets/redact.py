"""Scrub credentials from every log record before it is written (SEC-5).

A mistake in a future logging statement must not be able to leak a key. Two
nets: the shapes of keys we know, and the exact values currently in use, which
catches a credential whose format nobody anticipated.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

REDACTED = "[redacted]"
# Provider key shapes: Anthropic and OpenAI style `sk-…`, and bearer tokens in headers.
_SHAPES = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}|(?i:bearer)\s+[A-Za-z0-9_\-\.=]{16,}")
_MIN_KNOWN = 8  # a shorter "secret" would redact ordinary words


def scrub(text: str, known: Iterable[str] = ()) -> str:
    for value in known:
        if len(value) >= _MIN_KNOWN and value in text:
            text = text.replace(value, REDACTED)
    return _SHAPES.sub(REDACTED, text)


class RedactingFilter(logging.Filter):
    def __init__(self, known: Callable[[], Iterable[str]] = lambda: ()) -> None:
        super().__init__()
        self._known = known

    def filter(self, record: logging.LogRecord) -> bool:
        # Format first, so a secret passed as an argument is caught as well.
        message = record.getMessage()
        cleaned = scrub(message, self._known())
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def install(known: Callable[[], Iterable[str]] = lambda: ()) -> RedactingFilter:
    """Attach to every handler on the root logger, and to the root logger itself."""
    f = RedactingFilter(known)
    root = logging.getLogger()
    root.addFilter(f)
    for handler in root.handlers:
        handler.addFilter(f)
    return f
