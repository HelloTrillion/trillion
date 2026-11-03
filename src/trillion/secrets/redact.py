"""Scrub credentials from every log record before it is written (SEC-5).

The message, any traceback and any stack text are all scrubbed.

A mistake in a future logging statement must not be able to leak a key. Two
nets: the shapes of keys we know, and the exact values currently in use, which
catches a credential whose format nobody anticipated.

The scrub is installed in the record factory, the one place every record passes
through whichever logger made it and whichever handler writes it. (A filter on
the root logger does not see records from other loggers, and handlers added
later would not have it.)
"""

from __future__ import annotations

import functools
import logging
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any

REDACTED = "[redacted]"
# Key shapes providers issue. Formats with no prefix (Deepgram's 40 hex, older
# ElevenLabs 32 hex) look like ordinary ids and hashes, so only known values catch them.
_SHAPES = re.compile(
    "|".join(
        (
            r"\bsk-[A-Za-z0-9_\-]{16,}",  # Anthropic, OpenAI
            # An Authorization header. A digit is required, so prose is left alone.
            r"(?i:bearer)\s+(?=[A-Za-z0-9_\-\.=]*\d)[A-Za-z0-9_\-\.=]{16,}",
            r"\bxox(?:e\.xox)?[abeoprs]-[A-Za-z0-9\-]{10,}",  # Slack bot, user, rotating
            r"\bxapp-[A-Za-z0-9\-]{10,}",  # Slack app-level
            r"\bya29\.[A-Za-z0-9_\-\.]{20,}",  # Google access
            r"\b1//[A-Za-z0-9_\-]{20,}",  # Google refresh
            r"\bAIza[A-Za-z0-9_\-]{35}",  # Google API key
            r"\bGOCSPX-[A-Za-z0-9_\-]{20,}",  # Google OAuth client secret
            r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}",  # Stripe secret, restricted
            r"\bwhsec_[A-Za-z0-9]{24,}",  # Stripe webhook signing
            r"\bsk_[A-Za-z0-9]{32,}",  # ElevenLabs
            r"(?<![0-9])\d{6,12}:[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])",  # Telegram bot
            r"\bgh[pousr]_[A-Za-z0-9]{36,}",  # GitHub classic and app
            r"\bgithub_pat_[A-Za-z0-9_]{22,}",  # GitHub fine-grained
            r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",  # a JWT
            r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",  # AWS access key id
        )
    )
)
# Environment variables whose names say they hold a credential.
_ENV_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD)$")
_MIN_KNOWN = 8  # a shorter "secret" would redact ordinary words
_busy = threading.local()


def env_secrets(env: Mapping[str, str]) -> list[str]:
    """Values of environment variables named like credentials."""
    return [v for k, v in env.items() if v and _ENV_NAME.search(k.upper())]


@functools.lru_cache(maxsize=16)
def _longest_first(values: tuple[str, ...]) -> tuple[str, ...]:
    # Longest first, so a value holding a shorter one is removed whole. Cached: the
    # same values come with record after record.
    kept = {v for v in values if len(v) >= _MIN_KNOWN}
    return tuple(sorted(kept, key=len, reverse=True))


def scrub(text: str, known: Iterable[str] = ()) -> str:
    for value in _longest_first(tuple(known)):
        if value in text:
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
    # A traceback is formatted once and kept on the record; handlers print that text.
    if record.exc_info and not record.exc_text:
        try:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        except Exception:
            record.exc_text = "(the exception could not be formatted)"
    if record.exc_text:
        record.exc_text = scrub(record.exc_text, values)
    if record.stack_info:
        record.stack_info = scrub(record.stack_info, values)


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
