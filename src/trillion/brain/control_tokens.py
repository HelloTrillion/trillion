"""Strip a local model's chat-template tokens from her streamed words.

Some servers pass a model's own control tokens through as text, such as gemma's
`<channel|>` or `<end_of_turn>`, ChatML's `<|im_end|>`, Llama's `<|eot_id|>` or
DeepSeek's end▁of▁sentence between full-width bars. They are never hers to say,
show or save.

Two of gemma's carry text that is not hers either: a thinking span, opened by
`<|channel>thought` and closed by `<channel|>`, goes whole, and a turn marker
takes its role line (`model`, `user` or `system`) with it. A `<|channel>` not
followed by `thought` goes alone.

`<eos>` and `<bos>` go only at the very start or end of a reply: mid-reply they
may be her words about a tokenizer.

A token can be split across deltas, so a tail that could still become one is
held back until it is clear. Only pipe forms, full-width bar forms and a few
exact names count as tokens: ordinary angle brackets (`<div>`, `<s>`, `a < b`,
`List<T>`, `<|>`) pass through untouched.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
# DeepSeek's markers sit between full-width bars (U+FF5C), with a block for spaces.
_BAR = "\uff5c"
_WIDE = rf"[^{_BAR}<>\s]"
TOKEN = re.compile(
    rf"<\|{_NAME}\|?>"  # <|im_end|>, <|channel>
    rf"|<{_NAME}\|>"  # <channel|>
    rf"|<{_BAR}{_WIDE}{{1,64}}{_BAR}>"  # DeepSeek's end▁of▁sentence
    r"|<(?:start|end)_of_turn>"
)
# A tail that a later delta could still finish as a token.
_PARTIAL = re.compile(rf"<(?:\|(?:{_NAME}\|?)?|{_NAME}\|?|{_BAR}{_WIDE}*{_BAR}?)?")
# Longer than any real token: past this the tail is just text.
_HOLD_MAX = 72

_EDGE = ("<bos>", "<eos>")
# An edge marker with only whitespace after it: the reply may be ending.
_EDGE_TAIL = re.compile(r"<[eb]os>\s*\Z")
_THINK_OPEN = "<|channel>"
_THINK_WORD = ("thought\n", "thought\r\n")
_THINK_CLOSE = "<channel|>"
_TURN_OPEN = ("<start_of_turn>", "<|turn>")
_ROLES = tuple(f"{role}{end}" for role in ("model", "user", "system") for end in ("\n", "\r\n"))


def _tail_starting(buf: str, marker: str) -> str:
    """The end of `buf` that could be the start of `marker`, or ''."""
    for k in range(min(len(marker) - 1, len(buf)), 0, -1):
        if marker.startswith(buf[-k:]):
            return buf[-k:]
    return ""


def _could_become(buf: str, words: tuple[str, ...]) -> bool:
    return any(w.startswith(buf) for w in words)


class ControlTokenFilter:
    """Feed streamed text in; get it back without control tokens."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._held = ""
        self._started = False  # something of hers has gone out
        self._channel = False  # just after <|channel>: her thinking may follow
        self._thinking = False  # inside a thinking span: dropped
        self._dropped = 0  # characters of the open thinking span dropped so far
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
                    self._dropped += len(buf) - len(self._held)
                    break
                buf, self._thinking, self._dropped = buf[end + len(_THINK_CLOSE) :], False, 0
                continue
            if self._channel:
                if _could_become(buf, _THINK_WORD):
                    self._held = buf
                    break
                # Her thinking, dropped up to its close; or a stray marker, gone alone.
                self._thinking = any(buf.startswith(w) for w in _THINK_WORD)
                self._channel = False
                continue
            if self._header:
                if _could_become(buf, _ROLES):
                    self._held = buf  # still could be a role line
                    break
                role = next((r for r in _ROLES if buf.startswith(r)), "")
                buf, self._header = buf[len(role) :], False
                continue
            if not self._started and not "".join(out).strip():
                lead = buf.lstrip()
                if lead and _could_become(lead, _EDGE) and len(lead) < len(_EDGE[0]):
                    self._held = buf
                    break
                if lead.startswith(_EDGE):
                    buf = lead[len(_EDGE[0]) :]
                    continue
            m = TOKEN.search(buf)
            if m:
                before = buf[: m.start()]
                # An edge marker against a control token is not her words either.
                edge = _EDGE_TAIL.search(before)
                out.append(before[: edge.start()] if edge else before)
                buf = buf[m.end() :]
                self._channel = m.group() == _THINK_OPEN
                self._header = m.group() in _TURN_OPEN
                continue
            edge = _EDGE_TAIL.search(buf)
            at = buf.rfind("<")
            if edge:
                self._held, buf = buf[edge.start() :], buf[: edge.start()]
            elif at >= 0 and len(buf) - at <= _HOLD_MAX and _PARTIAL.fullmatch(buf, at):
                self._held, buf = buf[at:], buf[:at]
            out.append(buf)
            break
        said = "".join(out)
        if said.strip():
            self._started = True
        return said

    def flush(self) -> str:
        """The end of the reply: an open thinking span, a bare role line or a last
        edge marker is dropped, and an unfinished bracket is just text."""
        out = self._held
        if self._thinking:
            logger.warning(
                "reply ended inside a thinking span: %d characters dropped",
                self._dropped + len(out),
            )
            out = ""
        elif (self._header and _could_become(out, _ROLES)) or _EDGE_TAIL.fullmatch(out):
            out = ""
        self._reset()
        return out
