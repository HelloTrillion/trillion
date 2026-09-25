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
        (d(input_schema={**schema(), "patternProperties": {".*": {}}}), "patternProperties"),
        (d(input_schema={**schema(), "unevaluatedProperties": True}), "unevaluatedProperties"),
        (d(input_schema={**schema(), "$ref": "#/$defs/x", "$defs": {"x": {}}}), r"\$ref"),
        (
            d(input_schema={"type": "object", "properties": {}, "additionalProperties": {}}),
            "additionalProperties",
        ),
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


def test_a_definition_must_be_a_tool_definition():
    class Loose(BaseTool):
        definition = "loose"  # type: ignore[assignment]

    with pytest.raises(RegistryError, match="ToolDefinition"):
        ToolRegistry().register(Loose())


def test_an_invalid_json_schema_is_rejected():
    with pytest.raises(RegistryError, match="invalid input_schema"):
        ToolRegistry().register(make(d(input_schema={"type": 7})))


def test_discover_loads_the_first_party_file_tools():
    r = ToolRegistry.discover()
    names = {x.name for x in r.definitions()}
    assert {"file_list", "file_read", "file_create", "file_overwrite", "file_append"} <= names


@pytest.mark.parametrize("module", ["trillion.toolsevil", "trillion.tools_extra.x", "evil"])
def test_trusted_output_needs_a_real_first_party_module(module):
    with pytest.raises(RegistryError, match="trusted_output"):
        ToolRegistry().register(make(d(trusted_output=True)), module=module)


@pytest.mark.parametrize("module", ["trillion.tools", "trillion.tools.files"])
def test_first_party_modules_may_declare_trusted_output(module):
    ToolRegistry().register(make(d(trusted_output=True)), module=module)


def test_the_definition_is_snapshotted_at_registration():
    import dataclasses

    tool = make(d(risk=Risk.IRREVERSIBLE, input_schema=schema(q={"type": "string"})))
    r = ToolRegistry()
    r.register(tool)
    tool.definition = dataclasses.replace(tool.definition, risk=Risk.READ, trusted_output=True)
    tool.definition.input_schema["properties"]["q"]["type"] = "integer"  # type: ignore[index]
    snap = r.definition("good_tool")
    assert snap is not None and snap.risk is Risk.IRREVERSIBLE and not snap.trusted_output
    assert snap.input_schema["properties"]["q"] == {"type": "string"}
    assert snap in r.definitions()
    assert r.definition("missing") is None
