from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable


ACTION_SCHEMA_VERSION = "krail.action/v1"

ACTION_EFFECTS = frozenset({"read", "local_write", "external_write", "destructive"})


class ActionError(RuntimeError):
    """Base error for action registration and execution failures."""


class ActionValidationError(ActionError):
    """Raised when an action definition or value violates its schema."""


class ActionNotFoundError(ActionValidationError):
    """Raised when an action id is not registered."""


@dataclass(frozen=True)
class ActionDefinition:
    """Typed metadata for one reusable KRAIL operation.

    Handlers stay outside the serialized definition so action catalogs and dry
    runs never leak executable objects or credential values.
    """

    id: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    effect: str = "read"
    capabilities: tuple[str, ...] = ()
    credentials: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    max_attempts: int = 1
    idempotency: str = "not_applicable"
    version: str = ACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ActionValidationError("action id is required")
        if self.effect not in ACTION_EFFECTS:
            raise ActionValidationError(
                f"unsupported action effect {self.effect!r}; expected one of {sorted(ACTION_EFFECTS)}"
            )
        if self.max_attempts < 1:
            raise ActionValidationError("action max_attempts must be at least 1")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ActionValidationError("action timeout_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        payload["credentials"] = list(self.credentials)
        return payload


ActionHandler = Callable[[dict[str, Any]], Any]


def validate_simple_schema(value: Any, schema: Any, *, path: str = "value") -> list[str]:
    """Validate the stable JSON-schema subset used by local actions.

    KRAIL intentionally keeps this subset dependency-free. The contract can be
    widened later without changing ActionDefinition or the registry surface.
    """

    if not isinstance(schema, dict):
        return []
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type:
        type_map: dict[str, type | tuple[type, ...]] = {
            "object": dict,
            "array": list,
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "null": type(None),
        }
        expected = type_map.get(str(expected_type))
        if expected and (not isinstance(value, expected) or expected_type == "integer" and isinstance(value, bool)):
            errors.append(f"{path} must be {expected_type}")
            return errors
    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if isinstance(key, str) and key not in value:
                errors.append(f"{path}.{key} is required")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(validate_simple_schema(value[key], child_schema, path=f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(validate_simple_schema(item, schema["items"], path=f"{path}[{index}]"))
    return errors


class ActionRegistry:
    """In-process catalog for built-in and project-adapted actions."""

    def __init__(self) -> None:
        self._definitions: dict[str, ActionDefinition] = {}
        self._handlers: dict[str, ActionHandler] = {}

    def register(
        self,
        definition: ActionDefinition,
        handler: ActionHandler | None = None,
        *,
        replace: bool = False,
    ) -> ActionDefinition:
        if definition.id in self._definitions and not replace:
            raise ActionValidationError(f"action already registered: {definition.id}")
        self._definitions[definition.id] = definition
        if handler is not None:
            self._handlers[definition.id] = handler
        elif replace:
            self._handlers.pop(definition.id, None)
        return definition

    def get(self, action_id: str) -> ActionDefinition:
        try:
            return self._definitions[action_id]
        except KeyError as exc:
            raise ActionNotFoundError(f"unknown action: {action_id}") from exc

    def list(self) -> list[dict[str, Any]]:
        return [self._definitions[key].to_dict() for key in sorted(self._definitions)]

    def describe(self, action_id: str) -> dict[str, Any]:
        definition = self.get(action_id)
        return {**definition.to_dict(), "executable": action_id in self._handlers}

    def execute(self, action_id: str, inputs: dict[str, Any] | None = None, *, dry_run: bool = False) -> dict[str, Any]:
        definition = self.get(action_id)
        payload = dict(inputs or {})
        errors = validate_simple_schema(payload, definition.input_schema, path="input")
        if errors:
            raise ActionValidationError("; ".join(errors))
        if dry_run:
            return {
                "status": "dry_run",
                "action": definition.to_dict(),
                "input": payload,
            }
        handler = self._handlers.get(action_id)
        if handler is None:
            raise ActionError(f"action has no local handler: {action_id}")
        output = handler(payload)
        output_errors = validate_simple_schema(output, definition.output_schema, path="output")
        if output_errors:
            raise ActionValidationError("; ".join(output_errors))
        return {
            "status": "done",
            "action_id": action_id,
            "effect": definition.effect,
            "output": output,
        }
