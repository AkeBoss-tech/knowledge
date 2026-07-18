"""Import a deliberately small, safe subset of Claude dynamic workflows.

Claude workflow files are JavaScript, but their ``agent`` and ``pipeline``
functions only exist in Claude Code.  This module never evaluates JavaScript:
it recognizes the small generated shape documented by Claude and translates it
to KRAIL's declarative workflow syntax.  Anything outside that subset is an
actionable import error, not a best-effort execution attempt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ClaudeWorkflowImportError(ValueError):
    """Raised when a Claude workflow needs a runtime feature KRAIL cannot port."""


_FORBIDDEN = re.compile(r"\b(?:import|require|eval|Function|process|child_process|fs)\b")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# This is intentionally an envelope schema.  KRAIL's existing workflow schema
# owns executable steps; the envelope records a portable orchestration format
# and provenance without coupling execution to a vendor runtime.
KRAIL_FLOW_V1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["format"],
    "properties": {
        "format": {"const": "krail-flow/v1"},
        "imported_from": {
            "type": "object",
            "required": ["format", "path"],
            "properties": {
                "format": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
}


def validate_krail_flow_v1(flow: Any) -> list[str]:
    """Validate the portable Flow envelope without requiring jsonschema."""
    if not isinstance(flow, dict):
        return ["flow must be a mapping"]
    if flow.get("format") != "krail-flow/v1":
        return ["flow.format must be krail-flow/v1"]
    imported_from = flow.get("imported_from")
    if imported_from is not None:
        if not isinstance(imported_from, dict):
            return ["flow.imported_from must be a mapping"]
        for key in ("format", "path"):
            if not isinstance(imported_from.get(key), str) or not imported_from[key].strip():
                return [f"flow.imported_from.{key} must be a non-empty string"]
    return []


def _balanced(text: str, start: int, opening: str = "(", closing: str = ")") -> tuple[str, int]:
    """Return the balanced expression following *start*, preserving strings."""
    if start >= len(text) or text[start] != opening:
        raise ClaudeWorkflowImportError("internal parser error: expected balanced expression")
    depth, quote, escaped = 0, None, False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    raise ClaudeWorkflowImportError("unterminated JavaScript expression")


def _split_top_level(value: str, delimiter: str = ",") -> list[str]:
    parts, start, depth, quote, escaped = [], 0, 0, None, False
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif char == delimiter and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    # Claude's formatter commonly leaves a trailing comma in multi-line calls.
    return [part for part in parts if part]


def _string(value: str, *, loop_var: str | None = None) -> str:
    value = value.strip()
    if len(value) < 2 or value[0] not in {"'", '"', "`"} or value[-1] != value[0]:
        raise ClaudeWorkflowImportError("agent prompts must be quoted string literals or template literals")
    body = value[1:-1]
    if value[0] == "`" and "${" in body:
        if not loop_var:
            raise ClaudeWorkflowImportError("template literals are only supported inside pipeline agent prompts")
        body = re.sub(r"\$\{\s*" + re.escape(loop_var) + r"\s*\}", "${{ " + loop_var + " }}", body)
        if re.search(r"\$\{(?!\{)", body):
            raise ClaudeWorkflowImportError("pipeline prompts may interpolate only their pipeline item variable")
    try:
        # JSON handles the escapes used in generated single/double quoted prompts.
        if value[0] != "`":
            return json.loads('"' + body.replace('"', '\\"') + '"')
    except json.JSONDecodeError:
        pass
    return body.replace("\\`", "`").replace("\\'", "'").replace('\\"', '"')


def _object(value: str) -> dict[str, Any]:
    """Parse JSON-shaped workflow options; this intentionally is not JS eval."""
    value = value.strip()
    if not value:
        return {}
    if not value.startswith("{") or not value.endswith("}"):
        raise ClaudeWorkflowImportError("agent options must be an object literal")
    normalized = re.sub(r"([,{]\s*)([A-Za-z_$][A-Za-z0-9_$]*)(\s*:)", r'\1"\2"\3', value)
    normalized = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", lambda match: json.dumps(bytes(match.group(1), "utf-8").decode("unicode_escape")), normalized)
    normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ClaudeWorkflowImportError("agent options must use JSON-compatible literals") from exc
    if not isinstance(parsed, dict):
        raise ClaudeWorkflowImportError("agent options must be an object")
    return parsed


def _agent_call(expression: str, *, loop_var: str | None = None) -> dict[str, Any]:
    expression = expression.strip()
    if not expression.startswith("agent(") or not expression.endswith(")"):
        raise ClaudeWorkflowImportError("only agent(...) calls are supported in this position")
    args, end = _balanced(expression, expression.index("("))
    if end != len(expression):
        raise ClaudeWorkflowImportError("unexpected code after agent(...) call")
    parts = _split_top_level(args)
    if not parts:
        raise ClaudeWorkflowImportError("agent() requires a prompt")
    prompt = _string(parts[0], loop_var=loop_var)
    option_source = parts[1] if len(parts) > 1 else ""
    if loop_var:
        # Claude commonly labels a pipeline agent with the current item.  It
        # is metadata, so preserve it as KRAIL interpolation rather than
        # accepting a general JavaScript expression.
        option_source = re.sub(
            r"(\blabel\s*:\s*)" + re.escape(loop_var) + r"(?=\s*[,}])",
            r'\1"${{ ' + loop_var + r' }}"',
            option_source,
        )
    options = _object(option_source) if option_source else {}
    if len(parts) > 2:
        raise ClaudeWorkflowImportError("agent() accepts at most prompt and options")
    step: dict[str, Any] = {"kind": "agent", "runner": "auto", "role": "research", "prompt": prompt}
    if isinstance(options.get("schema"), dict):
        step["output_schema"] = options["schema"]
    if isinstance(options.get("label"), str):
        step["label"] = options["label"]
    unsupported = set(options) - {"schema", "label"}
    if unsupported:
        raise ClaudeWorkflowImportError("unsupported agent options: " + ", ".join(sorted(unsupported)))
    return step


def _parse_meta(source: str) -> dict[str, Any]:
    match = re.search(r"export\s+const\s+meta\s*=\s*", source)
    if not match:
        raise ClaudeWorkflowImportError("workflow must export a meta object")
    body, _end = _balanced(source, source.find("{", match.end()), "{", "}")
    return _object("{" + body + "}")


def parse_claude_workflow(source: str, *, source_path: str, max_items: int = 100) -> dict[str, Any]:
    if _FORBIDDEN.search(source):
        raise ClaudeWorkflowImportError("imports, process access, filesystem access, and dynamic evaluation are not portable")
    meta = _parse_meta(source)
    name = meta.get("name")
    if not isinstance(name, str) or not _IDENTIFIER.match(name.replace("-", "_")):
        raise ClaudeWorkflowImportError("meta.name must be a non-empty slug-like string")
    description = meta.get("description") if isinstance(meta.get("description"), str) else f"Imported Claude workflow: {name}."
    steps: list[dict[str, Any]] = []
    variables: dict[str, str] = {}
    pattern = re.compile(r"(?:const|let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*await\s+")
    position = 0
    while match := pattern.search(source, position):
        variable = match.group(1)
        call_start = match.end()
        if source.startswith("agent(", call_start):
            expression, position = _balanced(source, source.index("(", call_start))
            step = _agent_call("agent(" + expression + ")")
            step["id"] = variable
            steps.append(step)
            variables[variable] = variable
            continue
        if source.startswith("pipeline(", call_start):
            expression, position = _balanced(source, source.index("(", call_start))
            parts = _split_top_level(expression)
            if len(parts) != 2:
                raise ClaudeWorkflowImportError("pipeline() requires items and a single item callback")
            source_expr = parts[0].strip()
            source_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", source_expr)
            if not source_match or source_match.group(1) not in variables:
                raise ClaudeWorkflowImportError("pipeline items must reference a field from an earlier agent result, e.g. found.files")
            loop_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=>\s*(agent\(.+\))", parts[1].strip(), flags=re.DOTALL)
            if not loop_match:
                raise ClaudeWorkflowImportError("pipeline callback must be `item => agent(...)`")
            item_var, agent_expr = loop_match.groups()
            child = _agent_call(agent_expr, loop_var=item_var)
            child["id"] = f"{variable}_item"
            steps.append({
                "id": variable,
                "kind": "foreach",
                "items_from": f"steps.{source_match.group(1)}.output.{source_match.group(2)}",
                "as": item_var,
                "max_items": max_items,
                "steps": [child],
            })
            variables[variable] = variable
            continue
        raise ClaudeWorkflowImportError("only await agent(...) and await pipeline(...) declarations are supported")

    if not steps:
        raise ClaudeWorkflowImportError("no supported agent or pipeline declarations found")
    returned = re.search(r"\breturn\s+([A-Za-z_][A-Za-z0-9_]*)(?:\.filter\(Boolean\))?\s*;", source)
    outputs: dict[str, Any] = {}
    if returned:
        variable = returned.group(1)
        if variable not in variables:
            raise ClaudeWorkflowImportError("return value must reference an imported agent or pipeline variable")
        outputs["result"] = {"from": f"steps.{variable}"}
    return {
        "id": name,
        "description": description,
        "flow": {"format": "krail-flow/v1", "imported_from": {"format": "claude-dynamic-workflow/js", "path": source_path}},
        "steps": steps,
        "outputs": outputs,
    }
