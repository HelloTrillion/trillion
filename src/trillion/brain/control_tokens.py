"""Strip a local model's chat-template tokens from her streamed words.

Some servers pass a model's own control tokens through as text, such as gemma's
`<channel|>` or `<end_of_turn>`, ChatML's `<|im_end|>`, Llama's `<|eot_id|>` or
DeepSeek's end▁of▁sentence between full-width bars. They are never hers to say, show or save.

Two of gemma's carry text that is not hers either: a thinking span from
`<|channel>` to `<channel|>` goes whole, and a turn marker takes its role line
(`model`, `user` or `system`) with it.

A token can be split across deltas, so a tail that could still become one is
held back until it is clear. Only pipe forms, full-width bar forms and a few
exact names count as tokens: ordinary angle brackets (`<div>`, `a < b`,
`List<T>`, `<|>`) pass through untouched.
"""

from __future__ import annotations

import re

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
# DeepSeek's markers sit between full-width bars (U+FF5C), with a block for spaces.
_BAR = "\uff5c"
_WIDE = rf"[^{_BAR}<>\s]"
TOKEN = re.compile(
    rf"<\|{_NAME}\|?>"  # <|im_end|>, <|channel>
    rf"|<{_NAME}\|>"  # <channel|>
    rf"|<{_BAR}{_WIDE}{{1,64}}{_BAR}>"  # DeepSeek's end▁of▁sentence
    r"|<(?:start|end)_of_turn>"
    r"|</?s>|<[eb]os>"
)
# A tail that a later delta could still finish as a token.
_PARTIAL = re.compile(rf"<(?:\|(?:{_NAME}\|?)?|{_NAME}\|?|/s?|{_BAR}{_WIDE}*{_BAR}?)?")
# Longer than any real token: past this the tail is just text.
_HOLD_MAX = 72

_THINK_OPEN = "<|channel>"
_THINK_CLOSE = "<channel|>"
_TURN_OPEN = ("<start_of_turn>", "<|turn>")
_ROLES = ("model\n", "user\n", "system\n")


def _tail_starting(buf: str, marker: str) -> str:
    """The end of `buf` that could be the start of `marker`, or ''."""
    for k in range(min(len(marker) - 1, len(buf)), 0, -1):
        if marker.startswith(buf[-k:]):
            return buf[-k:]
    return ""


class ControlTokenFilter:
    """Feed streamed text in; get it back without control tokens."""

    def __init__(self) -> None:
        self._held = ""
        self._thinking = False  # inside a thinking span: dropped
        self._header = False  # just after a turn marker: a role line may follow

    def feed(self, text: str) -> str:
        buf, self._held = self._held + text, ""
        out: list[str] = []
        while buf:
            if self._thinking:
                end = buf.find(_THINK_CLOSE)
                if end < 0:
                    # Dropped, but the closing marker may be arriving in pieces.
                    self._held = _tail_starting(buf, _THINK_CLOSE)
                    break
                buf, self._thinking = buf[end + len(_THINK_CLOSE) :], False
                continue
            if self._header:
                if any(role.startswith(buf) for role in _ROLES):
                    self._held = buf  # still could be a role line
                    break
                role = next((r for r in _ROLES if buf.startswith(r)), "")
                buf, self._header = buf[len(role) :], False
                continue
            m = TOKEN.search(buf)
            if m:
                out.append(buf[: m.start()])
                buf = buf[m.end() :]
                self._thinking = m.group() == _THINK_OPEN
                self._header = m.group() in _TURN_OPEN
                continue
            at = buf.rfind("<")
            if at >= 0 and len(buf) - at <= _HOLD_MAX and _PARTIAL.fullmatch(buf, at):
                self._held = buf[at:]
                buf = buf[:at]
            out.append(buf)
            break
        return "".join(out)

    def flush(self) -> str:
        """The end of the reply: an open thinking span or a bare role line is dropped,
        and an unfinished bracket is just text."""
        out = self._held
        if self._thinking or (self._header and any(r.startswith(out) for r in _ROLES)):
            out = ""
        self._held, self._thinking, self._header = "", False, False
        return out
