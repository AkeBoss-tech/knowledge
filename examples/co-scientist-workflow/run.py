"""Replay the offline workflow contract and inspect a candidate review artifact."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent


def cli(project: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "rail.cli", "--local", "--path", str(project), *args],
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    artifact = json.loads((HERE / "expected/hypothesis-review.json").read_text())
    assert artifact["status"] == "candidate_for_human_review"
    assert artifact["supporting_evidence"] and artifact["critique"]
    assert "estimated" in artifact["ranking_rubric"]["expected_energy_reduction"]
    assert "experiment" in artifact["human_decision"]
    with tempfile.TemporaryDirectory(prefix="krail-co-scientist-") as temp:
        project = Path(temp) / "project"
        shutil.copytree(HERE, project, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        validated = cli(project, "workflow", "validate", "co_scientist_idea_tournament")
        dry_run = cli(project, "workflow", "execute", "co_scientist_idea_tournament", "--dry-run")
        assert validated["ok"] is True and validated["steps"] == 8
        assert dry_run["status"] in {"dry_run", "dry-run"}
    print("Workflow: valid; dry run:", dry_run["status"])
    print("Hypothesis:", artifact["hypothesis"])
    print("Evidence:", artifact["supporting_evidence"][0]["verification"])
    print("Human decision:", artifact["human_decision"])


if __name__ == "__main__":
    main()
