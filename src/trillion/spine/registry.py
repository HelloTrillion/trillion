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
# Families an unattended run never gets, whatever their risk: a run nobody watches does
# not change what runs later (AGT-9), and has no page to show a board on (SUR-1).
UNATTENDED_NEVER = frozenset({"schedule", "workspace"})


class RegistryError(ValueError):
    pass


def runs_unattended(d: ToolDefinition) -> bool:
    """Whether a run nobody is watching may use this tool (D5): only what the router runs
    without a card, so never irreversible or spend (an MCP, Google or Slack tool the user
    left asking first is registered irreversible), and nothing that changes the schedule.
    `unattended()` offers exactly these, and the router refuses the rest by name."""
    return d.risk not in GATED and d.category not in UNATTENDED_NEVER


def validate(tool: Tool, module: str, definition: ToolDefinition | None = None) -> None:
    """Check `tool` against the contract, with `definition` in place of its own if given."""
    d = tool.definition if definition is None else definition
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
    for field, names in (("path_args", d.path_args), ("outside_path_args", d.outside_path_args)):
        for name in names:
            if name not in props:
                raise RegistryError(f"{d.name}: {field} entry {name!r} is not in the schema")
    for prop in props:
        if _PATHY.search(prop) and prop not in (*d.path_args, *d.outside_path_args):
            raise RegistryError(f"{d.name}: {prop!r} looks like a path; declare it in path_args")
    first_party = module == FIRST_PARTY_PACKAGE or module.startswith(FIRST_PARTY_PACKAGE + ".")
    if d.trusted_output and not first_party:
        raise RegistryError(f"{d.name}: trusted_output is reserved for first-party tools")
    if d.lossless_output and not first_party:
        raise RegistryError(f"{d.name}: lossless_output is reserved for first-party tools")
    if d.risk in GATED and isinstance(tool, BaseTool) and type(tool).describe is BaseTool.describe:
        raise RegistryError(f"{d.name}: gated tools must implement describe() (SPN-2a)")


def _snapshot(d: ToolDefinition) -> ToolDefinition:
    # What was validated, copied: a plugin that later reassigns `tool.definition` (or
    # mutates its schema dict) cannot change its risk, path_args or trust.
    return dataclasses.replace(d, input_schema=copy.deepcopy(dict(d.input_schema)))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._definitions: dict[str, ToolDefinition] = {}
        self._modules: dict[str, str] = {}

    def register(self, tool: Tool, *, module: str | None = None) -> None:
        d = tool.definition
        module = module or type(tool).__module__
        validate(tool, module)
        name = d.name
        if name in self._tools:
            raise RegistryError(f"{name}: already registered")
        # Kept so a redefinition is held to what this module may declare.
        self._modules[name] = module
        self._tools[name] = tool
        self._definitions[name] = _snapshot(d)

    def redefine(self, name: str, definition: ToolDefinition) -> None:
        """Change what a registered tool says about itself, as when a connector changes
        which widgets the workspace tools list. Validated like `register`; only the
        description and input_schema may change."""
        tool, old = self._tools.get(name), self._definitions.get(name)
        if tool is None or old is None:
            raise RegistryError(f"{name}: not registered")
        if not isinstance(definition, ToolDefinition) or definition.name != name:
            raise RegistryError(f"{name}: a new definition must keep the name")
        if definition.risk is not old.risk:
            raise RegistryError(f"{name}: a new definition may not change the risk")
        if definition.category != old.category:
            raise RegistryError(f"{name}: a new definition may not change the category")
        # Only words and shape: how the router treats it (paths, trust, timeout,
        # recovery) is settled once, at registration.
        kept = dataclasses.replace(
            definition, description=old.description, input_schema=old.input_schema
        )
        if kept != old:
            raise RegistryError(
                f"{name}: a new definition may change only its description and input_schema"
            )
        validate(tool, self._modules[name], definition)
        self._definitions[name] = _snapshot(definition)

    def unregister(self, name: str) -> None:
        """Take a tool away, as when the user disconnects the server it came from. A
        card already waiting for it can no longer run: the router finds no tool."""
        self._tools.pop(name, None)
        self._definitions.pop(name, None)
        self._modules.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definition(self, name: str) -> ToolDefinition | None:
        """The definition as validated at registration (or `redefine`). The router uses
        only this."""
        return self._definitions.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values()) + list(META_TOOLS.values())

    def unattended(self) -> ToolRegistry:
        """A read-only view for a run nobody is watching (D5): only the tools that
        `runs_unattended` allows, and not the meta tools that settle a card.

        Taken when the run starts: a server connected later is not in it."""
        view = _ReadOnly()
        for name, d in self._definitions.items():
            if runs_unattended(d):
                view._tools[name] = self._tools[name]
                view._definitions[name] = d
        return view

    @classmethod
    def discover(cls, group: str = "trillion.tools") -> ToolRegistry:
        reg = cls()
        for ep in entry_points(group=group):
            obj = ep.load()
            tool = obj() if isinstance(obj, type) else obj
            reg.register(tool, module=ep.value.split(":", 1)[0])
        return reg


class _ReadOnly(ToolRegistry):
    """What `unattended()` returns: nothing can be added to it or taken from it, and it
    offers no meta tool, since an unattended run can settle no card."""

    def register(self, tool: Tool, *, module: str | None = None) -> None:
        raise RegistryError("an unattended view is read-only")

    def unregister(self, name: str) -> None:
        raise RegistryError("an unattended view is read-only")

    def redefine(self, name: str, definition: ToolDefinition) -> None:
        raise RegistryError("an unattended view is read-only")

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())
