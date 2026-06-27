from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import yaml

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def test_queue_init_claim_status_and_complete(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Queue Project", slug="queue-project")
    inventory = root / "repos.csv"
    with inventory.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["repo_url", "family"])
        writer.writeheader()
        writer.writerow({"repo_url": "https://example.com/a", "family": "application_or_api"})
        writer.writerow({"repo_url": "https://example.com/b", "family": "library"})
        writer.writerow({"repo_url": "https://example.com/c", "family": "application_or_api"})
    runtime = KnowledgeRuntime(root)

    created = runtime.queue_init("repos", source="repos.csv", key="repo_url")
    claimed = runtime.queue_claim("repos", limit=1, where=["family=application_or_api"], owner="test")
    status = runtime.queue_status("repos")
    completed = runtime.queue_update_batch("repos", claimed["batch"]["batch_id"], status="done")
    final = runtime.queue_status("repos")

    assert created["items"] == 3
    assert len(claimed["batch"]["items"]) == 1
    assert status["counts"]["reserved"] == 1
    assert completed["item_ids"] == claimed["batch"]["item_ids"]
    assert final["counts"]["done"] == 1


def test_queue_release_stale_reservations(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Queue Project", slug="queue-project")
    inventory = root / "repos.json"
    inventory.write_text(json.dumps([{"repo_url": "https://example.com/a"}]), encoding="utf-8")
    runtime = KnowledgeRuntime(root)

    runtime.queue_init("repos", source="repos.json", key="repo_url")
    claimed = runtime.queue_claim("repos", limit=1, lease_minutes=-1)
    released = runtime.queue_release("repos", stale=True)

    assert released["item_ids"] == claimed["batch"]["item_ids"]
    assert runtime.queue_status("repos")["counts"]["pending"] == 1


def test_workflow_execute_accepts_inputs_and_dashboard_reports_run(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Queue Project", slug="queue-project")
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "input-flow.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "input_flow",
                "steps": [
                    {
                        "id": "write_input",
                        "kind": "command",
                        "run": "python3 -c \"from pathlib import Path; Path('artifacts/input.txt').write_text('${{ inputs.batch_path }}')\"",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    runtime = KnowledgeRuntime(root)

    result = runtime.workflow_execute("input-flow", inputs={"batch_path": "research_plan/queues/repos/claims/batch.json"})
    dashboard = runtime.workflow_dashboard()

    assert result["status"] == "done"
    assert (root / "artifacts" / "input.txt").read_text(encoding="utf-8") == "research_plan/queues/repos/claims/batch.json"
    assert dashboard["counts"]["done"] >= 1
    assert dashboard["sessions"][0]["result_present"] is True


def test_workflow_outputs_schema_marks_run_failed(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Queue Project", slug="queue-project")
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "schema-flow.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "schema_flow",
                "steps": [{"id": "noop", "kind": "command", "run": "true"}],
                "outputs": {"summary": "steps.noop.output"},
                "outputs_schema": {"type": "object", "required": ["repositories_reviewed"]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    runtime = KnowledgeRuntime(root)

    result = runtime.workflow_execute("schema-flow")

    assert result["status"] == "failed"
    assert "output.repositories_reviewed is required" in result["schema_errors"]


def test_graph_summary_and_diff(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Queue Project", slug="queue-project")
    topic = root / "topics" / "demo.md"
    topic.parent.mkdir(parents=True, exist_ok=True)
    topic.write_text("---\ntitle: Demo\ntopics: [demo]\nentities: [Repo]\n---\nBody\n", encoding="utf-8")
    runtime = KnowledgeRuntime(root)

    built = runtime.graph_build(write=True)
    summary = runtime.graph_summary()
    topic.write_text("---\ntitle: Demo\ntopics: [demo]\nentities: [Repo, API]\n---\nBody\n", encoding="utf-8")
    diff = runtime.graph_diff()

    assert built["counts"]["documents"] >= 1
    assert summary["counts"]["documents"] >= 1
    assert any("entity:api" == item for item in diff["nodes"]["added"])


def test_repo_inspect_detects_manifests_and_endpoint_files(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Queue Project", slug="queue-project")
    repo = root / "sample-repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repo / "app.py").write_text("@app.get('/health')\ndef health(): pass\n", encoding="utf-8")
    runtime = KnowledgeRuntime(root)

    result = runtime.repo_inspect("sample-repo")

    assert result["status"] == "inspected"
    assert "python" in result["frameworks"]
    assert "app.py" in result["endpoint_files"]
