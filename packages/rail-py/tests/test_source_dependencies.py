from __future__ import annotations

import sys
from pathlib import Path

import yaml

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def test_source_dependency_check_and_affected_docs(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Source Project", slug="source-project")
    runtime = KnowledgeRuntime(root)

    first = runtime.sources_check()
    changed_first = runtime.sources_changed()

    assert first["status"] == "checked"
    assert first["changed_sources"] == []
    assert changed_first["changed_sources"] == []

    (root / "specs" / "research_question.yaml").write_text("question: Updated?\n", encoding="utf-8")
    second = runtime.sources_check()
    affected = runtime.sources_affected()

    assert second["changed_sources"] == ["local:research-question"]
    assert affected["affected_documents"] == [
        {"path": "topics/brief.md", "sources": ["local:research-question"]}
    ]


def test_source_dependency_validation_rejects_bad_manifest(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Source Project", slug="source-project")
    runtime = KnowledgeRuntime(root)
    (root / "sources" / "dependencies.yaml").write_text(
        yaml.safe_dump({"documents": [{"path": "topics/brief.md", "depends_on": [{"id": "bad"}]}]}),
        encoding="utf-8",
    )

    result = runtime.sources_validate()

    assert result["ok"] is False
    assert "must define url or path" in "\n".join(result["errors"])


def test_markdown_graph_includes_dependency_edges(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Source Project", slug="source-project")
    runtime = KnowledgeRuntime(root)

    graph = runtime.graph_build(write=False)
    dependency_edges = [edge for edge in graph["edges"] if edge["type"] == "depends_on"]

    assert dependency_edges
    assert dependency_edges[0]["from"] == "doc:topics/brief.md"
    assert dependency_edges[0]["to"] == "source:local-research-question"

