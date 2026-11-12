"""What an approval card shows in full, beside its one-line headline (SPN-2a).

The headline (`router.safe_summary`) is one line, cut to what a card's head can show.
The detail is everything the action sends or writes, exactly as it will go: every
recipient, the subject, the whole text. It keeps its line breaks, so it reads as
written, and it is never cut. What could not be shown unmistakably (a character
nobody sees, a direction override, a control character) or would not fit is refused
before any card, the way `folder_share` refuses a card that would be cut: a card that
shows part of what it does is a card for something else.

The detail is for the user's eyes on the card. It is never sent to the model, which
wrote the arguments it comes from.
"""

from __future__ import annotations

import unicodedata

import idna

from trillion.spine.lossless import CLOSE, OPEN

# The longest detail a card carries. Generous: a long email, a page of a document.
DETAIL_LIMIT = 20_000

TOO_LONG = (
    f"it is too long to show whole on a card (more than {DETAIL_LIMIT:,} characters), and "
    "a card never shows part of what it does: make it shorter, or split it in two"
)
UNSHOWABLE = (
    "it holds characters a card can't show as they are (an invisible character, a "
    "direction override or a control character), so it can't be approved from here: "
    "take them out and try again"
)

# Said in the description of every tool only a tap approves (SPN-14): what reaches
# other people, or can't be undone. A spoken or typed yes is refused for it.
ONLY_A_TAP = (
    "Only a tap on its card approves it, never a spoken or typed yes: ask the user to tap Approve."
)

# Variation selectors 15 and 16 only choose how the character before them is drawn
# (text or emoji); the zero-width joiner joins emoji into one.
_PRESENTATION = frozenset("\ufe0e\ufe0f")
_ZWJ = "\u200d"
# The zero-width non-joiner keeps two letters apart, as Persian and Indic words use it.
_ZWNJ = "\u200c"
_KEPT_CONTROLS = frozenset("\n\t")


def _emoji(c: str) -> bool:
    """A pictograph, or a skin tone that changes the one before it."""
    if len(c) != 1:
        return False
    o = ord(c)
    return unicodedata.category(c) == "So" or 0x1F000 <= o <= 0x1FAFF


def _hidden_at(text: str, i: int) -> bool:
    """Whether the character at `i` is one nobody sees that could make `text` read as
    something else (see `hidden_marks`)."""
    c = text[i]
    if c in _PRESENTATION:
        return False
    if c == _ZWNJ:
        # Only where it changes how letters join, by the rule domain names use
        # (RFC 5892 CONTEXTJ): after a virama, or between letters of a script that
        # joins. Anywhere else ("Cli\u200cents") it changes nothing anyone sees.
        return not idna.core.valid_contextj(text, i)
    if c == _ZWJ:
        before = text[i - 1] if i > 0 else ""
        if before in _PRESENTATION and i > 1:
            before = text[i - 2]
        after = text[i + 1] if i + 1 < len(text) else ""
        return not (_emoji(before) and _emoji(after))
    o = ord(c)
    return (
        unicodedata.category(c) == "Cf"
        or o == 0x034F
        or 0xFE00 <= o <= 0xFE0D
        or 0xE0100 <= o <= 0xE01EF
    )


def hidden_marks(text: str) -> bool:
    """Whether `text` holds a character nobody sees that could make it read as something
    else: a direction override or isolate, a zero-width space or joiner in a word, a
    soft hyphen, a tag or other variation selector. Emoji drawn as they should be (a
    variation selector, a joiner between two emoji) are not hidden: they are seen, and
    so is a non-joiner between two letters, which changes how they are written."""
    return any(_hidden_at(text, i) for i in range(len(text)))


def _lines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def unshowable(text: str) -> bool:
    """Whether `text` holds anything a card could not show as it is: a control character
    other than a line break or a tab, a line or paragraph separator, or a hidden mark."""
    for c in _lines(text):
        if c in _KEPT_CONTROLS:
            continue
        if unicodedata.category(c) in ("Cc", "Zl", "Zp"):
            return True
    return hidden_marks(text)


def card_detail(text: str) -> tuple[str, str | None]:
    """The detail as the card shows it, and None; or "" and why it can't be shown.

    Shown exactly as given, line breaks kept (a carriage return reads as one). Nothing
    is removed or replaced: anything that would have to be is refused instead."""
    shown = _lines(text)
    if len(shown) > DETAIL_LIMIT:
        return "", TOO_LONG
    if unshowable(shown):
        return "", UNSHOWABLE
    return shown, None


def _token(c: str) -> str:
    return f"{OPEN}U+{ord(c):04X}{CLOSE}"


def visible(text: str) -> str:
    """`text` with every character a card could not show written as a visible
    ⟦U+XXXX⟧ token, and the token opener itself too, so nothing in it is hidden or
    passes for a token. For a file's contents, which may hold such characters on
    purpose (a lossless round trip, SPN-6a); what goes to other people is refused
    instead."""
    text = _lines(text)
    out: list[str] = []
    for i, c in enumerate(text):
        if c == OPEN or (
            c not in _KEPT_CONTROLS
            and (unicodedata.category(c) in ("Cc", "Zl", "Zp") or _hidden_at(text, i))
        ):
            out.append(_token(c))
        else:
            out.append(c)
    return "".join(out)
