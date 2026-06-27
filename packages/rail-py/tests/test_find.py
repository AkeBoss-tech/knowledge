from __future__ import annotations

import json
from pathlib import Path

from rail.bootstrap import bootstrap_future_project
from rail.integrity import ResearchIntegrityRepo
from rail.knowledge import KnowledgeRuntime
from rail.queues import QueueEngine


def _seed_find_project(tmp_path: Path) -> Path:
    root = bootstrap_future_project(tmp_path, name="Find Project", slug="find-project")
    topic = root / "topics" / "repo-intake.md"
    topic.write_text(
        "---\n"
        "title: Repo Intake\n"
        "kind: method\n"
        "topics:\n"
        "  - architecture-ingestion\n"
        "entities:\n"
        "  - PDDLStream\n"
        "sources:\n"
        "  - https://example.com/repo-intake\n"
        "---\n\n"
        "# Repo Intake\n\n"
        "PDDLStream repo intake depends on manifest inspection and endpoint extraction.\n"
        "- Source: https://example.com/pddlstream-repo\n"
        "- Claim: PDDLStream repositories need explicit work-order paths for parallel workers.\n",
        encoding="utf-8",
    )
    runtime = KnowledgeRuntime(root)
    runtime.graph_build(write=True)
    ResearchIntegrityRepo(root).extract_candidates_from_paths(["topics/repo-intake.md"])

    session = root / "research_plan" / "sessions" / "repo_ingest_001"
    session.mkdir(parents=True)
    (session / "result.json").write_text(
        json.dumps(
            {
                "workflow": "corptech_repo_architecture_intake",
                "status": "failed",
                "failed_step": "inspect_manifests",
                "summary": "PDDLStream manifest inspection failed.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    inventory = root / "artifacts" / "repos.json"
    inventory.write_text(
        json.dumps([{"repo": "pddlstream", "family": "application_or_api", "url": "https://github.com/demo/pddlstream"}]) + "\n",
        encoding="utf-8",
    )
    QueueEngine(root).init("repo-intake", source="artifacts/repos.json", key="repo")
    return root


def test_find_returns_typed_results_across_documents_graph_integrity_sessions_and_queues(tmp_path: Path):
    root = _seed_find_project(tmp_path)
    result = KnowledgeRuntime(root).find("PDDLStream", limit=20, rag=False, explain=True)

    result_types = {item["type"] for item in result["results"]}
    assert {"document", "entity", "claim_candidate", "source_candidate", "workflow_run", "queue_item"}.issubset(result_types)
    assert result["summary"]["by_type"]["document"] >= 1
    assert result["explain"]["mode"] == "unified_local_find"
    assert any("candidate evidence" in action for action in result["suggested_actions"])


def test_find_filters_by_type_status_and_workflow(tmp_path: Path):
    root = _seed_find_project(tmp_path)
    runtime = KnowledgeRuntime(root)

    workflow = runtime.find(
        "PDDLStream",
        types=["workflow_run"],
        status="failed",
        workflow="corptech_repo_architecture_intake",
        limit=10,
        rag=False,
    )
    assert workflow["summary"]["by_type"] == {"workflow_run": 1}
    assert workflow["results"][0]["status"] == "failed"

    documents = runtime.find("PDDLStream", types=["document"], limit=10, rag=False)
    assert documents["results"]
    assert {item["type"] for item in documents["results"]} == {"document"}

