from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.runners.base import TaskPayload
from app.runners.mcp_injector import inject_mcp_config
from app.services.policy_resolver import (
    CompletionPolicy,
    PathPolicy,
    RunnerPolicy,
    RuntimePolicy,
    SecretPolicy,
    SkillPolicy,
    ToolPolicy,
    resolve_runner_scope,
)


def _runtime_policy() -> RuntimePolicy:
    return RuntimePolicy(
        runner=RunnerPolicy(),
        paths=PathPolicy(read=["topics"], write=["topics", "artifacts"], deny=["topics/private"]),
        secrets=SecretPolicy(allow=["OPENAI_API_KEY"]),
        tools=ToolPolicy(allow=["write_repo", "execute_python"], deny=["set_secret"]),
        completion=CompletionPolicy(),
        skills=SkillPolicy(),
    )


def test_resolve_runner_scope_keeps_requested_subset_and_explicit_scope():
    scope = resolve_runner_scope(
        _runtime_policy(),
        requested_write_paths=["topics/reports"],
        requested_tools=["execute_python"],
    )

    assert scope.allowed_paths == ["topics/reports"]
    assert scope.denied_paths == ["topics/private"]
    assert scope.allowed_tools == ["execute_python"]
    assert scope.denied_tools == ["set_secret"]
    assert scope.allowed_secrets == ["OPENAI_API_KEY"]


def test_resolve_runner_scope_rejects_path_outside_policy():
    with pytest.raises(PermissionError, match="outside role policy"):
        resolve_runner_scope(_runtime_policy(), requested_write_paths=["research_plan"])


def test_task_payload_to_dict_exposes_scope_metadata():
    payload = TaskPayload(
        project_slug="demo",
        role="coding",
        task_id="task-1",
        repo_url="https://example.com/repo.git",
        branch="main",
        task_description="Implement the change",
        allowed_paths=["topics/reports"],
        denied_paths=["topics/private"],
        allowed_tools=["execute_python"],
        denied_tools=["set_secret"],
        allowed_secrets={"OPENAI_API_KEY": "secret"},
        allowed_secret_names=["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
    )

    data = payload.to_dict()

    assert data["allowed_paths"] == ["topics/reports"]
    assert data["denied_paths"] == ["topics/private"]
    assert data["allowed_tools"] == ["execute_python"]
    assert data["denied_tools"] == ["set_secret"]
    assert data["allowed_secrets"] == ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]


def test_inject_mcp_config_includes_runner_scope_env(tmp_path: Path):
    config_path = inject_mcp_config(
        tmp_path,
        project_slug="demo",
        session_id="sess-1",
        work_order_id="wo-1",
        work_order_path="research_plan/work_orders/wo-1.json",
        extra_env={
            "KRAIL_ALLOWED_WRITE_PATHS": json.dumps(["topics/reports"]),
            "KRAIL_ALLOWED_TOOLS": json.dumps(["execute_python"]),
            "KRAIL_ALLOWED_SECRETS": json.dumps(["OPENAI_API_KEY"]),
        },
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    env = payload["mcpServers"]["rail"]["env"]

    assert env["RAIL_LOCAL"] == "1"
    assert env["RAIL_WORK_ORDER_ID"] == "wo-1"
    assert env["RAIL_WORK_ORDER_PATH"] == "research_plan/work_orders/wo-1.json"
    assert json.loads(env["KRAIL_ALLOWED_WRITE_PATHS"]) == ["topics/reports"]
    assert json.loads(env["KRAIL_ALLOWED_TOOLS"]) == ["execute_python"]
    assert json.loads(env["KRAIL_ALLOWED_SECRETS"]) == ["OPENAI_API_KEY"]
