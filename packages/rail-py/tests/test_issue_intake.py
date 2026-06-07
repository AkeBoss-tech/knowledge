from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

RAIL_PY_ROOT = Path(__file__).parents[1]
REPO_ROOT = Path(__file__).parents[3]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project


def _load_intake():
    path = REPO_ROOT / "scripts" / "krail_issue_intake.py"
    spec = importlib.util.spec_from_file_location("krail_issue_intake", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_commands_from_issue_body():
    intake = _load_intake()

    commands = intake.extract_commands(
        """
        Please run this:
        /krail doctor
        /krail sources affected --source-id local:demo
        """
    )

    assert commands == ["doctor", "sources affected --source-id local:demo"]


def test_issue_intake_workflow_is_dry_run(tmp_path: Path):
    intake = _load_intake()
    root = bootstrap_future_project(tmp_path, name="Issue Project", slug="issue-project")
    project = intake.rail.local(str(root))
    project.init_workflow("source_refresh", template="source_refresh")

    result = intake.run_command(project, "workflow source_refresh")

    assert result["status"] == "dry_run"
    assert result["workflow"] == "source_refresh"


def test_issue_intake_task_create_writes_task_and_work_order(tmp_path: Path):
    intake = _load_intake()
    root = bootstrap_future_project(tmp_path, name="Issue Project", slug="issue-project")
    project = intake.rail.local(str(root))

    result = intake.run_command(
        project,
        'task create --title "Audit sources" --description "Check stale docs" --runner codex_cli --role research --workflow source_refresh',
    )

    task_path = root / result["created"]["path"]
    work_order_path = root / result["work_order"]["path"]
    assert task_path.exists()
    assert work_order_path.exists()
    assert json.loads(task_path.read_text(encoding="utf-8"))["workflow"] == "source_refresh"

