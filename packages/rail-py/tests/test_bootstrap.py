"""Tests for future project bootstrapping."""

from __future__ import annotations

import sys
import subprocess
import json
import os
from pathlib import Path

import yaml

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project


def test_bootstrap_future_project_creates_workspace_scaffold(tmp_path):
    root = bootstrap_future_project(tmp_path, name="Test Project", slug="test-project")

    assert (root / "rail.yaml").exists()
    assert (root / ".ontology/ontology.yaml").exists()
    assert (root / "research_plan/current_plan.md").exists()
    assert (root / "research_plan/assumptions.md").exists()
    assert (root / "research_plan/decisions.md").exists()
    assert (root / "research_plan/methodology.md").exists()
    assert (root / "research_plan/provenance.md").exists()
    assert (root / "research_plan/claim_evidence.md").exists()
    assert (root / "research_plan/open_questions.md").exists()
    assert (root / "research_plan/rerun_options.md").exists()
    assert (root / "research_plan/verification_summary.md").exists()
    assert (root / "research_plan/state/assumptions.json").exists()
    assert (root / "research_plan/state/sources.json").exists()
    assert (root / "research_plan/state/claims.json").exists()
    assert (root / "research_plan/state/hypotheses.json").exists()
    assert (root / "research_plan/state/source_candidates.json").exists()
    assert (root / "research_plan/state/claim_candidates.json").exists()
    assert (root / "research_plan/state/entity_candidates.json").exists()
    assert (root / "research_plan/state/conflicts.json").exists()
    assert (root / "research_plan/state/artifact_lineage.json").exists()
    assert (root / "research_plan/state/verification_runs.json").exists()
    assert (root / "sources/dependencies.yaml").exists()
    assert (root / "agents/prompts/planner.md").exists()
    assert (root / "skills/repo-contract.md").exists()
    assert (root / "skills/web-research.md").exists()
    assert (root / "skills/source-inventory.md").exists()
    assert (root / "skills/literature-review.md").exists()
    assert (root / "skills/econometric-design.md").exists()
    assert (root / "topics").is_dir()
    assert (root / "topics/inbox").is_dir()
    assert (root / "scripts/setup-workspace.sh").exists()
    assert (root / "scripts/run-verification.sh").exists()
    assert (root / "scripts/archive-workspace.sh").exists()

    rail_data = yaml.safe_load((root / "rail.yaml").read_text(encoding="utf-8"))
    assert rail_data["project"]["name"] == "Test Project"
    assert rail_data["autonomy"]["mode"] == "assisted"
    assert rail_data["autonomy"]["max_retries_per_task"] == 3
    assert rail_data["integrity"]["require_evidence_for_report_claims"] is True
    assert rail_data["workspaces"]["mode"] == "isolated"
    assert rail_data["workspaces"]["setup_script"] == "scripts/setup-workspace.sh"
    assert rail_data["project"]["mode"] == "ontology_first"
    assert rail_data["repo_contract"]["source_of_truth"] == "git"
    assert rail_data["research"]["brief_path"] == "topics/brief.md"
    assert rail_data["planner"]["task_root"] == "research_plan/tasks"
    assert rail_data["verification"]["deterministic_command"] == "scripts/run-verification.sh"
    assert "hydrated" in rail_data["lifecycle"]["phases"]
    assert "ontology_healthy" in rail_data["lifecycle"]["phases"]
    assert rail_data["auditors"]["enabled"] is True
    assert rail_data["auditors"]["fail_closed"] is True

    assert yaml.safe_load((root / "research_plan/state/assumptions.json").read_text(encoding="utf-8")) == []
    assert yaml.safe_load((root / "research_plan/state/hypotheses.json").read_text(encoding="utf-8")) == []
    assert yaml.safe_load((root / "research_plan/state/verification_runs.json").read_text(encoding="utf-8")) == []

    planner_prompt = (root / "agents/prompts/planner.md").read_text(encoding="utf-8")
    assert "# RAIL Planner Prompt" in planner_prompt
    planner_checklist = (root / "agents/checklists/planner.md").read_text(encoding="utf-8")
    assert "read the latest project audit and current blocker before advancing work" in planner_checklist

    research_cfg = yaml.safe_load((root / "agents/research.yaml").read_text(encoding="utf-8"))
    planner_cfg = yaml.safe_load((root / "agents/planner.yaml").read_text(encoding="utf-8"))
    assert planner_cfg["runner"]["approval_required"] is True
    assert research_cfg["runner"]["approval_required"] is False
    assert research_cfg["skills"]["allow_use"] is True
    assert research_cfg["skills"]["root"] == "skills"
    assert "web_research" in research_cfg["tools"]["allow"]

    research_prompt = (root / "agents/prompts/research.md").read_text(encoding="utf-8")
    assert "Do not treat web snippets as evidence" in research_prompt
    research_checklist = (root / "agents/checklists/research.md").read_text(encoding="utf-8")
    assert "separate facts, interpretations, and open questions explicitly" in research_checklist

    setup_script = (root / "scripts/setup-workspace.sh").read_text(encoding="utf-8")
    assert "packages/rail-py" in setup_script
    assert "rail --help" in setup_script

    dependency_manifest = yaml.safe_load((root / "sources/dependencies.yaml").read_text(encoding="utf-8"))
    assert dependency_manifest["documents"][0]["path"] == "topics/brief.md"


