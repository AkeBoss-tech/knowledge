from __future__ import annotations

import sys
from pathlib import Path

import yaml

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def test_active_mode_comes_from_manifest(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Company Brain", slug="company-brain", knowledge_mode="company")
    runtime = KnowledgeRuntime(root)

    active = runtime.active_mode()
    manifest = yaml.safe_load((root / "rail.yaml").read_text(encoding="utf-8"))

    assert manifest["project"]["knowledge_mode"] == "company"
    assert active["mode"]["id"] == "company"
    assert active["mode"]["default_pack"] == "company-brain"


def test_capture_promote_updates_topic_and_marks_inbox_item(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Knowledge Project", slug="knowledge-project")
    runtime = KnowledgeRuntime(root)
    captured = runtime.capture(
        text="PDDLStream is useful for task and motion planning baselines.",
        kind="note",
        topics=["robotics"],
        entities=["PDDLStream"],
        entity_type="Package",
    )

    inbox_before = runtime.inbox_list()
    promoted = runtime.inbox_promote(captured["path"], topic="task-and-motion-planning", kind="method")
    inbox_after = runtime.inbox_list()
    topic = root / promoted["topic"]["path"]
    capture = root / captured["path"]

    assert inbox_before["unhandled"] == 1
    assert promoted["status"] == "promoted"
    assert topic.exists()
    assert "PDDLStream is useful" in topic.read_text(encoding="utf-8")
    assert "triage_status: promoted" in capture.read_text(encoding="utf-8")
    assert inbox_after["unhandled"] == 0


def test_topic_upsert_creates_mode_shaped_topic(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Software Project", slug="software-project", knowledge_mode="software")
    runtime = KnowledgeRuntime(root)

    result = runtime.topic_upsert(
        "api-gateway",
        title="API Gateway",
        kind="service",
        content="Routes public API traffic to internal services.",
        entities=["API Gateway"],
        entity_type="Service",
    )
    text = (root / result["path"]).read_text(encoding="utf-8")

    assert result["status"] == "created"
    assert "## Interfaces" in text
    assert "## Dependencies" in text
    assert "Routes public API traffic" in text
    assert "entity_type: Service" in text


def test_wiki_plan_and_build_generate_source_linked_pages(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Company Brain", slug="company-brain", knowledge_mode="company")
    runtime = KnowledgeRuntime(root)
    topic = runtime.topic_upsert(
        "revops-stack",
        title="RevOps Stack",
        kind="system",
        content="Salesforce is the CRM source of truth.",
        entities=["Salesforce"],
        entity_type="System",
    )

    plan = runtime.wiki_plan(source_paths=[topic["path"]])
    built = runtime.wiki_build(source_paths=[topic["path"]])
    listed = runtime.wiki_list()
    checked = runtime.wiki_check()
    page_path = root / built["written"][0]["target_path"]
    text = page_path.read_text(encoding="utf-8")

    assert plan["count"] == 1
    assert plan["pages"][0]["source_path"] == "topics/revops-stack.md"
    assert {item["id"] for item in plan["rich_artifacts"]} >= {"interactive_html", "svg", "web_image_reference"}
    assert built["written"][0]["target_path"] == "docs/wiki/revops-stack.md"
    assert "source_path: topics/revops-stack.md" in text
    assert "knowledge_mode: company" in text
    assert "Generated from `topics/revops-stack.md`" in text
    assert "Salesforce is the CRM source of truth." in text
    assert listed["pages"][0]["path"] == "docs/wiki/revops-stack.md"
    assert checked["ok"] is True


def test_wiki_build_skips_existing_pages_unless_forced(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Knowledge Project", slug="knowledge-project")
    runtime = KnowledgeRuntime(root)
    topic = runtime.topic_upsert("platform-notes", title="Platform Notes", content="Initial note.")

    first = runtime.wiki_build(source_paths=[topic["path"]])
    page_path = root / first["written"][0]["target_path"]
    page_path.write_text("manual edit\n", encoding="utf-8")

    skipped = runtime.wiki_build(source_paths=[topic["path"]])
    forced = runtime.wiki_build(source_paths=[topic["path"]], force=True)

    assert skipped["written"] == []
    assert skipped["skipped"][0]["reason"] == "exists"
    assert forced["written"][0]["target_path"] == "docs/wiki/platform-notes.md"
    assert "manual edit" not in page_path.read_text(encoding="utf-8")


def test_wiki_check_rejects_unresolved_artifact_tokens(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Knowledge Project", slug="knowledge-project")
    runtime = KnowledgeRuntime(root)
    topic = runtime.topic_upsert("demo-topic", title="Demo Topic", content="A short source note for a visual concept.")
    built = runtime.wiki_build(source_paths=[topic["path"]])
    page_path = root / built["written"][0]["target_path"]
    page_path.write_text(page_path.read_text(encoding="utf-8") + "\n[AI_DEMO]\n", encoding="utf-8")

    checked = runtime.wiki_check()

    assert checked["ok"] is False
    assert "unresolved artifact tokens" in checked["errors"][0]


def test_wiki_check_rejects_missing_local_image_assets(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Knowledge Project", slug="knowledge-project")
    runtime = KnowledgeRuntime(root)
    topic = runtime.topic_upsert("visual-topic", title="Visual Topic", content="A source note that needs a visual.")
    built = runtime.wiki_build(source_paths=[topic["path"]])
    page_path = root / built["written"][0]["target_path"]
    page_path.write_text(page_path.read_text(encoding="utf-8") + "\n![Process](assets/visual-topic/missing.svg)\n", encoding="utf-8")

    checked = runtime.wiki_check()

    assert checked["ok"] is False
    assert any("image target does not exist" in error for error in checked["errors"])
