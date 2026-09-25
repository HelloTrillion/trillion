import time

import pytest

from trillion.spine.clock import system_clock
from trillion.spine.contract import (
    GATED,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
)


def test_risk_is_required():
    with pytest.raises(TypeError):
        ToolDefinition(  # type: ignore[call-arg]
            name="x", description="d", input_schema={}, category="c", timeout_s=1.0
        )


def test_gated_risks():
    assert GATED == {Risk.IRREVERSIBLE, Risk.SPEND}


def test_safe_defaults():
    d = ToolDefinition(
        name="x", description="d", input_schema={}, risk=Risk.READ, category="c", timeout_s=1.0
    )
    assert d.trusted_output is False
    assert d.recoverable is True
    assert d.path_args == ()


def test_precondition_helpers():
    assert Precondition.passed().ok
    failed = Precondition.failed("gone")
    assert not failed.ok and failed.reason == "gone"


async def test_base_tool_defaults_are_inert():
    class T(BaseTool):
        definition = ToolDefinition(
            name="t", description="d", input_schema={}, risk=Risk.READ, category="c", timeout_s=1
        )

    ctx = ToolContext(owner_id="o", session_id="s", turn=1, call_id="c", via="text")
    t = T()
    assert t.target({}) == "t"
    assert (await t.precheck({}, ctx)).ok
    assert await t.reconcile({}, ctx) is None
    with pytest.raises(NotImplementedError):
        await t.execute({}, ctx)


def test_the_system_clock_is_epoch_milliseconds():
    assert abs(system_clock() - time.time() * 1000) < 5000
