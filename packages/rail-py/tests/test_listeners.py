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


def _write_workflow(root: Path) -> None:
    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "refresh-notes.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "refresh_notes",
                "steps": [
                    {
                        "id": "write_marker",
                        "kind": "command",
                        "run": "python3 -c \"from pathlib import Path; Path('artifacts/listener-workflow.txt').write_text('ran')\"",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_file_listener_baselines_then_emits_changed_event(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)
    _write_workflow(root)

    source = root / "sources" / "watched.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("first", encoding="utf-8")

    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "watched-file.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "watched_file",
                "type": "file",
                "path": "sources/watched.txt",
                "on_change": {"workflow": "refresh_notes"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    first = runtime.listener_poll("watched_file", execute=False)
    assert first["results"][0]["events"] == []

    source.write_text("second", encoding="utf-8")
    second = runtime.listener_poll("watched_file", execute=False)

    events = second["results"][0]["events"]
    assert len(events) == 1
    assert events[0]["source"] == "file.changed"
    assert events[0]["payload"]["target"] == "sources/watched.txt"
    assert (root / "research_plan" / "events" / f"{events[0]['id']}.json").exists()

    listed = runtime.event_list()
    assert listed["events"][0]["id"] == events[0]["id"]


def test_event_replay_runs_workflow_dry_run(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)
    _write_workflow(root)

    source = root / "sources" / "watched.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("first", encoding="utf-8")

    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "watched-file.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "watched_file",
                "type": "file",
                "path": "sources/watched.txt",
                "emit_initial": True,
                "on_change": {"workflow": "refresh_notes", "dry_run_first": True},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    polled = runtime.listener_poll("watched_file")
    event = polled["results"][0]["events"][0]
    assert event["workflow_result"]["status"] == "dry_run"

    replay = runtime.event_replay(event["id"], dry_run=True)
    assert replay["workflow"] == "refresh_notes"
    assert replay["result"]["status"] == "dry_run"
    assert not (root / "artifacts" / "listener-workflow.txt").exists()


def test_listener_test_does_not_write_state(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)

    source = root / "sources" / "watched.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("first", encoding="utf-8")

    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "watched-file.yaml").write_text(
        yaml.safe_dump({"id": "watched_file", "type": "file", "path": "sources/watched.txt"}),
        encoding="utf-8",
    )

    result = runtime.listener_test("watched_file")

    assert result["observations"][0]["target"] == "sources/watched.txt"
    assert not (root / ".krail" / "listener_state.json").exists()


def test_command_listener_accepts_json_events(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)

    script = root / "scripts" / "emit-event.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import json\nprint(json.dumps({'source': 'custom.ready', 'target': 'demo', 'changed': True}))\n",
        encoding="utf-8",
    )

    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "custom.yaml").write_text(
        yaml.safe_dump({"id": "custom", "type": "command", "run": f"{sys.executable} scripts/emit-event.py"}),
        encoding="utf-8",
    )

    result = runtime.listener_poll("custom", execute=False)

    assert result["results"][0]["events"][0]["source"] == "custom.ready"
    assert json.loads((root / "research_plan" / "events" / f"{result['results'][0]['events'][0]['id']}.json").read_text(encoding="utf-8"))["listener_id"] == "custom"
