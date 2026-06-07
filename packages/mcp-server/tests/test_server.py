from __future__ import annotations

import json
import sys
import types
from pathlib import Path


MCP_ROOT = Path(__file__).parents[1]
RAIL_PY_ROOT = Path(__file__).parents[2] / "rail-py"
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

if "mcp.server.fastmcp" not in sys.modules:
    mcp_pkg = types.ModuleType("mcp")
    mcp_server_pkg = types.ModuleType("mcp.server")
    fastmcp_pkg = types.ModuleType("mcp.server.fastmcp")

    class _FastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self):
            def _decorator(fn):
                return fn

            return _decorator

    fastmcp_pkg.FastMCP = _FastMCP
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = mcp_server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_pkg

from rail_mcp import server


def test_mcp_integrity_status_calls_project(monkeypatch):
    class _Project:
        def integrity_status(self):
            return {
                "summary": {"sourceCount": 1},
                "agentWorkflow": {"health": {"status": "ready"}},
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_status()

    payload = json.loads(result)
    assert payload["summary"]["sourceCount"] == 1
    assert payload["agentWorkflow"]["health"]["status"] == "ready"


def test_mcp_graph_entities_calls_project(monkeypatch):
    class _Project:
        def graph_entities(self, *, entity_type=None, limit=100):
            return {
                "entities": [{"label": "PDDLStream", "entityType": entity_type}],
                "count": 1,
                "limit": limit,
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.graph_entities("Package", 5)

    payload = json.loads(result)
    assert payload["entities"][0]["label"] == "PDDLStream"
    assert payload["entities"][0]["entityType"] == "Package"
    assert payload["limit"] == 5


def test_mcp_vector_search_calls_project(monkeypatch):
    class _Project:
        def vector_search(self, query, *, limit=10):
            return {"query": query, "hits": [{"path": "topics/brief.md"}], "limit": limit}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.vector_search("robotics planning", 3)

    payload = json.loads(result)
    assert payload["query"] == "robotics planning"
    assert payload["hits"][0]["path"] == "topics/brief.md"
    assert payload["limit"] == 3


def test_mcp_agent_prompt_calls_project(monkeypatch):
    class _Project:
        def agent_prompt(self, role, *, task=""):
            return {"role": role, "prompt": f"{role}: {task}"}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.agent_prompt("doctor", "check platform")

    payload = json.loads(result)
    assert payload["role"] == "doctor"
    assert "check platform" in payload["prompt"]


def test_mcp_execute_workflow_defaults_to_dry_run(monkeypatch):
    class _Project:
        def execute_workflow(self, workflow_id, *, dry_run=True, force=False):
            return {"workflow": workflow_id, "dry_run": dry_run, "force": force}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.execute_workflow("weekly_review")

    payload = json.loads(result)
    assert payload == {"workflow": "weekly_review", "dry_run": True, "force": False}


def test_mcp_init_workflow_passes_template(monkeypatch):
    class _Project:
        def init_workflow(self, workflow_id, *, force=False, template=None):
            return {"workflow": workflow_id, "force": force, "template": template}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.init_workflow("weekly", template="weekly_research_review")

    payload = json.loads(result)
    assert payload == {"workflow": "weekly", "force": False, "template": "weekly_research_review"}


def test_mcp_workflow_status_calls_project(monkeypatch):
    class _Project:
        def workflow_status(self, run_id):
            return {"run_id": run_id, "status": "done"}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.workflow_status("workflow_weekly_20260607")

    payload = json.loads(result)
    assert payload["status"] == "done"


def test_mcp_sources_affected_passes_source_ids(monkeypatch):
    class _Project:
        def sources_affected(self, *, source_ids=None):
            return {"source_ids": source_ids, "affected_documents": []}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.sources_affected(json.dumps(["github:demo/repo"]))

    payload = json.loads(result)
    assert payload["source_ids"] == ["github:demo/repo"]


def test_mcp_sources_check_calls_project(monkeypatch):
    class _Project:
        def sources_check(self, *, write=True):
            return {"write": write, "changed_sources": []}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.sources_check(False)

    payload = json.loads(result)
    assert payload == {"write": False, "changed_sources": []}


def test_mcp_integrity_assumptions_calls_project(monkeypatch):
    class _Project:
        def integrity_assumptions(self):
            return [{"assumption_key": "study-period"}]

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_assumptions()

    payload = json.loads(result)
    assert payload[0]["assumption_key"] == "study-period"


def test_mcp_integrity_sources_calls_project(monkeypatch):
    class _Project:
        def integrity_sources(self):
            return [{"source_key": "briefing-note", "sourceState": {"isFresh": True}}]

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_sources()

    payload = json.loads(result)
    assert payload[0]["source_key"] == "briefing-note"
    assert payload[0]["sourceState"]["isFresh"] is True


def test_mcp_integrity_claims_calls_project(monkeypatch):
    class _Project:
        def integrity_claims(self):
            return [{"claim_key": "claim-001", "claimState": {"evidenceComplete": True}}]

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_claims()

    payload = json.loads(result)
    assert payload[0]["claim_key"] == "claim-001"
    assert payload[0]["claimState"]["evidenceComplete"] is True


def test_mcp_integrity_reproducibility_rerun_calls_project(monkeypatch):
    class _Project:
        def apply_integrity_reproducibility_rerun(self, outputs, *, run_id="rerun-verification", scope="health"):
            return {
                "status": "passed",
                "outputs": outputs,
                "run_id": run_id,
                "scope": scope,
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_reproducibility_rerun(
        json.dumps({"artifacts/report.md": "stable report\n"}),
        run_id="rerun-001",
        scope="health",
    )

    payload = json.loads(result)
    assert payload["status"] == "passed"
    assert payload["outputs"]["artifacts/report.md"] == "stable report\n"
    assert payload["run_id"] == "rerun-001"


def test_mcp_integrity_freshness_evaluate_calls_project(monkeypatch):
    class _Project:
        def apply_integrity_freshness_evaluation(self, *, as_of=None):
            return {
                "status": "evaluated",
                "as_of": as_of,
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_freshness_evaluate("2026-05-14T00:00:00Z")

    payload = json.loads(result)
    assert payload["status"] == "evaluated"
    assert payload["as_of"] == "2026-05-14T00:00:00Z"


def test_mcp_integrity_source_detail_calls_project(monkeypatch):
    class _Project:
        def integrity_source_detail(self, source_key):
            return {
                "source": {"source_key": source_key},
                "sourceState": {"isFresh": True},
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_source_detail("briefing-note")

    payload = json.loads(result)
    assert payload["source"]["source_key"] == "briefing-note"
    assert payload["sourceState"]["isFresh"] is True


def test_mcp_integrity_claim_detail_calls_project(monkeypatch):
    class _Project:
        def integrity_claim_detail(self, claim_key):
            return {
                "claim": {"claim_key": claim_key},
                "claimState": {"evidenceComplete": True},
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_claim_detail("claim-001")

    payload = json.loads(result)
    assert payload["claim"]["claim_key"] == "claim-001"
    assert payload["claimState"]["evidenceComplete"] is True


def test_mcp_integrity_verification_runs_calls_project(monkeypatch):
    class _Project:
        def integrity_verification_runs(self):
            return {
                "summary": {"loopTypeCounts": {"analysis_reproducibility": 1}},
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_verification_runs()

    payload = json.loads(result)
    assert payload["summary"]["loopTypeCounts"]["analysis_reproducibility"] == 1


def test_mcp_integrity_benchmark_calls_project(monkeypatch):
    class _Project:
        def integrity_benchmark(self, *, retrieval_limit=10):
            return {
                "summary": {
                    "caseCount": 7,
                    "passedCases": 7,
                    "hybridOutperformsVectorOnly": True,
                }
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_benchmark(5)

    payload = json.loads(result)
    assert payload["summary"]["caseCount"] == 7
    assert payload["summary"]["passedCases"] == 7
    assert payload["summary"]["hybridOutperformsVectorOnly"] is True


def test_mcp_integrity_stale_graph_calls_project(monkeypatch):
    class _Project:
        def integrity_stale_graph(self):
            return {
                "summary": {"staleArtifactCount": 1},
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_stale_graph()

    payload = json.loads(result)
    assert payload["summary"]["staleArtifactCount"] == 1


def test_mcp_integrity_promote_artifact_calls_project(monkeypatch):
    class _Project:
        def apply_integrity_artifact_promotion(self, artifact_path, *, target_state):
            return {
                "status": "promoted",
                "artifact_path": artifact_path,
                "target_state": target_state,
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_promote_artifact("artifacts/report.md", "verified")

    payload = json.loads(result)
    assert payload["status"] == "promoted"
    assert payload["artifact_path"] == "artifacts/report.md"
    assert payload["target_state"] == "verified"


def test_mcp_integrity_artifact_detail_calls_project(monkeypatch):
    class _Project:
        def integrity_artifact_detail(self, artifact_path):
            return {
                "artifact": {"artifact_path": artifact_path},
                "trustState": {"currentState": "verified"},
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_artifact_detail("artifacts/report.md")

    payload = json.loads(result)
    assert payload["artifact"]["artifact_path"] == "artifacts/report.md"
    assert payload["trustState"]["currentState"] == "verified"


def test_mcp_integrity_graph_calls_project(monkeypatch):
    class _Project:
        def integrity_dependency_graph(self):
            return {
                "edges": [{"from": "source:briefing-note", "to": "claim:claim-001", "relationship": "supports"}],
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_graph()

    payload = json.loads(result)
    assert payload["edges"][0]["relationship"] == "supports"


def test_mcp_integrity_retrieve_passes_date_filters(monkeypatch):
    class _Project:
        def integrity_retrieve(self, query, **kwargs):
            return {
                "query": query,
                "filters": kwargs,
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_retrieve(
        "labor source",
        date_from="2026-01-01T00:00:00Z",
        date_to="2026-12-31T23:59:59Z",
    )

    payload = json.loads(result)
    assert payload["filters"]["date_from"] == "2026-01-01T00:00:00Z"
    assert payload["filters"]["date_to"] == "2026-12-31T23:59:59Z"


def test_mcp_integrity_rerun_plan_calls_project(monkeypatch):
    class _Project:
        def integrity_rerun_plan(self, assumption_key):
            return {
                "assumption": {"assumption_key": assumption_key},
                "affectedPaths": [".ontology/onto.duckdb", "artifacts/report.md"],
            }

        def apply_integrity_rerun_plan(self, assumption_key):
            return {
                "rerunPlan": {
                    "assumption": {"assumption_key": assumption_key},
                    "affectedPaths": [".ontology/onto.duckdb", "artifacts/report.md"],
                }
            }

    monkeypatch.setattr(server, "_project", _Project())

    preview = json.loads(server.integrity_rerun_plan("study-period", apply=False))
    applied = json.loads(server.integrity_rerun_plan("study-period", apply=True))

    assert preview["assumption"]["assumption_key"] == "study-period"
    assert preview["affectedPaths"] == [".ontology/onto.duckdb", "artifacts/report.md"]
    assert applied["rerunPlan"]["affectedPaths"] == [".ontology/onto.duckdb", "artifacts/report.md"]
