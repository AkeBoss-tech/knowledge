from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def test_krail_agent_scaffold_and_prompt(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)

    result = runtime.scaffold_krail_agents()
    prompt = runtime.agent_prompt("doctor", task="Check workflow health")

    assert "agents/prompts/doctor.md" in result["written"] or "agents/prompts/doctor.md" in result["skipped"]
    assert (root / "skills/krail-platform.md").exists()
    assert prompt["role"] == "doctor"
    assert "KRAIL Doctor Agent Prompt" in prompt["prompt"]
    assert "Check workflow health" in prompt["prompt"]


def test_workflow_init_show_and_dry_run(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)

    created = runtime.workflow_init("weekly_review", template="weekly_research_review")
    shown = runtime.workflow_show("weekly_review")
    validation = runtime.workflow_validate("weekly_review")
    dry_run = runtime.workflow_execute("weekly_review", dry_run=True)

    assert created["status"] == "written"
    assert created["path"] == "research_plan/workflows/weekly-review.yaml"
    assert created["template"] == "weekly_research_review"
    assert shown["workflow"]["id"] == "weekly_review"
    assert validation["ok"] is True
    assert dry_run["status"] == "dry_run"
    assert len(dry_run["steps"]) == 4
    assert dry_run["duration_seconds"] >= 0
    assert (root / dry_run["path"] / "result.json").exists()


def test_workflow_validate_rejects_bad_specs(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "bad.yaml").write_text(
        yaml.safe_dump({"id": "bad", "steps": [{"id": "x", "kind": "agent", "runner": "missing_cli"}]}),
        encoding="utf-8",
    )

    validation = runtime.workflow_validate("bad")
    execution = runtime.workflow_execute("bad")

    assert validation["ok"] is False
    assert "unknown runner" in "\n".join(validation["errors"])
    assert execution["status"] == "invalid"


def test_workflow_execute_command_steps(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "command-only.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "command_only",
                "steps": [
                    {
                        "id": "write_artifact",
                        "kind": "command",
                        "run": "python3 -c \"from pathlib import Path; Path('artifacts/workflow.txt').write_text('ok')\"",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("command-only")

    assert result["status"] == "done"
    assert result["steps"][0]["status"] == "done"
    assert result["failed_step"] is None
    assert (root / "artifacts" / "workflow.txt").read_text(encoding="utf-8") == "ok"
    stored = json.loads((root / result["path"] / "result.json").read_text(encoding="utf-8"))
    assert stored["workflow"] == "command_only"
    runs = runtime.workflow_runs()
    assert runs["runs"][0]["run_id"] == result["run_id"]
    status = runtime.workflow_status(result["run_id"])
    assert status["status"] == "done"


def test_workflow_execute_continue_failure_policy(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "continue.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "continue",
                "steps": [
                    {"id": "fail", "kind": "command", "run": "exit 7", "on_failure": "continue"},
                    {"id": "write", "kind": "command", "run": "python3 -c \"from pathlib import Path; Path('artifacts/continued.txt').write_text('ok')\""},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("continue")

    assert result["status"] == "failed"
    assert result["failed_step"] == "fail"
    assert (root / "artifacts" / "continued.txt").read_text(encoding="utf-8") == "ok"


def test_dispatch_creates_session_result_template(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    created = runtime.create_task("Doctor task", runner="codex_cli", role="doctor")

    result = runtime.dispatch_task(created["task"]["id"], dry_run=True)

    session_dir = root / "research_plan" / "sessions" / result["session_id"]
    assert (session_dir / "session_result.template.json").exists()
    work_order = json.loads((session_dir / "work_order.json").read_text(encoding="utf-8"))
    assert work_order["session_result_path"].endswith("session_result.json")


def test_schedule_install_list_and_remove(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    runtime.workflow_init("source_refresh", template="source_refresh")

    installed = runtime.schedule_install("source_refresh", schedule="15 9 * * 1", dry_run=True)
    listed = runtime.schedule_list()

    wrapper = root / installed["schedule"]["wrapper"]
    assert installed["status"] == "written"
    assert wrapper.exists()
    assert "--dry-run" in wrapper.read_text(encoding="utf-8")
    assert listed["schedules"][0]["workflow"] == "source_refresh"

    removed = runtime.schedule_remove("source_refresh")
    assert "scripts/krail-run-source-refresh.sh" in removed["removed"]
    assert not wrapper.exists()


def test_workflow_list_filters_malformed_pack_entries(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    runtime.krail_dir.mkdir(exist_ok=True)
    runtime.active_pack_path.write_text(
        yaml.safe_dump({"id": "demo", "workflows": ["daily_refresh", {"task_root": "research_plan/tasks"}]}),
        encoding="utf-8",
    )

    result = runtime.workflow_list()

    assert result["workflows"] == ["daily_refresh"]
    assert result["warnings"]
