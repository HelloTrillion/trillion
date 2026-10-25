"""Board edits and the one-line summary she is given each turn (D3, D5, D7).

Pure: every edit takes a board (or None) and returns a new one, leaving the given
board as it was, so a refused change leaves nothing half done. A board is the JSON
the page draws, plus `next_id`, the counter that keeps ids from being reused:

    {"title": "October spend", "open": true, "next_id": 3, "widgets": [{"id": "w1", ...}]}
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any, Literal

from trillion.workspace.kit import MAX_WIDGETS, TITLE, BoardError, Widget, cap, validate_widget

__all__ = ["Board", "BoardError", "close", "describe", "remove", "show", "summary", "update"]

Board = dict[str, Any]
Mode = Literal["replace", "add"]

# How long a title may run in her context line; the board itself has the rest.
CLIP = 40


def _copy(board: Board | None) -> Board:
    if board is None:
        return {"title": "", "open": False, "next_id": 1, "widgets": []}
    return copy.deepcopy(board)


def _index(board: Board | None, widget_id: Any) -> int:
    for i, widget in enumerate(board["widgets"] if board else []):
        if widget["id"] == widget_id:
            return i
    raise BoardError(f"there is no widget {widget_id} on the board.")


def show(
    board: Board | None, title: str | None, widgets: Sequence[Any], mode: str = "replace"
) -> Board:
    """Put widgets up and open the board. `replace` clears what was there; `add`
    appends. No widgets reopens the board as it was."""
    if mode not in ("replace", "add"):
        raise BoardError(f"mode must be replace or add, not {mode!r}.")
    if not isinstance(widgets, list):
        raise BoardError("widgets must be a list.")
    out = _copy(board)
    if not widgets and not out["widgets"]:
        raise BoardError("there is nothing on the board to show; give it widgets.")
    kept = out["widgets"] if mode == "add" or not widgets else []
    total = len(kept) + len(widgets)
    if total > MAX_WIDGETS:
        raise BoardError(
            f"that makes {total} widgets; a board holds {MAX_WIDGETS} at most. "
            "Remove some or replace them."
        )
    fresh = [validate_widget(raw, i) for i, raw in enumerate(widgets, 1)]
    for widget in fresh:
        widget_id = f"w{out['next_id']}"
        out["next_id"] += 1
        kept.append({"id": widget_id, **widget})
    out["widgets"] = kept
    if title is not None:
        if not isinstance(title, str):
            raise BoardError("title must be a string.")
        out["title"] = cap(title, TITLE)
    out["open"] = True
    return out


def update(board: Board | None, widget_id: str, widget: Any) -> Board:
    """Replace one widget's content, keeping its id and position. The board opens:
    she changed it so the user would see it."""
    i = _index(board, widget_id)
    out = _copy(board)
    out["widgets"][i] = {"id": widget_id, **validate_widget(widget, widget_id)}
    out["open"] = True
    return out


def remove(board: Board | None, ids: Sequence[str] | Literal["all"]) -> Board:
    """Take widgets off. Taking the last one off closes the board."""
    out = _copy(board)
    if ids == "all":
        out["widgets"] = []
    else:
        if isinstance(ids, str) or not ids:
            raise BoardError('ids must be a list of widget ids, or "all".')
        for widget_id in ids:
            _index(out, widget_id)
        out["widgets"] = [w for w in out["widgets"] if w["id"] not in ids]
    if not out["widgets"]:
        out["open"] = False
    return out


def close(board: Board | None) -> Board:
    """Close the board and keep it."""
    if board is None:
        raise BoardError("there is no board to close.")
    out = _copy(board)
    out["open"] = False
    return out


def _clip(text: str) -> str:
    return text if len(text) <= CLIP else text[: CLIP - 1] + "…"


def describe(widget: Widget, quote: str = '"') -> str:
    """`w2 line chart "Daily spend"`: how a widget is named to her."""
    kind = f"{widget['kind']} chart" if widget["type"] == "chart" else widget["type"]
    name = widget.get("label") if widget["type"] == "stat" else widget.get("title")
    named = f" {quote}{_clip(name)}{quote}" if name else ""
    return f"{widget['id']} {kind}{named}"


def summary(board: Board | None) -> str:
    """One line for her context, or "" with no board."""
    if board is None:
        return ""
    state = "open" if board["open"] else "closed"
    title = f' "{_clip(board["title"])}":' if board["title"] else ""
    shown = ", ".join(describe(w) for w in board["widgets"]) or "empty"
    return f"Board ({state}):{title} {shown}."
