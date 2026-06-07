from __future__ import annotations

import json
from pathlib import Path

import rail
from rail.markdown_graph import build_markdown_graph, export_graph, filter_documents, filter_edges, filter_entities


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
