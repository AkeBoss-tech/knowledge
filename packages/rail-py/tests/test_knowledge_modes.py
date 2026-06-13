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

