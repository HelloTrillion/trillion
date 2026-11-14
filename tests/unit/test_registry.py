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


@pytest.mark.parametrize("module", ["trillion.toolsevil", "trillion.tools_extra.x", "evil"])
def test_lossless_output_needs_a_real_first_party_module(module):
    """Skipping the neutralizer (SPN-6) is reserved exactly like trusted_output: a plugin
    that could declare it could hand the model raw role markers from anywhere."""
    with pytest.raises(RegistryError, match="lossless_output"):
        ToolRegistry().register(make(d(lossless_output=True)), module=module)


@pytest.mark.parametrize("module", ["trillion.toolsevil", "trillion.tools_extra.x", "evil"])
def test_saying_it_writes_no_paths_needs_a_real_first_party_module(module):
    """A server or skill that could opt out would pass a read-only share (D11)."""
    with pytest.raises(RegistryError, match="writes_paths"):
        ToolRegistry().register(make(d(writes_paths=False)), module=module)


def test_first_party_modules_may_say_they_write_no_paths():
    ToolRegistry().register(make(d(writes_paths=False)), module="trillion.tools.folders")


def test_discover_loads_file_edit():
    assert "file_edit" in {x.name for x in ToolRegistry.discover().definitions()}


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


def test_redefine_replaces_the_snapshot():
    r = ToolRegistry()
    r.register(make(d()))
    new = d(
        description="told more", input_schema=schema(q={"type": "string"}, n={"type": "integer"})
    )
    r.redefine("good_tool", new)
    snap = r.definition("good_tool")
    assert snap is not None and snap.description == "told more"
    assert set(snap.input_schema["properties"]) == {"q", "n"}
    # A snapshot: changing the definition handed in afterwards changes nothing.
    new.input_schema["properties"]["n"]["type"] = "string"  # type: ignore[index]
    assert snap.input_schema["properties"]["n"] == {"type": "integer"}
    assert snap in r.definitions()


@pytest.mark.parametrize(
    ("defn", "fragment"),
    [
        (d(risk=Risk.WRITE), "risk"),
        (d(category="other"), "category"),
        (d(name="other_tool"), "name"),
        (d(input_schema={"type": "object", "properties": {}}), "additionalProperties"),
        (d(input_schema=schema(path={"type": "string"})), "path_args"),
        (d(input_schema={"type": 7}), "invalid input_schema"),
    ],
)
def test_redefine_is_validated_like_register(defn, fragment):
    r = ToolRegistry()
    r.register(make(d()), module="evil")
    before = r.definition("good_tool")
    with pytest.raises(RegistryError, match=fragment):
        r.redefine("good_tool", defn)
    assert r.definition("good_tool") == before


def test_redefine_refuses_a_tool_not_registered():
    r = ToolRegistry()
    with pytest.raises(RegistryError, match="not registered"):
        r.redefine("good_tool", d())
    r.register(make(d()))
    r.unregister("good_tool")
    with pytest.raises(RegistryError, match="not registered"):
        r.redefine("good_tool", d())


def test_an_unattended_view_cannot_be_redefined():
    r = ToolRegistry()
    r.register(make(d()))
    with pytest.raises(RegistryError, match="read-only"):
        r.unattended().redefine("good_tool", d(description="x"))


@pytest.mark.parametrize(
    "change",
    [
        {"path_args": ("q",)},
        {"outside_path_args": ("q",)},
        {"recoverable": False},
        {"timeout_s": 9.0},
        {"lossless_output": True},
        {"trusted_output": True},
        {"writes_paths": False},
    ],
)
def test_redefine_changes_only_the_description_and_schema(change):
    """How the router treats a tool is settled at registration, even for a first-party
    tool that could have declared it then."""
    r = ToolRegistry()
    r.register(make(d()), module="trillion.tools.x")
    before = r.definition("good_tool")
    with pytest.raises(RegistryError, match="only its description and input_schema"):
        r.redefine("good_tool", d(description="new", **change))
    assert r.definition("good_tool") == before


# ── CON-6a: only Trillion's own entry points are imported ────────────────


class _Dist:
    def __init__(self, name: object) -> None:
        self.name = name


class _EP:
    """An entry point as `discover` reads it: a name, a value, a dist, and `load`."""

    def __init__(self, name: str, value: str, dist: object, obj: object = None) -> None:
        self.name = name
        self.value = value
        self.dist = dist
        self._obj = obj
        self.loads = 0

    def load(self) -> object:
        self.loads += 1
        return self._obj


def _faked(monkeypatch: pytest.MonkeyPatch, eps: list[_EP]) -> None:
    import trillion.spine.registry as registry

    monkeypatch.setattr(registry, "entry_points", lambda *, group: list(eps))


def _tool(name: str) -> type[BaseTool]:
    return type(make(d(name=name)))


def test_discover_never_loads_another_packages_entry_point(monkeypatch, caplog):
    ours = _EP("good_tool", "trillion.tools.x:T", _Dist("trillion"), _tool("good_tool"))
    foreign = _EP("evil_tool", "trillion.tools.evil:T", _Dist("trillion-evil"))
    _faked(monkeypatch, [ours, foreign])
    with caplog.at_level("WARNING", logger="trillion.spine.registry"):
        r = ToolRegistry.discover()
    assert foreign.loads == 0
    assert ours.loads == 1
    assert r.get("good_tool") is not None
    assert "evil_tool" in caplog.text
    # Only the name: never what it points at.
    assert "trillion.tools.evil" not in caplog.text


def test_discover_skips_an_entry_point_with_no_distribution(monkeypatch):
    lost = _EP("lost_tool", "trillion.tools.lost:T", None)
    nameless = _EP("nameless_tool", "trillion.tools.nameless:T", _Dist(None))
    _faked(monkeypatch, [lost, nameless])
    r = ToolRegistry.discover()
    assert lost.loads == 0 and nameless.loads == 0
    assert r.definitions() == ToolRegistry().definitions()


@pytest.mark.parametrize("dist", ["trillion", "Trillion", "TRILLION"])
def test_discover_loads_first_party_entry_points_by_normalized_name(monkeypatch, dist):
    ours = _EP("good_tool", "trillion.tools.x:T", _Dist(dist), _tool("good_tool"))
    _faked(monkeypatch, [ours])
    assert ToolRegistry.discover().get("good_tool") is not None
    assert ours.loads == 1


def test_skipped_plugins_names_them_without_loading(monkeypatch):
    from trillion.spine.registry import skipped_plugins

    ours = _EP("good_tool", "trillion.tools.x:T", _Dist("trillion"))
    foreign = _EP("evil_tool", "evil:T", _Dist("trillion_tools_plus"))
    _faked(monkeypatch, [ours, foreign])
    assert skipped_plugins() == ["evil_tool"]
    assert ours.loads == 0 and foreign.loads == 0


def test_the_installed_trillion_has_no_skipped_tools():
    """However Trillion is installed here (editable in development), its own tools are
    reported as its own, so none of them is skipped."""
    from trillion.spine.registry import skipped_plugins

    assert skipped_plugins() == []
