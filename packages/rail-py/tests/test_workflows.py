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


def _set_permissions_rules(root: Path, rules: list[dict]) -> None:
    manifest_path = root / "rail.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    manifest["permissions"] = {"rules": rules}
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


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


def test_software_workflow_templates_are_materialized_with_repo_steps(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Software Project", slug="software-project", knowledge_mode="software")
    runtime = KnowledgeRuntime(root)

    initialized = runtime.workflow_init("map_codebase", force=True)
    shown = runtime.workflow_show("map_codebase")
    listed = runtime.workflow_list()

    commands = [step.get("run") for step in shown["workflow"]["steps"] if step.get("kind") == "command"]

    assert initialized["template"] == "map_codebase"
    assert shown["workflow"]["id"] == "map_codebase"
    assert "krail --local repo snapshot ." in commands
    assert "krail --local repo inventory ." in commands
    assert "krail --local repo symbols ." in commands
    assert "sync_recent_changes" in listed["mode_workflows"]


def test_capture_denial_is_blocked_and_audited(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    _set_permissions_rules(root, [{"path": "topics/inbox/*.md", "allowed_roles": ["triager"]}])

    result = KnowledgeRuntime(root).capture(text="restricted note")

    assert result["status"] == "blocked"
    assert result["permission"] == "denied"
    assert result["action"] == "write"
    assert result["reason"] == "allowlist_not_matched"
    assert result["target"].startswith("topics/inbox/")
    assert (root / "topics" / "inbox").exists()
    assert not list((root / "topics" / "inbox").glob("*.md"))
    audit = [json.loads(line) for line in (root / "research_plan" / "audit" / "access.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["action"] == "write"
    assert audit[-1]["decision"] == "denied"
    assert audit[-1]["target"] == result["target"]


def test_topic_upsert_denial_is_blocked_and_preserves_existing_file(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    topic_path = root / "topics" / "restricted.md"
    topic_path.write_text(
        "---\n"
        "allowed_roles:\n"
        "  - reviewer\n"
        "---\n\n"
        "# Restricted\n\nOriginal content.\n",
        encoding="utf-8",
    )

    result = KnowledgeRuntime(root).topic_upsert("restricted", content="new content")

    assert result["status"] == "blocked"
    assert result["permission"] == "denied"
    assert result["target"] == "topics/restricted.md"
    assert "new content" not in topic_path.read_text(encoding="utf-8")
    audit = [json.loads(line) for line in (root / "research_plan" / "audit" / "access.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["action"] == "write"
    assert audit[-1]["target"] == "topics/restricted.md"


def test_inbox_promote_denial_blocks_target_write_without_partial_updates(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    _set_permissions_rules(root, [{"path": "topics/restricted-topic.md", "allowed_roles": ["reviewer"]}])
    capture = root / "topics" / "inbox" / "capture.md"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text("---\ntitle: Capture\n---\n\nCaptured body.\n", encoding="utf-8")

    result = KnowledgeRuntime(root).inbox_promote("topics/inbox/capture.md", topic="restricted-topic")

    assert result["status"] == "blocked"
    assert result["permission"] == "denied"
    assert result["target"] == "topics/restricted-topic.md"
    promoted_capture = capture.read_text(encoding="utf-8")
    assert "triage_status" not in promoted_capture
    assert not (root / "topics" / "restricted-topic.md").exists()


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


def test_workflow_execute_dry_run_denial_is_blocked_and_audited(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "restricted.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "restricted",
                "permissions": {"allowed_roles": ["reviewer"]},
                "steps": [{"id": "noop", "kind": "command", "run": "true"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("restricted", dry_run=True)

    assert result["status"] == "blocked"
    assert result["permission"] == "denied"
    assert result["action"] == "execute"
    assert result["workflow"] == "restricted"
    audit = [json.loads(line) for line in (root / "research_plan" / "audit" / "access.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["action"] == "execute"
    assert audit[-1]["decision"] == "denied"
    assert audit[-1]["target"] == "restricted"


def test_dispatch_task_denial_blocks_workflow_agent_launch(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "restricted.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "restricted",
                "permissions": {"allowed_roles": ["reviewer"]},
                "steps": [{"id": "noop", "kind": "command", "run": "true"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    created = runtime.create_task("Restricted Workflow", workflow="restricted")
    result = runtime.dispatch_task(created["task"]["id"], dry_run=True)

    assert result["status"] == "blocked"
    assert result["permission"] == "denied"
    assert result["action"] == "dispatch_agent"
    assert result["workflow"] == "restricted"
    task = json.loads((root / created["path"]).read_text(encoding="utf-8"))
    assert task["status"] == "blocked"
    assert "dispatch agent denied" in task["blocker"]


def test_dispatch_task_honors_dispatch_specific_denials(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "dispatch-locked.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "dispatch_locked",
                "permissions": {"deny_actions": ["dispatch_agent"]},
                "steps": [{"id": "noop", "kind": "command", "run": "true"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    created = runtime.create_task("Dispatch Locked Workflow", workflow="dispatch-locked")
    result = runtime.dispatch_task(created["task"]["id"], dry_run=True)

    assert result["status"] == "blocked"
    assert result["permission"] == "denied"
    assert result["action"] == "dispatch_agent"
    assert result["workflow"] == "dispatch-locked"
    assert result["reason"] == "action_denied:dispatch_agent"


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


def test_workflow_execute_think_step(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "think.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "think_flow",
                "steps": [
                    {
                        "id": "synthesize",
                        "kind": "think",
                        "mode": "deterministic",
                        "query": "project objective",
                        "limit": 2,
                        "output_path": "artifacts/think-result.json",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    validation = runtime.workflow_validate("think")
    result = runtime.workflow_execute("think")

    assert validation["ok"] is True
    assert result["status"] == "done"
    assert result["steps"][0]["kind"] == "think"
    assert result["steps"][0]["think"]["mode"] == "deterministic"
    assert result["steps"][0]["think"]["citations"]
    assert result["steps"][0]["output_path"] == "artifacts/think-result.json"
    assert json.loads((root / "artifacts" / "think-result.json").read_text(encoding="utf-8"))["mode"] == "deterministic"
    assert result["steps"][0]["integrity"]["status"] == "registered"


def test_workflow_when_if_repeat_and_foreach(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "control-flow.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "control_flow",
                "inputs": {"risk_level": "high"},
                "steps": [
                    {
                        "id": "unit_tests",
                        "kind": "command",
                        "run": "python3 -c 'import json; print(json.dumps({\"needs_repair\": True}))'",
                        "capture": {"from": "stdout", "format": "json"},
                    },
                    {
                        "id": "skipped_integration",
                        "kind": "command",
                        "when": 'steps.unit_tests.output.needs_repair == false',
                        "run": "exit 9",
                    },
                    {
                        "id": "choose_review",
                        "kind": "if",
                        "condition": 'inputs.risk_level in ["high", "critical"]',
                        "then": [
                            {
                                "id": "deep_review",
                                "kind": "command",
                                "run": "python3 -c \"from pathlib import Path; Path('artifacts/deep.txt').write_text('yes')\"",
                            }
                        ],
                        "else": [
                            {
                                "id": "standard_review",
                                "kind": "command",
                                "run": "exit 8",
                            }
                        ],
                    },
                    {
                        "id": "repair_cycle",
                        "kind": "repeat",
                        "max_iterations": 3,
                        "until": 'steps.verify.output.done == true',
                        "steps": [
                            {
                                "id": "verify",
                                "kind": "command",
                                "run": "python3 -c \"import json; from pathlib import Path; p=Path('artifacts/count.txt'); n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); print(json.dumps({'done': n >= 1}))\"",
                                "capture": {"from": "stdout", "format": "json"},
                                "on_failure": "continue",
                            },
                            {
                                "id": "repair",
                                "kind": "command",
                                "when": 'steps.verify.output.done != true',
                                "run": "python3 -c \"from pathlib import Path; Path('artifacts/repaired.txt').write_text('yes')\"",
                            },
                        ],
                    },
                    {
                        "id": "versions",
                        "kind": "foreach",
                        "items": ["3.12", "3.13"],
                        "as": "python_version",
                        "max_items": 3,
                        "steps": [
                            {
                                "id": "write_version",
                                "kind": "command",
                                "run": "python3 -c \"from pathlib import Path; Path('artifacts/versions.txt').open('a').write('${{ loop.item }}\\n')\"",
                            }
                        ],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    validation = runtime.workflow_validate("control-flow")
    result = runtime.workflow_execute("control-flow")

    assert validation["ok"] is True
    assert result["status"] == "done"
    assert result["steps"][1]["status"] == "skipped"
    assert result["steps"][2]["branch"] == "then"
    assert result["steps"][3]["status"] == "done"
    assert result["steps"][3]["iterations"] == 2
    assert result["steps"][3]["stop_reason"] == "condition_met"
    assert result["steps"][4]["status"] == "done"
    assert (root / "artifacts" / "deep.txt").read_text(encoding="utf-8") == "yes"
    assert (root / "artifacts" / "repaired.txt").read_text(encoding="utf-8") == "yes"
    assert (root / "artifacts" / "versions.txt").read_text(encoding="utf-8").splitlines() == ["3.12", "3.13"]


def test_workflow_repeat_fails_after_max_iterations(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "repeat-fail.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "repeat_fail",
                "steps": [
                    {
                        "id": "never_done",
                        "kind": "repeat",
                        "max_iterations": 2,
                        "until": 'steps.verify.output.done == true',
                        "steps": [
                            {
                                "id": "verify",
                                "kind": "command",
                                "run": "python3 -c 'import json; print(json.dumps({\"done\": False}))'",
                                "capture": {"from": "stdout", "format": "json"},
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("repeat-fail")

    assert result["status"] == "failed"
    assert result["failed_step"] == "never_done"
    assert result["steps"][0]["iterations"] == 2
    assert result["steps"][0]["stop_reason"] == "max_iterations_reached"


def test_workflow_approval_pauses_and_resumes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRAIL_ACTOR", "local:reviewer")
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "approval-flow.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "approval_flow",
                "steps": [
                    {
                        "id": "prepare",
                        "kind": "command",
                        "run": "python3 -c \"from pathlib import Path; Path('artifacts/prepared.txt').write_text('yes')\"",
                    },
                    {
                        "id": "approve_plan",
                        "kind": "approval",
                        "title": "Approve plan",
                        "description": "Review the generated plan.",
                        "subject": {"step": "prepare"},
                        "reviewers": {"teams": ["platform"]},
                        "minimum_approvals": 1,
                        "prevent_self_approval": False,
                    },
                    {
                        "id": "release",
                        "kind": "command",
                        "when": 'steps.approve_plan.decision == "approved"',
                        "run": "python3 -c \"from pathlib import Path; Path('artifacts/released.txt').write_text('yes')\"",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    paused = runtime.workflow_execute("approval-flow")
    approval_id = paused["pending_approval_id"]

    assert paused["status"] == "awaiting_approval"
    assert approval_id
    assert (root / "research_plan" / "sessions" / paused["run_id"] / "state.json").exists()
    assert runtime.approval_show(approval_id)["approval"]["status"] == "pending"

    decision = runtime.approval_decide(approval_id, decision="approved", comment="Reviewed.")
    resumed = runtime.workflow_resume(paused["run_id"])

    assert decision["approval"]["status"] == "approved"
    assert resumed["status"] == "done"
    assert [step["id"] for step in resumed["steps"]] == ["prepare", "approve_plan", "release"]
    assert resumed["steps"][1]["status"] == "done"
    assert resumed["steps"][1]["decision"] == "approved"
    assert (root / "artifacts" / "prepared.txt").read_text(encoding="utf-8") == "yes"
    assert (root / "artifacts" / "released.txt").read_text(encoding="utf-8") == "yes"
    decisions_path = root / "research_plan" / "approvals" / f"{approval_id}.decisions.jsonl"
    assert json.loads(decisions_path.read_text(encoding="utf-8").splitlines()[0])["decision"] == "approved"


def test_workflow_approval_rejection_fails_on_resume(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRAIL_ACTOR", "local:reviewer")
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "approval-reject.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "approval_reject",
                "steps": [
                    {"id": "prepare", "kind": "command", "run": "true"},
                    {
                        "id": "approval",
                        "kind": "approval",
                        "prevent_self_approval": False,
                        "subject": {"step": "prepare"},
                    },
                    {"id": "after", "kind": "command", "run": "exit 9"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    paused = runtime.workflow_execute("approval-reject")
    runtime.approval_decide(paused["pending_approval_id"], decision="rejected", comment="No.")
    resumed = runtime.workflow_resume(paused["run_id"])

    assert resumed["status"] == "failed"
    assert resumed["failed_step"] == "approval"
    assert resumed["steps"][1]["decision"] == "rejected"


def test_workflow_resume_rechecks_permissions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRAIL_ROLES", "reviewer")
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "restricted-resume.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "restricted_resume",
                "permissions": {"allowed_roles": ["reviewer"]},
                "steps": [
                    {"id": "prepare", "kind": "command", "run": "true"},
                    {
                        "id": "approval",
                        "kind": "approval",
                        "prevent_self_approval": False,
                        "subject": {"step": "prepare"},
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    paused = runtime.workflow_execute("restricted-resume")
    monkeypatch.delenv("KRAIL_ROLES", raising=False)
    blocked = runtime.workflow_resume(paused["run_id"])

    assert paused["status"] == "awaiting_approval"
    assert blocked["status"] == "blocked"
    assert blocked["permission"] == "denied"
    assert blocked["action"] == "execute"
    assert blocked["workflow"] == "restricted_resume"
    assert blocked["run_id"] == paused["run_id"]


def test_workflow_step_runs_child_and_exposes_outputs(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "child.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "child",
                "outputs": {
                    "digest": {"from": "steps.validate.output.digest"},
                    "validated": {"from": "steps.validate.output.validated"},
                },
                "steps": [
                    {
                        "id": "validate",
                        "kind": "command",
                        "run": "python3 -c 'import json; print(json.dumps({\"digest\": \"sha256:test\", \"validated\": True}))'",
                        "capture": {"from": "stdout", "format": "json"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (workflow_dir / "parent.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "parent",
                "inputs": {"specification": "spec.yaml"},
                "steps": [
                    {
                        "id": "validate_specification",
                        "kind": "workflow",
                        "workflow": "child",
                        "with": {"specification": "${{ inputs.specification }}"},
                        "expose": {"specification_digest": "digest", "validated_specification": "validated"},
                    },
                    {
                        "id": "write_digest",
                        "kind": "command",
                        "run": "python3 -c \"from pathlib import Path; Path('artifacts/digest.txt').write_text('${{ steps.validate_specification.output.specification_digest }}')\"",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("parent")
    child_step = result["steps"][0]
    child_status = runtime.workflow_status(child_step["child_run_id"])

    assert result["status"] == "done"
    assert child_step["status"] == "done"
    assert child_step["output"]["specification_digest"] == "sha256:test"
    assert child_status["parent_run_id"] == result["run_id"]
    assert child_status["parent_step_path"].endswith("validate_specification")
    assert child_status["call_depth"] == 1
    assert (root / "artifacts" / "digest.txt").read_text(encoding="utf-8") == "sha256:test"


def test_workflow_step_rejects_recursion(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "self-call.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "self_call",
                "steps": [
                    {"id": "again", "kind": "workflow", "workflow": "self_call"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("self-call")

    assert result["status"] == "failed"
    assert result["steps"][0]["error"] == "recursive workflow call rejected"


def test_workflow_parallel_block_aggregates_branch_outputs(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "parallel.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "parallel_flow",
                "steps": [
                    {
                        "id": "reviews",
                        "kind": "parallel",
                        "max_parallel": 2,
                        "fail_fast": False,
                        "branches": [
                            {
                                "id": "security",
                                "read_only": True,
                                "resources": {"read": ["candidate"]},
                                "steps": [
                                    {
                                        "id": "review",
                                        "kind": "command",
                                        "run": "python3 -c 'import json; print(json.dumps({\"finding\": \"none\"}))'",
                                        "capture": {"from": "stdout", "format": "json"},
                                    }
                                ],
                            },
                            {
                                "id": "correctness",
                                "read_only": True,
                                "resources": {"read": ["candidate"]},
                                "steps": [
                                    {
                                        "id": "review",
                                        "kind": "command",
                                        "run": "python3 -c 'import json; print(json.dumps({\"finding\": \"ok\"}))'",
                                        "capture": {"from": "stdout", "format": "json"},
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("parallel")

    assert result["status"] == "done"
    reviews = result["steps"][0]
    assert reviews["status"] == "done"
    assert reviews["branches"]["security"]["output"]["review"]["finding"] == "none"
    assert reviews["branches"]["correctness"]["output"]["review"]["finding"] == "ok"


def test_workflow_needs_dag_fans_out_and_joins_outputs(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "dag.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "dag_flow",
                "dag": {"max_concurrency": 2},
                "outputs": {"joined": {"from": "steps.join.output.joined"}},
                "steps": [
                    {
                        "id": "extract_a",
                        "kind": "command",
                        "run": "python3 -c 'import json; print(json.dumps({\"value\": \"A\"}))'",
                        "capture": {"from": "stdout", "format": "json"},
                    },
                    {
                        "id": "extract_b",
                        "kind": "command",
                        "run": "python3 -c 'import json; print(json.dumps({\"value\": \"B\"}))'",
                        "capture": {"from": "stdout", "format": "json"},
                    },
                    {
                        "id": "join",
                        "kind": "command",
                        "needs": ["extract_a", "extract_b"],
                        "run": "python3 -c 'import json; print(json.dumps({\"joined\": \"${{ steps.extract_a.output.value }}${{ steps.extract_b.output.value }}\"}))'",
                        "capture": {"from": "stdout", "format": "json"},
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("dag")

    assert result["status"] == "done"
    assert result["output"]["joined"] == "AB"
    assert result["steps"][2]["needs"] == ["extract_a", "extract_b"]


def test_workflow_needs_blocks_dependents_after_failure(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "blocked-dag.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "blocked_dag",
                "steps": [
                    {"id": "fail", "kind": "command", "run": "exit 5", "on_failure": "continue"},
                    {"id": "dependent", "kind": "command", "needs": ["fail"], "run": "true"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("blocked-dag")

    assert result["status"] == "failed"
    assert result["steps"][1]["status"] == "blocked"
    assert result["steps"][1]["reason"] == "dependency_not_successful"


def test_workflow_command_retry_policy_and_timeout_seconds(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "retry-policy.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "retry_policy",
                "steps": [
                    {
                        "id": "flaky",
                        "kind": "command",
                        "retry": {"max_attempts": 2, "backoff_seconds": 0},
                        "timeout_seconds": 5,
                        "run": "python3 -c \"from pathlib import Path; p=Path('artifacts/flaky.txt'); exists=p.exists(); p.parent.mkdir(exist_ok=True); p.write_text('seen'); raise SystemExit(0 if exists else 1)\"",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_execute("retry-policy")

    assert result["status"] == "done"
    assert result["steps"][0]["attempts"] == 2


def test_dispatch_creates_session_result_template(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    created = runtime.create_task("Doctor task", runner="codex_cli", role="doctor")

    result = runtime.dispatch_task(created["task"]["id"], dry_run=True)

    session_dir = root / "research_plan" / "sessions" / result["session_id"]
    assert (session_dir / "session_result.template.json").exists()
    work_order = json.loads((session_dir / "work_order.json").read_text(encoding="utf-8"))
    assert work_order["session_result_path"].endswith("session_result.json")


def test_dispatch_auto_runner_falls_back_to_available_cli(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)
    monkeypatch.setenv("CODEX_CLI_COMMAND", "/missing/codex")
    monkeypatch.setenv("CLAUDE_CODE_COMMAND", sys.executable)

    created = runtime.create_task("Fallback task", runner="auto", role="doctor")
    result = runtime.dispatch_task(created["task"]["id"], dry_run=True)

    assert created["task"]["runner"] == "claude_code"
    assert result["runner"] == "claude_code"
    assert result["work_order"]


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


def test_pack_workflow_show_guides_materialization(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Company Project", slug="company-project")
    runtime = KnowledgeRuntime(root)
    runtime.use_pack("company-brain")

    listed = runtime.workflow_list()
    shown = runtime.workflow_show("company_profile_refresh")
    execution = runtime.workflow_execute("company_profile_refresh", dry_run=True)
    run_result = runtime.workflow_run("company_profile_refresh", dry_run=True)
    schedule = runtime.schedule_install("company_profile_refresh", dry_run=True)
    initialized = runtime.workflow_init("company_profile_refresh")

    available = {item["id"]: item for item in listed["available"]}
    assert available["company_profile_refresh"]["status"] == "template_available"
    assert shown["status"] == "template_available"
    assert shown["materialized"] is False
    assert shown["readiness"] == "init_required"
    assert "workflow init company_profile_refresh" in shown["next_action"]
    assert execution["status"] == "not_materialized"
    assert "workflow init company_profile_refresh" in execution["message"]
    assert run_result["status"] == "not_materialized"
    assert run_result["next_action"] == "krail --local workflow init company_profile_refresh"
    assert schedule["status"] == "not_materialized"
    assert initialized["template"] == "company_profile_refresh"
    assert initialized["workflow"]["steps"][1]["runner"] == "auto"


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


def test_mode_workflows_are_discoverable_and_materializable(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Personal Brain", slug="personal-brain", knowledge_mode="personal")
    runtime = KnowledgeRuntime(root)

    listed = runtime.workflow_list()
    initialized = runtime.workflow_init("triage_inbox")
    shown = runtime.workflow_show("triage_inbox")

    available = {item["id"]: item for item in listed["available"]}
    assert "triage_inbox" in listed["mode_workflows"]
    assert available["triage_inbox"]["source"] == "mode"
    assert available["triage_inbox"]["template"] == "triage_inbox"
    assert initialized["status"] == "written"
    assert initialized["template"] == "triage_inbox"
    assert shown["workflow"]["steps"][1]["run"] == "krail --local inbox list"


def test_workflow_list_distinguishes_materialized_templates_and_invalid_specs(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Company Project", slug="company-project")
    runtime = KnowledgeRuntime(root)
    runtime.use_pack("company-brain")
    runtime.workflow_init("company_profile_refresh")
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "broken.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "broken",
                "steps": [{"id": "bad", "kind": "agent", "runner": "missing_cli"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runtime.workflow_list()

    available = {item["id"]: item for item in result["available"]}
    assert available["company_profile_refresh"]["status"] == "materialized"
    assert available["company_profile_refresh"]["readiness"] == "ready"
    assert available["company_profile_refresh"]["next_action"] == "krail --local workflow execute company_profile_refresh --dry-run"
    assert available["triage_inbox"]["status"] == "template_available"
    assert available["triage_inbox"]["readiness"] == "init_required"
    assert available["triage_inbox"]["next_action"] == "krail --local workflow init triage_inbox"
    assert available["broken"]["status"] == "invalid"
    assert available["broken"]["source"] == "local"
    assert available["broken"]["readiness"] == "repair_required"
    assert available["broken"]["next_action"] == "krail --local workflow validate broken"
    assert "unknown runner" in "\n".join(available["broken"]["errors"])
    assert result["summary"]["materialized"] == 1
    assert result["summary"]["invalid"] == 1
    assert result["summary"]["declared_only"] == 0
    assert result["summary"]["template_available"] == len(
        [item for item in result["available"] if item["status"] == "template_available"]
    )


def test_rich_wiki_generation_workflow_uses_wiki_agent(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Research Wiki", slug="research-wiki", knowledge_mode="research")
    runtime = KnowledgeRuntime(root)

    listed = runtime.workflow_list()
    initialized = runtime.workflow_init("rich_wiki_generation")
    shown = runtime.workflow_show("rich_wiki_generation")
    dry_run = runtime.workflow_execute("rich_wiki_generation", dry_run=True)

    available = {item["id"]: item for item in listed["available"]}
    agent_step = next(step for step in initialized["workflow"]["steps"] if step["kind"] == "agent")

    assert "rich_wiki_generation" in listed["mode_workflows"]
    assert available["rich_wiki_generation"]["template"] == "rich_wiki_generation"
    assert initialized["template"] == "rich_wiki_generation"
    assert agent_step["role"] == "wiki"
    assert shown["validation"]["ok"] is True
    assert any(step["step"].get("run") == "krail --local wiki check" for step in dry_run["steps"] if step["kind"] == "command")


def test_ci_init_writes_matrix_ci_template(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Workflow Project", slug="workflow-project")
    runtime = KnowledgeRuntime(root)

    result = runtime.ci_init()
    content = (root / ".github" / "workflows" / "krail-ci.yml").read_text(encoding="utf-8")

    assert result == {"status": "written", "path": ".github/workflows/krail-ci.yml"}
    assert 'python-version: ["3.11", "3.12", "3.13"]' in content
    assert "pip install -e packages/rail-py -e packages/mcp-server" in content
    assert "pip install krail rail-mcp" in content
    assert "rail-mcp --help >/dev/null" in content
