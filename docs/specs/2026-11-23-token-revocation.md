# Signing out every other page

**Date:** 2026-11-23 · **Requirements:** SPN-11, SPN-11a, SEC-11 · **Builds on:** [security alerts](2026-11-07-security-alerts.md)

## Why

The session token is made at each launch, held only in memory and shared by every page on this computer. Nothing could revoke it before the server stopped. A link that leaks (terminal scrollback, a screenshot, shell history, a shared screen) stayed good for the whole launch, and the only fix was to quit Trillion. `SECURITY.md` listed this as required before the first stable release.

## Decisions

1. **Rotate, don't track pages.** One button replaces the token: Settings → Devices → **Sign out every other page**. Every page shares one token by design, and rotation is what makes a leaked link worthless. Signing out one named page would need a token per page; that can come later if it is ever needed.
2. **Asked over the page's own socket.** The page sends `session.rotate` on `/ws`. The socket that asked is the page that keeps working, so it can never sign itself out by accident, and a socket that never signed in can't ask. It is answered with `session.token{token}` on that socket only, and the page keeps the token in its tab's session storage, as it keeps the first one.
3. **The swap is first and whole.** A fresh token (`secrets.token_urlsafe(32)`) replaces `rt.token` before anything else is awaited. Every request and every socket hello checks against `rt.token` when it comes, so from that moment the old token gets 401 on the API and 4401 on a socket's hello.
4. **Every other socket is told and closed.** Each socket that signed in (`/ws`, `/ws/observe`, `/ws/browser`, any plugged-in one through `admit`) is kept in a weak set on the app. On rotation every one but the asker is sent `error{code: "signed_out"}` and closed with 4401. A signed-out page says "Signed out. Open Trillion again from the app, or from the link in its terminal." and does not retry, so it adds no refusals for the security alerts to count. Her browser's window reads the token each time it connects, so the asking page's own window reconnects on the new one.
5. **Who is told.** `trillion run` (not started by the desktop app) prints a fresh `open:` line in its terminal, as it did at launch. A security notice says every other page was signed out and how many, so a press nobody meant is seen. The token is never logged.
6. **Two presses.** Each rotation makes a new token; whoever pressed last holds the only one that works.

## Known limits

Whoever holds a leaked token can press the button first and sign the owner out. They could already do anything the owner can; quitting and restarting Trillion makes a new token. The desktop app checks its server with the token only at start, so a rotation never trips that check. The floating orb holds no token.

## Tests

- **Core** (`tests/api/test_sign_out.py`, `tests/delivery/test_security_alerts.py`): the asker gets a new token and stays signed in; the old token gets 401 on the API and is refused on `/ws` and `/ws/observe`; every other socket is told `signed_out` and closed with 4401; two rotations leave exactly one working token; a socket that never signed in can't rotate; the hooks hear the new token and the token never reaches a log; the security notice is told once with the count.
- **Web** (`apps/web/test/sign-out.test.ts`): the button asks once more and Cancel sends nothing; a page that isn't connected says so; the confirmation line after the new token; a `signed_out` error makes the page signed out, with "Signed out" in its status.
- **Live**, on a throwaway install: two tabs signed in, the button pressed in one; the other said it was signed out, the terminal printed a new link, the old token got 401 and the asking tab kept working.
