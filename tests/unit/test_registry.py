import pytest

from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.registry import RegistryError, ToolRegistry


def schema(**props: object) -> dict[str, object]:
    return {"type": "object", "properties": props, "additionalProperties": False}


def make(defn: ToolDefinition, *, describes: bool = True) -> BaseTool:
    class T(BaseTool):
        definition = defn

        async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
            return ToolResult(ok=True, content="", effect="none")

    if describes:
        T.describe = lambda self, args: ActionSummary(text="Do the thing")  # type: ignore[method-assign]
    return T()


def d(**kw: object) -> ToolDefinition:
    base: dict[str, object] = dict(
        name="good_tool",
        description="d",
        input_schema=schema(q={"type": "string"}),
        risk=Risk.READ,
        category="c",
        timeout_s=1.0,
    )
    base.update(kw)
    return ToolDefinition(**base)  # type: ignore[arg-type]


def test_registers_a_valid_tool():
    r = ToolRegistry()
    r.register(make(d()))
    assert r.get("good_tool") is not None


@pytest.mark.parametrize(
    ("defn", "fragment"),
    [
        (d(risk="read"), "risk"),
        (d(name="Bad Name"), "name"),
        (d(name="confirm_pending_action"), "reserved"),
        (d(input_schema={"type": "object", "properties": {}}), "additionalProperties"),
        (d(input_schema=schema(path={"type": "string"})), "path_args"),
        (d(input_schema=schema(target_dir={"type": "string"})), "path_args"),
        (d(path_args=("nope",)), "path_args"),
        (d(timeout_s=0), "timeout"),
        (d(trusted_output=True), "trusted_output"),
    ],
)
def test_rejects_invalid_definitions(defn, fragment):
    with pytest.raises(RegistryError, match=fragment):
        ToolRegistry().register(make(defn))


def test_gated_tools_must_write_their_own_approval_text():
    with pytest.raises(RegistryError, match="describe"):
        ToolRegistry().register(make(d(risk=Risk.IRREVERSIBLE), describes=False))


def test_duplicate_names_are_rejected():
    r = ToolRegistry()
    r.register(make(d()))
    with pytest.raises(RegistryError, match="already"):
        r.register(make(d()))


def test_definitions_include_the_meta_tools():
    names = {x.name for x in ToolRegistry().definitions()}
    assert {"confirm_pending_action", "cancel_pending_action"} <= names
