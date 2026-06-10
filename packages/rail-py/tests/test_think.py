from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime
import yaml


def test_think_returns_citations_and_source_freshness(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    runtime = KnowledgeRuntime(root)

    result = runtime.think("project objective", limit=3)

    assert result["mode"] == "deterministic"
    assert result["citations"]
    assert result["source_freshness"]["dependency_manifest_ok"] is True
    assert "source_refresh" in "\n".join(result["suggested_next_actions"])
    assert result["confidence"] in {"low", "medium"}
    assert "verification" in result


def test_think_runner_dry_run_materializes_session(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    runtime = KnowledgeRuntime(root)

    result = runtime.think("project objective", limit=3, mode="runner", runner="codex_cli", dry_run=True)

    assert result["mode"] == "runner"
    assert result["runner"] == "codex_cli"
    assert result["status"] == "dry_run"
    assert result["session"]["session_path"]
    session_dir = root / result["session"]["session_path"]
    assert (session_dir / "think_request.json").exists()
    assert (session_dir / "work_order.json").exists()
    assert (session_dir / "command.json").exists()
    assert (session_dir / "session_result.template.json").exists()

    sessions = runtime.list_think_sessions()
    status = runtime.get_think_session(result["session"]["session_id"])

    assert sessions["sessions"][0]["session_id"] == result["session"]["session_id"]
    assert status["status"] == "prepared"
    assert status["request"]["mode"] == "runner"


def test_think_runner_uses_manifest_think_preference(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    manifest_path = root / "rail.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("agents", {}).setdefault("runner_policy", {})["think_preferred"] = ["claude_code"]
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("CODEX_CLI_COMMAND", "/missing/codex")
    monkeypatch.setenv("CLAUDE_CODE_COMMAND", sys.executable)
    runtime = KnowledgeRuntime(root)

    result = runtime.think("project objective", limit=3, mode="runner", runner="auto", dry_run=True)

    assert result["runner"] == "claude_code"


def test_register_think_result_creates_integrity_artifact_and_candidates(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    runtime = KnowledgeRuntime(root)
    artifact_path = root / "artifacts" / "think-output.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "query": "What changed?",
        "mode": "deterministic",
        "answer": "Finding: Evidence suggests the project objective is documented in the brief and README.",
        "evidence": [{"path": "README.md", "title": "Readme", "snippet": "project objective", "score": 1.0}],
        "citations": [{"ref": "[1]", "path": "README.md", "title": "Readme", "score": 1.0}],
        "source_freshness": {"dependency_manifest_ok": True, "changed_sources": [], "affected_documents": [], "stale_evidence": []},
        "verification": {"ok": True, "checks": [{"name": "citation_coverage", "ok": True}]},
    }
    artifact_path.write_text("{}", encoding="utf-8")

    registered = runtime.register_think_result(result, artifact_path=str(artifact_path), title="Weekly synthesis")
    repo = runtime._integrity_repo()

    assert registered["status"] == "registered"
    assert registered["artifact"]["artifact_path"] == "artifacts/think-output.json"
    assert registered["verification_run"]["status"] == "passed"
    assert registered["claim_candidates"]
    assert repo.load_claim_candidates()
    assert any(item.artifact_path == "artifacts/think-output.json" for item in repo.load_artifact_lineage())


def test_search_ignores_unpromoted_think_artifacts_and_operational_state(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    runtime = KnowledgeRuntime(root)
    artifact_path = root / "artifacts" / "think-output.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text('{"answer":"Evidence suggests generated output"}', encoding="utf-8")
    runtime.register_think_result(
        {
            "query": "generated output",
            "mode": "deterministic",
            "answer": "Evidence suggests generated output",
            "evidence": [],
            "citations": [],
            "source_freshness": {"dependency_manifest_ok": True, "changed_sources": [], "affected_documents": [], "stale_evidence": []},
            "verification": {"ok": True, "checks": []},
        },
        artifact_path=str(artifact_path),
        title="Generated output",
    )

    results = runtime.search("generated output", limit=10)
    paths = [item["path"] for item in results["hits"]]

    assert "artifacts/think-output.json" not in paths
    assert not any(path.startswith("research_plan/state/") for path in paths)


def test_promote_registered_think_claim_candidate(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    runtime = KnowledgeRuntime(root)
    artifact_path = root / "artifacts" / "think-output.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("{}", encoding="utf-8")
    registered = runtime.register_think_result(
        {
            "query": "What changed?",
            "mode": "deterministic",
            "answer": "Evidence suggests the README states the project objective explicitly.",
            "evidence": [{"path": "README.md", "title": "Readme", "snippet": "project objective", "score": 1.0}],
            "citations": [{"ref": "[1]", "path": "README.md", "title": "Readme", "score": 1.0}],
            "source_freshness": {"dependency_manifest_ok": True, "changed_sources": [], "affected_documents": [], "stale_evidence": []},
            "verification": {"ok": True, "checks": [{"name": "citation_coverage", "ok": True}]},
        },
        artifact_path=str(artifact_path),
        title="Weekly synthesis",
    )
    candidate_key = registered["claim_candidates"][0]["candidate_key"]

    promoted = runtime._integrity_repo().promote_claim_candidate(candidate_key, status="needs_evidence")

    assert promoted["status"] == "promoted"
    assert promoted["claim"]["status"] == "needs_evidence"


def test_think_runner_executes_local_runner_and_records_result(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    runtime = KnowledgeRuntime(root)
    runner_script = root / "scripts" / "fake_cursor_runner.py"
    runner_script.parent.mkdir(parents=True, exist_ok=True)
    runner_script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, re, sys",
                "from pathlib import Path",
                "prompt = sys.argv[-1]",
                "match = re.search(r\"`([^`]*session_result\\.json)`\", prompt)",
                "assert match, prompt",
                "target = Path(match.group(1))",
                "target.parent.mkdir(parents=True, exist_ok=True)",
                "payload = {",
                "  'answer': 'Evidence suggests the objective is documented.',",
                "  'citations_used': ['[1]'],",
                "  'gaps': [],",
                "  'conflicts': [],",
                "  'suggested_next_actions': ['Promote candidate claims after review.'],",
                "  'unsupported_claims': [],",
                "}",
                "target.write_text(json.dumps(payload), encoding='utf-8')",
                "print(json.dumps(payload))",
            ]
        ),
        encoding="utf-8",
    )
    runner_script.chmod(0o755)
    monkeypatch.setenv("CURSOR_CLI_COMMAND", str(runner_script))

    result = runtime.think("project objective", limit=2, mode="runner", runner="cursor_cli", dry_run=False)
    session = runtime.get_think_session(result["session"]["session_id"])

    assert result["status"] == "done"
    assert result["runner"] == "cursor_cli"
    assert result["verification"]["ok"] is True
    assert result["answer"] == "Evidence suggests the objective is documented."
    assert session["status"] == "done"
    assert session["result"]["citations_used"] == ["[1]"]
