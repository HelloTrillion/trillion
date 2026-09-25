from trillion.spine.loop_guard import Limits, LoopGuard, StopReason, Verdict


class Tick:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_exact_repeat_warns_at_3_stops_at_5():
    g = LoopGuard()
    verdicts = [g.check_call("t", "h").verdict for _ in range(5)]
    assert verdicts == [Verdict.OK, Verdict.OK, Verdict.WARN, Verdict.WARN, Verdict.STOP]
    assert g.check_call("t", "h").reason is StopReason.REPEAT


def test_different_args_do_not_count_as_repeats():
    g = LoopGuard()
    assert all(g.check_call("t", str(i)).verdict is Verdict.OK for i in range(6))


def test_failing_tool_warns_at_3_stops_at_8():
    g = LoopGuard()
    verdicts = [g.record_result("t", ok=False).verdict for _ in range(8)]
    assert verdicts[:2] == [Verdict.OK, Verdict.OK]
    assert verdicts[2] is Verdict.WARN
    assert verdicts[7] is Verdict.STOP
    assert g.record_result("t", ok=True).verdict is Verdict.OK


def test_ceilings():
    tick = Tick()
    g = LoopGuard(Limits(max_rounds=2, max_seconds=5.0, max_cost_usd=0.5), clock=tick)
    assert g.next_round().verdict is Verdict.OK
    assert g.next_round().verdict is Verdict.OK
    assert g.next_round().reason is StopReason.ROUNDS
    tick.now = 6.0
    assert g.check_call("x", "1").reason is StopReason.WALL_CLOCK
    assert g.add_cost(0.6).reason is StopReason.COST
