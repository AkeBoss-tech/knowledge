#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAIL_PY = ROOT / "packages" / "rail-py"
if RAIL_PY.exists() and str(RAIL_PY) not in sys.path:
    sys.path.insert(0, str(RAIL_PY))

import rail  # noqa: E402


RUNNERS = {"codex_cli", "claude_code", "gemini_cli", "cursor_cli", "copilot_cli"}


def _event_text(event: dict[str, Any]) -> str:
    if "comment" in event and isinstance(event["comment"], dict):
        return str(event["comment"].get("body") or "")
    if "issue" in event and isinstance(event["issue"], dict):
        return str(event["issue"].get("body") or "")
    return ""


def extract_commands(text: str) -> list[str]:
    commands: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("/krail"):
            commands.append(line.removeprefix("/krail").strip())
    return commands


def _project_path(value: str | None) -> Path:
    if value:
        return Path(value).resolve()
    if (ROOT / "rail.yaml").exists() or (ROOT / "krail.yaml").exists():
        return ROOT
    example = ROOT / "examples" / "minimal-project"
    if example.exists():
        return example
    return ROOT


def _brief(data: Any, *, max_chars: int = 1600) -> str:
    rendered = json.dumps(data, indent=2, sort_keys=True)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars] + "\n... truncated ..."


def _arg_value(parts: list[str], flag: str, default: str = "") -> str:
    if flag not in parts:
        return default
    index = parts.index(flag)
    if index + 1 >= len(parts):
        return default
    return parts[index + 1]


def _task_create(project: rail.Project, parts: list[str]) -> dict[str, Any]:
    title = _arg_value(parts, "--title")
    description = _arg_value(parts, "--description")
    runner = _arg_value(parts, "--runner", "codex_cli")
    role = _arg_value(parts, "--role", "research")
    workflow = _arg_value(parts, "--workflow") or None
    positional = [part for part in parts if not part.startswith("--")]
    if not title:
        title = " ".join(positional[2:]).strip()
    if not title:
        raise ValueError("task create requires --title or positional title")
    if runner not in RUNNERS:
        raise ValueError(f"unsupported runner: {runner}")
    created = project.create_task(title, description=description or title, runner=runner, role=role, workflow=workflow)
    work_order = project.create_work_order(created["task"]["id"])
    return {"created": created, "work_order": work_order}


def run_command(project: rail.Project, command: str) -> dict[str, Any]:
    parts = shlex.split(command)
    if not parts:
        raise ValueError("empty /krail command")
    head = parts[0]
    if head == "doctor":
        return project.doctor()
    if head == "sources":
        sub = parts[1] if len(parts) > 1 else "affected"
        if sub == "validate":
            return project.sources_validate()
        if sub == "list":
            return project.sources_list()
        if sub == "changed":
            return project.sources_changed()
        if sub == "affected":
            source_ids = []
            idx = 0
            while idx < len(parts):
                if parts[idx] == "--source-id" and idx + 1 < len(parts):
                    source_ids.append(parts[idx + 1])
                    idx += 1
                idx += 1
            return project.sources_affected(source_ids=source_ids or None)
        if sub == "check":
            return project.sources_check(write="--write" in parts)
        raise ValueError(f"unsupported sources command: {sub}")
    if head == "workflow":
        if len(parts) < 2:
            raise ValueError("workflow command requires a workflow id")
        if parts[1] in {"execute", "run"}:
            workflow_id = parts[2] if len(parts) > 2 else ""
        else:
            workflow_id = parts[1]
        if not workflow_id:
            raise ValueError("workflow command requires a workflow id")
        return project.execute_workflow(workflow_id, dry_run=True)
    if head == "task" and len(parts) > 1 and parts[1] == "create":
        return _task_create(project, parts)
    raise ValueError(f"unsupported /krail command: {command}")


def render_markdown(results: list[dict[str, Any]], *, project_path: Path) -> str:
    lines = [
        "## KRAIL Issue Intake",
        "",
        f"Project path: `{project_path}`",
        "",
    ]
    for item in results:
        lines.append(f"### `/krail {item['command']}`")
        lines.append("")
        if item.get("ok"):
            lines.append("Status: `ok`")
            lines.append("")
            lines.append("```json")
            lines.append(_brief(item["result"]))
            lines.append("```")
        else:
            lines.append("Status: `error`")
            lines.append("")
            lines.append(f"Error: `{item.get('error')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse /krail issue commands into safe local KRAIL actions.")
    parser.add_argument("--event", required=True, help="Path to a GitHub event JSON payload")
    parser.add_argument("--project", help="KRAIL project path. Defaults to repo root if it has rail.yaml, else examples/minimal-project.")
    parser.add_argument("--response", help="Write Markdown response to this path")
    parser.add_argument("--json-output", help="Write structured JSON result to this path")
    args = parser.parse_args()

    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    commands = extract_commands(_event_text(event))
    project_path = _project_path(args.project)
    project = rail.local(str(project_path))
    results: list[dict[str, Any]] = []
    for command in commands:
        try:
            results.append({"command": command, "ok": True, "result": run_command(project, command)})
        except Exception as exc:
            results.append({"command": command, "ok": False, "error": str(exc)})

    payload = {"project_path": str(project_path), "commands": commands, "results": results}
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    response = render_markdown(results, project_path=project_path) if commands else "No `/krail` commands found.\n"
    if args.response:
        Path(args.response).write_text(response, encoding="utf-8")
    else:
        print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
