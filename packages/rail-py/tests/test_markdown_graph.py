from __future__ import annotations

import json
import time
from pathlib import Path

import rail
from rail.markdown_graph import (
    build_markdown_graph,
    check_markdown_graph,
    export_graph,
    filter_documents,
    filter_edges,
    filter_entities,
    validate_markdown_graph,
)


def _write_project(root: Path) -> None:
    (root / "topics" / "notes").mkdir(parents=True)
    (root / "research_plan").mkdir()
    (root / ".krail").mkdir()
    (root / ".ontology" / "sources").mkdir(parents=True)
    (root / ".ontology" / "pipelines").mkdir()
    (root / "specs").mkdir()
    (root / "agents").mkdir()
    (root / "skills").mkdir()
    (root / "artifacts").mkdir()
    (root / ".ontology" / "ontology.yaml").write_text("uri: http://example.test/onto.owl\nclasses: []\n", encoding="utf-8")
    (root / "rail.yaml").write_text(
        """\
version: 1
project:
  name: Graph Test
  slug: graph-test
  default_branch: main
paths:
  ontology_root: .ontology
  topics_root: topics
  specs_root: specs
  plan_root: research_plan
  agents_root: agents
  skills_root: skills
  artifacts_root: artifacts
hydration:
  ontology_file: .ontology/ontology.yaml
  sources_dir: .ontology/sources
  pipelines_dir: .ontology/pipelines
agents:
  roles_dir: agents
  default_runner: codex_cli
  sequential_execution: true
graph:
  mode: markdown_frontmatter
  include:
    - topics/**/*.md
    - research_plan/**/*.md
  export:
    json: research_plan/graph/graph.json
    mermaid: research_plan/graph/graph.mmd
    summary: research_plan/graph/summary.md
""",
        encoding="utf-8",
    )
    (root / ".krail" / "pack.yaml").write_text(
        "id: research-intelligence\nentities: [Paper]\nlink_types: [Paper INTRODUCES Method]\nworkflows: [weekly]\n",
        encoding="utf-8",
    )
    (root / "topics" / "brief.md").write_text(
        """\
---
title: Robotics Brief
kind: brief
topics:
  - dual-arm-planning
entities:
  - PDDLStream
entity_metadata:
  - name: PDDLStream
    entity_type: Package
sources:
  - https://example.com/pddlstream
relations:
  - from: PDDLStream
    type: baseline_for
    to: dual-arm TAMP experiments
---

# Robotics Brief
""",
        encoding="utf-8",
    )
    (root / "topics" / "notes" / "method.md").write_text(
        """\
---
title: Method Note
kind: synthesis-note
topics:
  - methods
entity_metadata:
  - name: SDAR
    entity_type: Method
relations:
  - from: SDAR
    type: evaluates_on
    to: entangled tabletop rearrangement
---

# Method Note
""",
        encoding="utf-8",
    )


def test_build_markdown_graph_writes_json_mermaid_and_summary(tmp_path: Path):
    _write_project(tmp_path)

    graph = build_markdown_graph(tmp_path, write=True)

    assert graph["mode"] == "markdown_frontmatter"
    assert graph["counts"]["documents"] == 2
    assert graph["entityTypeCounts"]["Package"] == 1
    assert graph["entityTypeCounts"]["Method"] == 1
    assert "research_plan/graph/graph.json" in graph["written"]
    assert (tmp_path / "research_plan" / "graph" / "graph.json").exists()
    assert (tmp_path / "research_plan" / "graph" / "graph.mmd").read_text(encoding="utf-8").startswith("graph TD")
    assert "Markdown Graph Summary" in (tmp_path / "research_plan" / "graph" / "summary.md").read_text(encoding="utf-8")

    stored = json.loads((tmp_path / "research_plan" / "graph" / "graph.json").read_text(encoding="utf-8"))
    assert stored["counts"]["nodes"] >= 6


