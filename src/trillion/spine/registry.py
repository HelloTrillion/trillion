"""Tool registration. A tool that breaks the contract fails when it is loaded, not when it runs."""

from __future__ import annotations

import copy
import dataclasses
import re
from importlib.metadata import entry_points

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from trillion.spine.confirm import META_TOOLS
from trillion.spine.contract import GATED, BaseTool, Risk, Tool, ToolDefinition

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PATHY = re.compile(r"path|file|dir|folder", re.IGNORECASE)
FIRST_PARTY_PACKAGE = "trillion.tools"
# Top-level keywords that let arguments through that `properties` never names.
_OPEN_SCHEMA_KEYS = ("patternProperties", "unevaluatedProperties", "$ref")


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
    for key in _OPEN_SCHEMA_KEYS:
        if key in schema:
            raise RegistryError(f"{d.name}: input_schema may not use top-level {key!r}")
    props = schema.get("properties", {})
    for name in d.path_args:
        if name not in props:
            raise RegistryError(f"{d.name}: path_args entry {name!r} is not in the schema")
    for prop in props:
        if _PATHY.search(prop) and prop not in d.path_args:
            raise RegistryError(f"{d.name}: {prop!r} looks like a path; declare it in path_args")
    first_party = module == FIRST_PARTY_PACKAGE or module.startswith(FIRST_PARTY_PACKAGE + ".")
    if d.trusted_output and not first_party:
        raise RegistryError(f"{d.name}: trusted_output is reserved for first-party tools")
    if d.lossless_output and not first_party:
        raise RegistryError(f"{d.name}: lossless_output is reserved for first-party tools")
    if d.risk in GATED and isinstance(tool, BaseTool) and type(tool).describe is BaseTool.describe:
        raise RegistryError(f"{d.name}: gated tools must implement describe() (SPN-2a)")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, tool: Tool, *, module: str | None = None) -> None:
        d = tool.definition
        validate(tool, module or type(tool).__module__)
        name = d.name
        if name in self._tools:
            raise RegistryError(f"{name}: already registered")
        # Snapshot what was validated. A plugin that later reassigns `tool.definition`
        # (or mutates its schema dict) cannot change its risk, path_args or trust.
        snapshot = dataclasses.replace(d, input_schema=copy.deepcopy(dict(d.input_schema)))
        self._tools[name] = tool
        self._definitions[name] = snapshot

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definition(self, name: str) -> ToolDefinition | None:
        """The definition as validated at registration. The router uses only this."""
        return self._definitions.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values()) + list(META_TOOLS.values())

    @classmethod
    def discover(cls, group: str = "trillion.tools") -> ToolRegistry:
        reg = cls()
        for ep in entry_points(group=group):
            obj = ep.load()
            tool = obj() if isinstance(obj, type) else obj
            reg.register(tool, module=ep.value.split(":", 1)[0])
        return reg
