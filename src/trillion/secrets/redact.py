"""Scrub credentials from every log record before it is written (SEC-5).

A mistake in a future logging statement must not be able to leak a key. Two
nets: the shapes of keys we know, and the exact values currently in use, which
catches a credential whose format nobody anticipated.

The scrub is installed in the record factory, the one place every record passes
through whichever logger made it and whichever handler writes it. (A filter on
the root logger does not see records from other loggers, and handlers added
later would not have it.)
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterable
from typing import Any

REDACTED = "[redacted]"
# Provider key shapes: Anthropic and OpenAI style `sk-…`, and bearer tokens in headers.
_SHAPES = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}|(?i:bearer)\s+[A-Za-z0-9_\-\.=]{16,}")
_MIN_KNOWN = 8  # a shorter "secret" would redact ordinary words
_busy = threading.local()


def scrub(text: str, known: Iterable[str] = ()) -> str:
    for value in known:
        if len(value) >= _MIN_KNOWN and value in text:
            text = text.replace(value, REDACTED)
    return _SHAPES.sub(REDACTED, text)


def _clean(record: logging.LogRecord, known: Callable[[], Iterable[str]]) -> None:
    # Looking up the known values can itself log (a keychain that fails). That record
    # is scrubbed by shape only, instead of recursing.
    if getattr(_busy, "on", False):
        values: Iterable[str] = ()
    else:
        _busy.on = True
        try:
            values = list(known())
        except Exception:
            values = ()
        finally:
            _busy.on = False
    try:
        message = record.getMessage()  # format first: a secret passed as an argument counts
    except Exception:
        return
    cleaned = scrub(message, values)
    if cleaned != message:
        record.msg, record.args = cleaned, None


class RedactingFilter(logging.Filter):
    """The same scrub as a filter, for a single logger or handler."""

    def __init__(self, known: Callable[[], Iterable[str]] = lambda: ()) -> None:
        super().__init__()
        self._known = known

    def filter(self, record: logging.LogRecord) -> bool:
        _clean(record, self._known)
        return True


def install(known: Callable[[], Iterable[str]] = lambda: ()) -> Callable[[], None]:
    """Scrub every log record made in this process from now on. Returns an undo."""
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        _clean(record, known)
        return record

    logging.setLogRecordFactory(factory)
    return lambda: logging.setLogRecordFactory(previous)