def test_markdown_graph_filters_entities_edges_and_docs(tmp_path: Path):
    _write_project(tmp_path)
    graph = build_markdown_graph(tmp_path, write=False)

    packages = filter_entities(graph, entity_type="Package")
    assert packages["count"] == 1
    assert packages["entities"][0]["label"] == "PDDLStream"

    edges = filter_edges(graph, entity="PDDLStream")
    assert any(edge["type"] == "baseline_for" for edge in edges["edges"])

    docs = filter_documents(graph, topic="dual-arm-planning")
    assert docs["count"] == 1
    assert docs["documents"][0]["path"] == "topics/brief.md"

    assert "baseline_for" in export_graph(graph, "mermaid")


def test_project_hydrate_markdown_graph_mode(tmp_path: Path):
    _write_project(tmp_path)
    project = rail.local(path=tmp_path)

    result = project.hydrate(mode="markdown_graph")

    assert result["status"] == "hydrated"
    assert result["mode"] == "markdown_graph"
    assert result["graph"]["counts"]["documents"] == 2


def test_graph_validate_and_check_report_freshness(tmp_path: Path):
    _write_project(tmp_path)

    validation = validate_markdown_graph(tmp_path)
    assert validation["ok"] is True
    missing = check_markdown_graph(tmp_path)
    assert missing["ok"] is False
    assert missing["status"] == "missing"

    build_markdown_graph(tmp_path, write=True)
    fresh = check_markdown_graph(tmp_path)
    assert fresh["ok"] is True
    assert fresh["status"] == "fresh"


def test_capture_can_write_graph_frontmatter_and_search_rag(tmp_path: Path):
    _write_project(tmp_path)
    project = rail.local(path=tmp_path)

    captured = project.capture(
        "PDDLStream remains a useful baseline for robotics planning.",
        title="PDDLStream capture",
        topics=["robotics"],
        entities=["PDDLStream"],
        entity_type="Package",
    )
    assert captured["status"] == "captured"

    graph = project.graph_build(write=False)
    assert any(node.get("label") == "PDDLStream" for node in graph["nodes"])

    indexed = project.vector_build()
    assert indexed["chunks"] >= 1

    vector_hits = project.vector_search("robotics planning baseline", limit=3)
    assert vector_hits["hits"]

    rag = project._backend.knowledge.search("robotics planning baseline", limit=3, rag=True, explain=True)
    assert rag["vector_hits"]
    assert rag["rag"]["database"] == ".krail/vector.sqlite"

    capture_path = tmp_path / captured["path"]
    captured_text = capture_path.read_text(encoding="utf-8")
    assert 'captured_at: "' in captured_text


def test_graph_context_boosts_connected_documents(tmp_path: Path):
    _write_project(tmp_path)
    project = rail.local(path=tmp_path)
    project.graph_build(write=False)

    result = project._backend.knowledge.search("PDDLStream", limit=5, explain=True)

    brief_hit = next(hit for hit in result["hits"] if hit["path"] == "topics/brief.md")
    assert brief_hit["graph_score"] > 0
    assert result["graph_context"]["entities"][0]["label"] == "PDDLStream"


def test_vector_build_records_provider_metadata(tmp_path: Path):
    _write_project(tmp_path)
    project = rail.local(path=tmp_path)

    indexed = project.vector_build(provider="local_hash", model="local-test")
    assert indexed["embedding"]["provider"] == "local_hash"
    assert indexed["embedding"]["model"] == "local-test"

    hits = project.vector_search("PDDLStream baseline", limit=2)
    assert hits["embedding"]["provider"] == "local_hash"
    assert hits["embedding"]["model"] == "local-test"


def test_markdown_graph_build_is_deterministic_across_identical_rebuilds(tmp_path: Path):
    _write_project(tmp_path)

    first = build_markdown_graph(tmp_path, write=True)
    json_1 = (tmp_path / "research_plan" / "graph" / "graph.json").read_text(encoding="utf-8")
    summary_1 = (tmp_path / "research_plan" / "graph" / "summary.md").read_text(encoding="utf-8")

    time.sleep(0.01)

    second = build_markdown_graph(tmp_path, write=True)
    json_2 = (tmp_path / "research_plan" / "graph" / "graph.json").read_text(encoding="utf-8")
    summary_2 = (tmp_path / "research_plan" / "graph" / "summary.md").read_text(encoding="utf-8")

    assert first["generatedAt"] == second["generatedAt"]
    assert json_1 == json_2
    assert summary_1 == summary_2
