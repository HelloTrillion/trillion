"""Cut a streaming reply into sentences as it arrives (VOI-5, VOI-16).

Speech is synthesized one sentence at a time, so prosody never resets
mid-thought. The splitter is fed text as the model produces it and gives back
each sentence the moment it is certainly complete: a full stop is only a
boundary once the next character shows it was not "Dr.", "3.5" or "e.g.".
"""

from __future__ import annotations

import re

# Words that end in a full stop without ending a sentence.
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "no", "fig", "approx",
        "inc", "ltd", "co", "e.g", "i.e", "a.m", "p.m", "u.s", "u.k",
    }
)  # fmt: skip
_CLOSERS = "\"')]}\u201d\u2019"  # straight and curly closing quotes, brackets
# A terminator, any closing quotes or brackets, then whitespace: the earliest point at
# which a sentence can be known to have ended.
_BOUNDARY = re.compile(rf"([.!?…]+[{re.escape(_CLOSERS)}]*)(\s+)")
_PARAGRAPH = re.compile(r"\n\s*\n")
_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")
# Below this a "sentence" is a fragment ("Yes." "No."): it is spoken with the next one
# so the voice does not stop and start.
MIN_CHARS = 12
# A run with no punctuation at all is still spoken eventually.
MAX_CHARS = 400


def _is_abbreviation(head: str) -> bool:
    m = _WORD.search(head)
    if m is None:
        return False
    word = m.group(1).lower()
    return word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())  # initials: "J. Smith"


class SentenceSplitter:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        """Add text; return the sentences that are now complete."""
        self._buffer += text
        out: list[str] = []
        while True:
            cut = self._next_cut()
            if cut is None:
                break
            sentence, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        """The reply ended: whatever is left is the last sentence."""
        rest, self._buffer = self._buffer.strip(), ""
        return [rest] if rest else []

    def _next_cut(self) -> int | None:
        buf = self._buffer
        paragraph = _PARAGRAPH.search(buf)
        for m in _BOUNDARY.finditer(buf):
            if paragraph is not None and paragraph.start() < m.start():
                break
            end = m.end(1)
            # Whitespace must be followed by something: at the very end of the buffer
            # the next token may still turn "3." into "3.5" or "U." into "U.S.".
            if m.end() >= len(buf):
                return None
            head = buf[:end]
            if head.endswith(".") and not head.endswith(".."):
                if _is_abbreviation(head):
                    continue
                if head[-2:-1].isdigit() and buf[m.end()].isdigit():
                    continue  # "1. 2" in a list of numbers
            if len(head.strip()) < MIN_CHARS:
                continue
            return end
        if paragraph is not None and buf[: paragraph.start()].strip():
            return paragraph.end()
        if len(buf) > MAX_CHARS:
            space = buf.rfind(" ", 0, MAX_CHARS)
            return space + 1 if space > 0 else MAX_CHARS
        return None