def test_bootstrap_future_project_supports_markdown_graph_mode(tmp_path):
    root = bootstrap_future_project(tmp_path, name="Graph Project", slug="graph-project", mode="markdown_graph")

    rail_data = yaml.safe_load((root / "rail.yaml").read_text(encoding="utf-8"))
    assert rail_data["project"]["mode"] == "research_first"
    assert rail_data["graph"]["mode"] == "markdown_frontmatter"
    assert "topics/**/*.md" in rail_data["graph"]["include"]

    brief = (root / "topics" / "brief.md").read_text(encoding="utf-8")
    assert "entity_type: ProjectIdea" in brief


def test_company_brain_scaffold_verification_does_not_require_panel_dataset(tmp_path):
    root = bootstrap_future_project(
        tmp_path,
        name="Company Brain",
        slug="company-brain",
        mode="markdown_graph",
        pack="company-brain",
    )

    result = subprocess.run(
        ["bash", "scripts/run-verification.sh"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "longitudinal_panel.csv" not in result.stdout


def test_cli_init_materializes_pack_workflows_by_default(tmp_path):
    target = tmp_path / "company-brain-project"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "init",
            str(target),
            "--pack",
            "company-brain",
            "--mode",
            "markdown_graph",
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["capture_inbox"] == "topics/inbox"
    assert "research_plan/graph/graph.json" in payload["graph"]["written"]
    assert "company_profile_refresh" in payload["materialized_workflows"]
    assert (target / "research_plan" / "workflows" / "company-profile-refresh.yaml").exists()
    assert (target / "research_plan" / "graph" / "graph.json").exists()


def test_cli_init_software_mode_materializes_repo_workflows(tmp_path):
    target = tmp_path / "software-project"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "init",
            str(target),
            "--knowledge-mode",
            "software",
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert "sync_recent_changes" in payload["materialized_workflows"]
    assert (target / "research_plan" / "workflows" / "sync-recent-changes.yaml").exists()


def test_cli_init_can_skip_pack_workflow_materialization(tmp_path):
    target = tmp_path / "company-brain-project"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "init",
            str(target),
            "--pack",
            "company-brain",
            "--mode",
            "markdown_graph",
            "--no-init-pack-workflows",
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert "materialized_workflows" not in payload
    assert not (target / "research_plan" / "workflows" / "company-profile-refresh.yaml").exists()


def test_cli_first_run_smoke_path_surfaces_fresh_capture(tmp_path):
    target = tmp_path / "demo-kb"

    init_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "init",
            str(target),
            "--pack",
            "research-intelligence",
            "--mode",
            "markdown_graph",
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr

    doctor_result = subprocess.run(
        [sys.executable, "-m", "rail.cli", "--local", "--path", str(target), "doctor"],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert doctor_result.returncode == 0, doctor_result.stdout + doctor_result.stderr
    doctor_payload = json.loads(doctor_result.stdout)
    assert doctor_payload["ok"] is True
    assert all(check["ok"] for check in doctor_payload["checks"])
    assert not any(item["name"] == "markdown_graph_freshness" for item in doctor_payload["warnings"])

    capture_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "--local",
            "--path",
            str(target),
            "capture",
            "PDDLStream may help bridge symbolic plans and geometric feasibility.",
            "--topic",
            "robotics",
            "--entity",
            "PDDLStream",
            "--entity-type",
            "Package",
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert capture_result.returncode == 0, capture_result.stdout + capture_result.stderr
    capture_payload = json.loads(capture_result.stdout)
    assert capture_payload["status"] == "captured"
    capture_path = capture_payload["path"]

    inbox_result = subprocess.run(
        [sys.executable, "-m", "rail.cli", "--local", "--path", str(target), "inbox", "list"],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert inbox_result.returncode == 0, inbox_result.stdout + inbox_result.stderr
    inbox_payload = json.loads(inbox_result.stdout)
    assert inbox_payload["unhandled"] == 1
    assert inbox_payload["captures"][0]["path"] == capture_path

    promote_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "--local",
            "--path",
            str(target),
            "inbox",
            "promote",
            capture_path,
            "--topic",
            "task-and-motion-planning",
            "--type",
            "method",
            "--entity",
            "PDDLStream",
            "--entity-type",
            "Package",
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert promote_result.returncode == 0, promote_result.stdout + promote_result.stderr
    promote_payload = json.loads(promote_result.stdout)
    assert promote_payload["status"] == "promoted"
    assert promote_payload["topic"]["path"] == "topics/task-and-motion-planning.md"

    topic_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "--local",
            "--path",
            str(target),
            "topic",
            "upsert",
            "task-and-motion-planning",
            "--content",
            "Reviewed PDDLStream evidence for task and motion planning.",
            "--source-path",
            capture_path,
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert topic_result.returncode == 0, topic_result.stdout + topic_result.stderr
    topic_payload = json.loads(topic_result.stdout)
    assert topic_payload["status"] == "updated"

    search_result = subprocess.run(
        [sys.executable, "-m", "rail.cli", "--local", "--path", str(target), "search", "PDDLStream feasibility", "--explain"],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert search_result.returncode == 0, search_result.stdout + search_result.stderr
    search_payload = json.loads(search_result.stdout)
    assert any(hit["path"] == capture_path for hit in search_payload["hits"])

    think_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rail.cli",
            "--local",
            "--path",
            str(target),
            "think",
            "What do we know about PDDLStream feasibility?",
            "--output",
            str(target / "artifacts" / "pddlstream-think.json"),
            "--register-integrity",
            "--title",
            "PDDLStream lifecycle smoke",
        ],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert think_result.returncode == 0, think_result.stdout + think_result.stderr
    think_payload = json.loads(think_result.stdout)
    assert any(item["path"] == capture_path for item in think_payload["evidence"])
    assert think_payload["integrity"]["status"] == "registered"
    assert think_payload["integrity"]["verification_run"]["status"] == "passed"

    integrity_result = subprocess.run(
        [sys.executable, "-m", "rail.cli", "--local", "--path", str(target), "integrity", "status"],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert integrity_result.returncode == 0, integrity_result.stdout + integrity_result.stderr
    integrity_payload = json.loads(integrity_result.stdout)
    assert integrity_payload["summary"]["artifactCount"] == 1
    assert integrity_payload["summary"]["verificationRunCount"] == 1
    assert integrity_payload["summary"]["claimCandidateCount"] >= 1
    assert integrity_payload["summary"]["status"] == "missing_evidence"

    workflow_result = subprocess.run(
        [sys.executable, "-m", "rail.cli", "--local", "--path", str(target), "workflow", "run", "weekly_literature_refresh", "--dry-run"],
        cwd=RAIL_PY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RAIL_PY_ROOT)},
    )
    assert workflow_result.returncode == 0, workflow_result.stdout + workflow_result.stderr
    workflow_payload = json.loads(workflow_result.stdout)
    assert workflow_payload["status"] == "dry_run"
    assert workflow_payload["path"].startswith("research_plan/sessions/")
