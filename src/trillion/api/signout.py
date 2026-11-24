"""Signing out every other page (SPN-11a, the token revocation spec).

The session token is one per launch, shared by every page on this computer. If its
link leaks (terminal scrollback, a screenshot, shell history), the owner presses
**Sign out every other page** in Settings → Devices. That page's socket sends
`session.rotate`, and here:

1. a fresh token replaces the old one at once, so every request and socket hello
   after this checks against the new one (`app.py` reads `rt.token` each time);
2. the page that asked is sent the new token on its own socket, and keeps working;
3. every other socket that signed in is told `signed_out` and closed with 4401;
4. whoever needs to hear of it is told: `trillion run` prints a fresh link, and a
   security notice says every other page was signed out.

The token itself is never logged. Whoever holds the old token and presses first
signs the owner out instead; they could already do anything the owner can, and a
restart makes a new token.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
from collections.abc import Iterable
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

CLOSE_SIGNED_OUT = 4401
SIGNED_OUT = "signed_out"


def new_token() -> str:
    return secrets.token_urlsafe(32)


async def sign_out_others(rt: Any, sockets: Iterable[WebSocket], keep: WebSocket) -> str:
    """Rotate the token, close every signed-in socket but `keep`, and return the new
    token. The swap comes first, before any await, so no request slips through on the
    old one while the others are being closed."""
    token = new_token()
    rt.token = token
    others = [s for s in list(sockets) if s is not keep]
    for s in others:
        # A socket already gone has nothing to be told.
        with contextlib.suppress(Exception):
            await s.send_text(json.dumps({"type": "error", "code": SIGNED_OUT}))
        with contextlib.suppress(Exception):
            await s.close(code=CLOSE_SIGNED_OUT)
    logger.info("security: signed out %d other page(s)", len(others))
    for hook in list(getattr(rt, "token_hooks", ())):
        try:
            hook(token)
        except Exception as e:
            logger.warning("token hook failed: %s", type(e).__name__)
    if getattr(rt, "security", None) is not None:
        try:
            rt.security.signed_out_others(len(others))
        except Exception as e:
            logger.warning("sign-out not told: %s", type(e).__name__)
    return token
