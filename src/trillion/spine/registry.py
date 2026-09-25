"""Tool registration. A tool that breaks the contract fails when it is loaded, not when it runs."""

from __future__ import annotations

import re
from importlib.metadata import entry_points

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from trillion.spine.confirm import META_TOOLS
from trillion.spine.contract import GATED, BaseTool, Risk, Tool, ToolDefinition

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PATHY = re.compile(r"path|file|dir|folder", re.IGNORECASE)
FIRST_PARTY_PREFIX = "trillion.tools"


class RegistryError(ValueError):
    pass


def validate(tool: Tool, module: str) -> None:
    d = tool.definition
    if not isinstance(d, ToolDefinition):
        raise RegistryError("definition must be a ToolDefinition")
    if not isinstance(d.risk, Risk):
        raise RegistryError(f"{d.name}: risk is required and must be a Risk")
    if not _NAME.match(d.name):
        raise RegistryError(f"{d.name!r}: name must match {_NAME.pattern}")
    if d.name in META_TOOLS:
        raise RegistryError(f"{d.name}: name is reserved by the spine")
    if d.timeout_s <= 0:
        raise RegistryError(f"{d.name}: timeout_s must be positive")
    schema = d.input_schema
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError as e:
        raise RegistryError(f"{d.name}: invalid input_schema ({e.message})") from None
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise RegistryError(
            f"{d.name}: input_schema must be an object with additionalProperties: false"
        )
    props = schema.get("properties", {})
    for name in d.path_args:
        if name not in props:
            raise RegistryError(f"{d.name}: path_args entry {name!r} is not in the schema")
    for prop in props:
        if _PATHY.search(prop) and prop not in d.path_args:
            raise RegistryError(f"{d.name}: {prop!r} looks like a path; declare it in path_args")
    if d.trusted_output and not module.startswith(FIRST_PARTY_PREFIX):
        raise RegistryError(f"{d.name}: trusted_output is reserved for first-party tools")
    if d.risk in GATED and isinstance(tool, BaseTool) and type(tool).describe is BaseTool.describe:
        raise RegistryError(f"{d.name}: gated tools must implement describe() (SPN-2a)")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, module: str | None = None) -> None:
        validate(tool, module or type(tool).__module__)
        name = tool.definition.name
        if name in self._tools:
            raise RegistryError(f"{name}: already registered")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return [t.definition for t in self._tools.values()] + list(META_TOOLS.values())

    @classmethod
    def discover(cls, group: str = "trillion.tools") -> ToolRegistry:
        reg = cls()
        for ep in entry_points(group=group):
            obj = ep.load()
            tool = obj() if isinstance(obj, type) else obj
            reg.register(tool, module=ep.value.split(":", 1)[0])
        return reg
