"""`trillion doctor --trace`: how long her last turns took, and where the time went (ONB-11).

It reads what each turn recorded (migration 007) and prints it: when the words were
ready, her first words on screen, her first audio, the moment it was heard, and the
reply saved, all counted from when the user finished. Then the medians against the
latency goals (PRD §1 success metrics, §7), which are a promise about a paid voice.

Measures, never repairs. Prints numbers only: nothing anyone said is in a trace.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from trillion.voice.catalog import listen_option, speech_option

# The goals, in seconds (PRD §1): a spoken reply heard within 1.2s at the median and
# 2.0s at p90 on a paid voice; the first words on screen within 0.6s.
SPOKEN_P50 = 1.2
SPOKEN_P90 = 2.0
TEXT_P50 = 0.6
# Fewer than this and a percentile says more about luck than about the build.
FIRM = 20

Row = Mapping[str, Any]


def percentile(values: Sequence[float], p: float) -> float:
    """The nearest-rank percentile: a value that really occurred."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def _s(ms: object) -> str:
    return f"{ms / 1000:.2f}" if isinstance(ms, int) else "-"


def _n(count: int) -> str:
    return f"{count:,}"


def heard(row: Row) -> tuple[int | None, bool]:
    """When her reply was heard, and whether that was measured at the page. Without
    the page's report, the first audio sent is the nearest thing, and is marked."""
    if isinstance(row.get("played_ms"), int):
        return row["played_ms"], True
    sent = row.get("first_audio_ms")
    return (sent, False) if isinstance(sent, int) else (None, False)


def _cached(calls: Sequence[Mapping[str, Any]]) -> str:
    read = sum(int(c.get("cache_read") or 0) for c in calls)
    total = sum(
        int(c.get("input") or 0) + int(c.get("cache_read") or 0) + int(c.get("cache_write") or 0)
        for c in calls
    )
    return f"{_n(read)} ({round(100 * read / total)}%)" if total else "-"


def _tokens(calls: Sequence[Mapping[str, Any]]) -> str:
    sent = sum(int(c.get("input") or 0) + int(c.get("cache_read") or 0) for c in calls)
    sent += sum(int(c.get("cache_write") or 0) for c in calls)
    out = sum(int(c.get("output") or 0) for c in calls)
    return f"{_n(sent)} / {_n(out)}"


def _table(rows: Sequence[Row], when: Callable[[int], str]) -> list[str]:
    head = (
        *("when", "how", "tools", "draft", "words", "text", "audio", "heard", "done"),
        *("tokens in / out", "cached", "model"),
    )
    body: list[tuple[str, ...]] = []
    for r in rows:
        calls = r.get("calls") or []
        at, measured = heard(r)
        body.append(
            (
                when(int(r["created_at"])),
                str(r["via"]) + ("*" if r.get("first_in_session") else ""),
                str(r["tools"]) if r["tools"] else "-",
                # Whether a draft started early was used or let go (VOI-12).
                d if (d := str(r.get("draft") or "none")) != "none" else "-",
                _s(r.get("heard_ms")),
                _s(r.get("first_text_ms")),
                _s(r.get("first_audio_ms") or r.get("first_sentence_ms")),
                _s(at) + ("" if measured or at is None else "~"),
                _s(r.get("saved_ms")),
                _tokens(calls),
                _cached(calls),
                str(r["model"]) + ("" if r["stop"] == "end" else f" ({r['stop']})"),
            )
        )
    widths = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(head)]
    numeric = {2, 4, 5, 6, 7, 8, 9, 10}

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            c.rjust(widths[i]) if i in numeric else c.ljust(widths[i]) for i, c in enumerate(cells)
        ).rstrip()

    out = [line(head)]
    for r, b in zip(rows, body, strict=True):
        out.append(line(b))
        if (cued := _cues(r.get("cues"))) is not None:
            out.append(cued)
        calls = r.get("calls") or []
        if len(calls) > 1:
            for i, c in enumerate(calls, 1):
                out.append(
                    f"      call {i}: started {_s(c.get('start_ms'))}s, first token "
                    f"{_s(c.get('first_token_ms'))}s, done {_s(c.get('end_ms'))}s, "
                    f"{_tokens([c])} tokens, {_cached([c])} cached"
                )
    return out


def _cues(cues: object) -> str | None:
    """A spoken turn's listening cues (PER-11): sent, played, and why the recognizer's
    other reports got none, most common first."""
    if not isinstance(cues, Mapping):
        return None
    skipped = cues.get("skipped") or {}
    why = ", ".join(
        f"{k} {n}" for k, n in sorted(skipped.items(), key=lambda kv: (-int(kv[1]), kv[0]))
    )
    said = f"      cues: {cues.get('sent', 0)} sent, {cues.get('played', 0)} played"
    return said + (f"; none: {why}" if why else "")


