from __future__ import annotations

import json
import subprocess
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


def test_listener_init_validate_and_templates(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)

    templates = runtime.listener_templates()
    created = runtime.listener_init("website_change_monitor", listener_id="docs_watch")
    validation = runtime.listener_validate("docs_watch")

    assert "website_change_monitor" in templates["templates"]
    assert "git_change_monitor" in templates["templates"]
    assert "github" in templates["types"]
    assert created["path"] == "research_plan/listeners/docs-watch.yaml"
    assert validation["ok"] is True


def test_listener_validation_rejects_missing_required_fields(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)
    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "bad.yaml").write_text(yaml.safe_dump({"id": "bad", "type": "http"}), encoding="utf-8")

    validation = runtime.listener_validate("bad")

    assert validation["ok"] is False
    assert "http listener requires url" in validation["errors"]


def test_event_context_is_available_to_workflow_steps(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)

    workflow_dir = root / "research_plan" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "capture-event.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "capture_event",
                "steps": [
                    {
                        "id": "write_target",
                        "kind": "command",
                        "run": "python3 -c \"from pathlib import Path; Path('artifacts/event-target.txt').write_text('${{ inputs.event.payload.target }}')\"",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    source = root / "sources" / "watched.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("first", encoding="utf-8")
    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "watched-file.yaml").write_text(
        yaml.safe_dump({"id": "watched_file", "type": "file", "path": "sources/watched.txt", "emit_initial": True, "on_change": {"workflow": "capture_event"}}),
        encoding="utf-8",
    )

    result = runtime.listener_poll("watched_file")

    assert result["results"][0]["events"][0]["status"] == "done"
    assert (root / "artifacts" / "event-target.txt").read_text(encoding="utf-8") == "sources/watched.txt"


def test_listener_records_error_state_and_backoff(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)
    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "bad-command.yaml").write_text(
        yaml.safe_dump({"id": "bad_command", "type": "command", "run": f"{sys.executable} -c \"print('not json')\""}),
        encoding="utf-8",
    )

    first = runtime.listener_poll("bad_command")
    second = runtime.listener_poll("bad_command")

    assert first["results"][0]["status"] == "error"
    assert first["results"][0]["state"]["failure_count"] == 1
    assert second["results"][0]["status"] == "backoff"


def test_github_listener_polls_issues(monkeypatch, tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)
    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "github.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "github",
                "type": "github",
                "repo": "owner/repo",
                "events": ["issues.opened"],
                "emit_initial": True,
            }
        ),
        encoding="utf-8",
    )

    def fake_run(args, **_kwargs):
        assert args[:2] == ["gh", "api"]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps([{"id": 1, "number": 7, "html_url": "https://github.test/issue/7"}]), stderr="")

    monkeypatch.setattr("rail.listeners.subprocess.run", fake_run)

    result = runtime.listener_poll("github", execute=False)

    event = result["results"][0]["events"][0]
    assert event["source"] == "github.issue.opened"
    assert event["payload"]["payload"]["number"] == 7


def test_git_listener_detects_working_tree_change(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)
    _write_workflow(root)

    repo = root / "tracked-repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)

    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "git-watch.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "git_watch",
                "type": "git",
                "repo_path": "tracked-repo",
                "on_change": {"workflow": "refresh_notes", "dry_run_first": True},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    first = runtime.listener_poll("git_watch")
    assert first["results"][0]["events"] == []

    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    second = runtime.listener_poll("git_watch")

    event = second["results"][0]["events"][0]
    assert event["source"] == "git.repo.changed"
    assert event["workflow_result"]["status"] == "dry_run"
    assert event["payload"]["payload"]["working_tree"]["dirty"] is True


def test_listener_doctor_reports_missing_workflow(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Listener Project", slug="listener-project")
    runtime = KnowledgeRuntime(root)
    listener_dir = root / "research_plan" / "listeners"
    listener_dir.mkdir(parents=True, exist_ok=True)
    (listener_dir / "missing-workflow.yaml").write_text(
        yaml.safe_dump({"id": "missing_workflow", "type": "schedule", "on_change": {"workflow": "does_not_exist"}}),
        encoding="utf-8",
    )

    result = runtime.listener_doctor()

    assert result["ok"] is False
    assert any(item["issue"] == "missing_workflow" for item in result["errors"])


def test_emit_event_helper_returns_command_listener_payload():
    from rail.listeners import emit_event

    event = emit_event(source="linear.issue.created", target="LIN-123", payload={"title": "Bug"})

    assert event == {
        "source": "linear.issue.created",
        "target": "LIN-123",
        "changed": True,
        "payload": {"title": "Bug"},
    }
