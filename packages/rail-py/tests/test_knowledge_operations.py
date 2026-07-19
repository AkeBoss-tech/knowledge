from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.actions import ActionDefinition, ActionRegistry, ActionValidationError
from rail.bootstrap import bootstrap_future_project
from rail.docs import query_builtin_doc, search_builtin_docs
from rail.knowledge import KnowledgeRuntime
from rail.local import LocalEngine
from rail.project import Project
from rail.retrieval import DeterministicQueryPlanner, reciprocal_rank_fusion


def test_action_registry_validates_inputs_outputs_and_supports_safe_dry_run():
    registry = ActionRegistry()
    registry.register(
        ActionDefinition(
            id="double",
            description="Double an integer.",
            input_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
            output_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
        ),
        lambda payload: {"value": payload["value"] * 2},
    )

    preview = registry.execute("double", {"value": 3}, dry_run=True)
    executed = registry.execute("double", {"value": 3})

    assert preview["status"] == "dry_run"
    assert preview["action"]["version"] == "krail.action/v1"
    assert executed["output"] == {"value": 6}
    with pytest.raises(ActionValidationError, match="input.value is required"):
        registry.execute("double", {})


def test_rrf_uses_rank_not_incomparable_raw_scores():
    fused = reciprocal_rank_fusion(
        {
            "lexical": [
                {"path": "topics/a.md", "score": 1_000_000},
                {"path": "topics/b.md", "score": 1},
            ],
            "vector": [
                {"path": "topics/b.md", "score": 0.51},
                {"path": "topics/a.md", "score": 0.99},
            ],
        },
        limit=2,
    )

    assert [item["path"] for item in fused] == ["topics/a.md", "topics/b.md"]
    assert fused[0]["rank_signals"]["lexical"]["raw_score"] == 1_000_000
    assert fused[0]["score"] == 1.0


def test_query_planner_selects_source_specific_retrievers():
    plan = DeterministicQueryPlanner().plan("Who owns the recently changed API implementation?")

    assert plan.intent == "code_recent_ownership"
    assert {"lexical", "vector", "graph", "exact_code", "recency", "ownership"} <= set(plan.retrievers)


def test_search_v2_returns_explicit_trust_context_and_trace(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Operations Project", slug="operations-project")
    (root / "topics" / "architecture.md").write_text(
        "---\ntitle: Runtime Architecture\nupdated: 2026-07-17T10:00:00+00:00\n---\n\n"
        "# Runtime Architecture\n\nThe action registry provides typed execution contracts.\n\n"
        "## Replay\n\nReplay is deferred to the durable execution release.\n",
        encoding="utf-8",
    )
    runtime = KnowledgeRuntime(root)

    result = runtime.search("typed execution contracts", limit=5, explain=True, rag=False)
    hit = result["hits"][0]

    assert hit["path"] == "topics/architecture.md"
    assert hit["trust_state"] == "reviewed"
    assert hit["source_type"] == "topic"
    assert hit["context"]["kind"] == "markdown_section"
    assert result["retrieval_trace"]["ranker"] == "deterministic_rrf_v2"
    assert result["evidence_packet"]["evidence"][0]["path"] == "topics/architecture.md"
    assert result["evidence_packet"]["citations"][0]["ref"] == "[1]"


def test_builtin_actions_are_catalogued_and_capture_stays_untrusted(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Action Project", slug="action-project")
    runtime = KnowledgeRuntime(root)

    catalog = runtime.action_list()
    preview = runtime.action_execute("capture-note", {"text": "candidate observation"}, dry_run=True)
    executed = runtime.action_execute("capture-note", {"text": "candidate observation"}, dry_run=False)

    assert {item["id"] for item in catalog["actions"]} >= {"search-project", "capture-note"}
    assert preview["action"]["effect"] == "local_write"
    assert executed["output"]["status"] == "captured"
    capture_path = root / executed["output"]["path"]
    assert capture_path.is_relative_to(root / "topics" / "inbox")


def test_trigger_vocabulary_aliases_existing_listener_storage(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Trigger Project", slug="trigger-project")
    project = Project(slug="trigger-project", backend=LocalEngine(project_path=root))

    assert project.trigger_list() == project.listener_list()
    assert project.retriever_list()["schema_version"] == "krail.retriever/v1"


def test_unified_run_inspector_lists_summarizes_and_traces_workflows(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Run Project", slug="run-project")
    runtime = KnowledgeRuntime(root)
    runtime.workflow_init("source_refresh")

    executed = runtime.workflow_execute("source_refresh", dry_run=True)
    listed = runtime.run_list(kind="workflow")
    summary = runtime.run_show(executed["run_id"], summary=True)
    trace = runtime.run_trace(executed["run_id"])

    assert listed["runs"][0]["run_id"] == executed["run_id"]
    assert summary["status"] == "dry_run"
    assert summary["step_count"] > 0
    assert trace["durability"] == "snapshot_result_v1"
    assert trace["spans"]


def test_builtin_docs_and_versioned_agent_guide_are_available_without_project(tmp_path: Path):
    results = search_builtin_docs("retrieval evidence")
    document = query_builtin_doc("retrieval-v2")
    root = bootstrap_future_project(tmp_path, name="Guided Project", slug="guided-project")

    assert results["version"] == "krail.docs/v1"
    assert results["results"][0]["path"] == "retrieval-v2"
    assert "reciprocal-rank fusion" in document["document"]["content"]
    guide = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Guide version: 1.1.12" in guide
    assert "krail docs search" in guide


def test_bootstrap_preserves_existing_agent_instructions(tmp_path: Path):
    guide = tmp_path / "AGENTS.md"
    guide.write_text("# Existing instructions\n", encoding="utf-8")

    bootstrap_future_project(tmp_path, name="Existing Project", slug="existing-project")

    assert guide.read_text(encoding="utf-8") == "# Existing instructions\n"


@pytest.mark.parametrize("run_id", ["../outside", "/tmp/outside", "nested/run"])
def test_run_inspector_rejects_path_like_ids(tmp_path: Path, run_id: str):
    root = bootstrap_future_project(tmp_path, name="Contained Runs", slug="contained-runs")
    runtime = KnowledgeRuntime(root)

    with pytest.raises(ValueError, match="simple identifier"):
        runtime.run_show(run_id)
