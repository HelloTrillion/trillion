"""`trillion doctor --trace`: what it prints from the timings (ONB-11)."""

from __future__ import annotations

from typing import Any

from trillion.cli import trace


def row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "created_at": 1_760_000_000_000,
        "via": "voice",
        "first_in_session": 0,
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "voice": "elevenlabs",
        "ears": "deepgram",
        "stop": "end",
        "tools": 0,
        "endpoint_ms": 400,
        "heard_ms": 600,
        "first_text_ms": 900,
        "first_sentence_ms": 950,
        "first_audio_ms": 1050,
        "played_ms": 1100,
        "saved_ms": 3000,
        "calls": [
            {
                "start_ms": 610,
                "first_token_ms": 880,
                "end_ms": 2900,
                "input": 100,
                "output": 40,
                "cache_read": 900,
                "cache_write": 0,
            }
        ],
    }
    return base | over


def test_nothing_recorded_says_how_to_get_something() -> None:
    assert "No turns recorded yet" in trace.report([])


def test_the_percentile_is_a_value_that_occurred() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 10.0]
    assert trace.percentile(values, 0.5) == 3.0
    assert trace.percentile(values, 0.9) == 10.0
    assert trace.percentile([0.7], 0.9) == 0.7


def test_each_turn_shows_its_steps_tokens_and_cache() -> None:
    out = trace.report([row()])
    line = next(ln for ln in out.splitlines() if ln.lstrip().startswith(("0", "1", "2")))
    for cell in ("voice", "0.60", "0.90", "1.05", "1.10", "3.00", "1,000 / 40", "900 (90%)"):
        assert cell in line, cell
    assert "claude-sonnet-5" in line


def test_spoken_turns_are_judged_against_the_goal() -> None:
    fast = [row(played_ms=1000 + i * 10) for i in range(20)]
    out = trace.report(fast)
    assert "p50 1.09s" in out and "goal 1.2s / 2.0s: met" in out
    assert "firm up" not in out  # twenty is enough to go on

    slow = [row(played_ms=2500) for _ in range(5)]
    out = trace.report(slow)
    assert "missed" in out and "Only 5 so far" in out


def test_free_voice_or_ears_are_told_the_goal_is_for_paid_ones() -> None:
    assert "goal is for paid voice and ears; system is free" in trace.report([row(voice="system")])
    assert "whisper is free" in trace.report([row(ears="whisper")])
    assert "is free" not in trace.report([row()])


def test_cold_turns_tools_and_unmeasured_ones_stay_out_of_the_medians() -> None:
    rows = [
        row(first_in_session=1, played_ms=9000),
        row(tools=2, played_ms=9000),
        row(played_ms=None, first_audio_ms=9000),
        row(played_ms=1000),
    ]
    out = trace.report(rows)
    assert "1 turns" in out and "p50 1.00s" in out
    assert "1 first-of-session turn left out" in out
    # Where the page never said, the audio sent stands in, and is marked.
    assert "9.00~" in out


def test_typed_turns_are_judged_on_their_first_words() -> None:
    typed = row(via="text", heard_ms=None, endpoint_ms=None, first_text_ms=450)
    out = trace.report([typed, typed])
    assert "Typed replies: 2 turns" in out and "p50 0.45s" in out and "met" in out


def test_a_turn_with_several_model_calls_lists_each() -> None:
    call = row()["calls"][0]
    out = trace.report([row(tools=1, calls=[call, call | {"start_ms": 3000}])])
    assert "call 1:" in out and "call 2: started 3.00s" in out


def test_a_turn_that_did_not_end_says_why() -> None:
    assert "claude-sonnet-5 (interrupted)" in trace.report([row(stop="interrupted")])


def test_each_turn_says_whether_a_draft_was_used() -> None:
    out = trace.report([row(draft="used"), row(draft="dropped"), row(draft="none")])
    head = next(ln for ln in out.splitlines() if ln.startswith("when "))
    assert " draft " in head
    lines = [ln for ln in out.splitlines() if "claude-sonnet-5" in ln]
    assert " used " in lines[0] and " dropped " in lines[1]
    assert "used" not in lines[2] and "dropped" not in lines[2]


def test_a_spoken_turn_says_what_became_of_its_cues() -> None:
    cues = {"sent": 1, "played": 1, "skipped": {"pause": 3, "coin": 1, "confident": 5}}
    out = trace.report([row(cues=cues), row(via="text")])
    lines = [ln for ln in out.splitlines() if "cues:" in ln]
    assert lines == ["      cues: 1 sent, 1 played; none: confident 5, pause 3, coin 1"]