def _free(turns: Sequence[Row]) -> list[str]:
    """The free voices and ears these turns used: the goal is a promise about paid ones."""
    out: set[str] = set()
    for r in turns:
        voice = speech_option(str(r.get("voice"))) if r.get("voice") else None
        ears = listen_option(str(r.get("ears"))) if r.get("ears") else None
        out |= {str(r["voice"])} if voice is not None and voice.free else set()
        out |= {str(r["ears"])} if ears is not None and ears.free else set()
    return sorted(out)


def _spoken(rows: Sequence[Row]) -> list[str]:
    """Spoken turns with no tool, after the first of a session: what the goal is about."""
    turns = [
        r
        for r in rows
        if r["via"] == "voice"
        and not r["tools"]
        and not r.get("first_in_session")
        and r["stop"] == "end"
        and heard(r)[1]
    ]
    if not turns:
        return [
            "Spoken replies: none measured yet. Talk to her a few times with her voice on,",
            "  then run this again.",
        ]
    values = [heard(r)[0] / 1000 for r in turns]  # type: ignore[operator]
    p50, p90 = percentile(values, 0.5), percentile(values, 0.9)
    voices = sorted({str(r["voice"]) for r in turns if r.get("voice")})
    ears = sorted({str(r["ears"]) for r in turns if r.get("ears")})
    met = p50 <= SPOKEN_P50 and p90 <= SPOKEN_P90
    verdict = "met" if met else "missed"
    free = _free(turns)
    if free:
        verdict += f" (the goal is for paid voice and ears; {', '.join(free)} is free)"
    out = [
        f"Spoken replies without tools: {len(turns)} turns, ears {', '.join(ears) or '-'}, "
        f"voice {', '.join(voices) or '-'}",
        f"  you stopped -> heard her   p50 {p50:.2f}s  p90 {p90:.2f}s"
        f"   goal {SPOKEN_P50}s / {SPOKEN_P90}s: {verdict}",
    ]
    parts = [
        ("waiting for quiet", [r.get("endpoint_ms") for r in turns]),
        ("words ready", [r.get("heard_ms") for r in turns]),
        ("first token", [_first_token(r) for r in turns]),
        ("first sentence", [r.get("first_sentence_ms") for r in turns]),
        ("first audio", [r.get("first_audio_ms") for r in turns]),
        ("heard", [heard(r)[0] for r in turns]),
    ]
    steps = [
        f"{name} {percentile([v / 1000 for v in vals if isinstance(v, int)], 0.5):.2f}"
        for name, vals in parts
        if any(isinstance(v, int) for v in vals)
    ]
    out.append("  by then, at the median: " + " · ".join(steps))
    out.append("  (each step counts from when you stopped talking)")
    if len(turns) < FIRM:
        out.append(f"  Only {len(turns)} so far: the numbers firm up after about {FIRM}.")
    return out


def _first_token(row: Row) -> int | None:
    calls = row.get("calls") or []
    first = calls[0].get("first_token_ms") if calls else None
    return first if isinstance(first, int) else None


def _typed(rows: Sequence[Row]) -> list[str]:
    turns = [
        r
        for r in rows
        if r["via"] == "text" and not r.get("first_in_session") and r.get("first_text_ms")
    ]
    if not turns:
        return []
    values = [int(r["first_text_ms"]) / 1000 for r in turns]
    p50 = percentile(values, 0.5)
    verdict = "met" if p50 <= TEXT_P50 else "missed"
    return [
        f"Typed replies: {len(turns)} turns",
        f"  sent -> first words on screen   p50 {p50:.2f}s   goal {TEXT_P50}s: {verdict}",
    ]


def report(rows: Sequence[Row], *, local: bool = True) -> str:
    """The trace, as text. `rows` oldest first, as `inspect.timings` returns them."""
    if not rows:
        return (
            "No turns recorded yet. Talk to her, then run: trillion doctor --trace\n"
            "(Turns are timed from this version on.)\n"
        )

    def when(ms: int) -> str:
        at = datetime.fromtimestamp(ms / 1000)
        return at.strftime("%H:%M:%S") if local else at.isoformat(timespec="seconds")

    cold = sum(1 for r in rows if r.get("first_in_session"))
    lines = [
        f"Her last {len(rows)} turn{'s' if len(rows) != 1 else ''}, oldest first. Seconds from "
        "when you stopped talking (spoken) or sent the message (typed).",
        "",
        *_table(rows, when),
        "",
        "words: what you said was ready · text: her first words on screen · audio: her first "
        "audio sent",
        "heard: when it reached your speaker (~ where the page did not say, so the audio sent "
        "stands in)",
        "draft: whether her answer started while you were finishing was used, or let go",
        "* the first turn after the page connected, when connections are still cold",
        "",
        *_spoken(rows),
    ]
    typed = _typed(rows)
    if typed:
        lines += ["", *typed]
    if cold:
        lines += ["", f"{cold} first-of-session turn{'s' if cold != 1 else ''} left out of the "
                  "medians."]  # fmt: skip
    return "\n".join(lines) + "\n"
