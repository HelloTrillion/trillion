"""Strip a local model's chat-template tokens from her streamed words.

Some servers pass a model's own control tokens through as text, such as gemma's
`<channel|>` or `<end_of_turn>`, ChatML's `<|im_end|>` or Llama's `<|eot_id|>`.
They are never hers to say, show or save. A token can be split across two
deltas, so a tail that could still become one is held back until it is clear.

Only forms with a pipe beside a bare name, plus gemma's two named turn markers,
count as tokens. Ordinary angle brackets (`<div>`, `a < b`, `List<T>`, `<|>`)
pass through untouched.
"""

from __future__ import annotations

import re

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
TOKEN = re.compile(
    rf"<\|{_NAME}\|?>"  # <|im_end|>, <|channel>
    rf"|<{_NAME}\|>"  # <channel|>
    r"|<(?:start|end)_of_turn>"
)
# A tail that a later delta could still finish as a token.
_PARTIAL = re.compile(rf"<(?:\|(?:{_NAME}\|?)?|{_NAME}\|?)?")
# Longer than any real token: past this the tail is just text.
_HOLD_MAX = 48


class ControlTokenFilter:
    """Feed streamed text in; get it back without control tokens."""

    def __init__(self) -> None:
        self._held = ""

    def feed(self, text: str) -> str:
        buf = TOKEN.sub("", self._held + text)
        at = buf.rfind("<")
        if at >= 0 and len(buf) - at <= _HOLD_MAX and _PARTIAL.fullmatch(buf, at):
            self._held = buf[at:]
            return buf[:at]
        self._held = ""
        return buf

    def flush(self) -> str:
        """What is left at the end of the reply: an unfinished bracket is just text."""
        out, self._held = self._held, ""
        return out
