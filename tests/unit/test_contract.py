import pytest

from trillion.spine.contract import GATED, Precondition, Risk, ToolDefinition


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
