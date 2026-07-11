from __future__ import annotations

import json
import sys
import types
from pathlib import Path

if "tomllib" not in sys.modules:
    tomllib_stub = types.ModuleType("tomllib")

    def _unsupported_tomllib(*_args, **_kwargs):
        raise NotImplementedError("tomllib access is not required for these MCP tests")

    tomllib_stub.load = _unsupported_tomllib
    tomllib_stub.loads = _unsupported_tomllib
    sys.modules["tomllib"] = tomllib_stub


MCP_ROOT = Path(__file__).parents[1]
RAIL_PY_ROOT = Path(__file__).parents[2] / "rail-py"
README_PATH = MCP_ROOT / "README.md"
PYPROJECT_PATH = MCP_ROOT / "pyproject.toml"
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
            self.registered_tools = {}

        def tool(self):
            def _decorator(fn):
                self.registered_tools[fn.__name__] = fn
                return fn

            return _decorator

    fastmcp_pkg.FastMCP = _FastMCP
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = mcp_server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_pkg

from rail_mcp import server


SCOPE_ENV_VARS = (
    "KRAIL_ALLOWED_WRITE_PATHS",
    "KRAIL_DENIED_PATHS",
    "KRAIL_ALLOWED_TOOLS",
    "KRAIL_DENIED_TOOLS",
    "KRAIL_ALLOWED_SECRETS",
    "RAIL_WORK_ORDER_PATH",
    "RAIL_LOCAL",
    "RAIL_PATH",
)


def _clear_scope_env(monkeypatch):
    for name in SCOPE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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


def test_v1_contract_defines_stable_and_experimental_tool_sets():
    stable_groups = server.STABLE_V1_TOOL_GROUPS
    assert set(stable_groups) == {
        "contract",
        "doctor",
        "search",
        "think",
        "capture",
        "tasks",
        "workflows",
        "integrity",
        "permissions",
    }
    stable = set(server.STABLE_V1_TOOLS)
    experimental = set(server.EXPERIMENTAL_TOOLS)
    assert {"doctor", "search", "think", "capture", "create_task", "run_workflow", "integrity_status", "permissions_doctor"} <= stable
    assert stable.isdisjoint(experimental)


def test_mcp_contract_exposes_runtime_discoverable_v1_boundary():
    payload = json.loads(server.mcp_contract())

    assert payload["contract"] == "krail.mcp.v1"
    assert payload["contract_version"] == "v1"
    assert payload["stable"]["tool_groups"] == {
        group: list(tool_names)
        for group, tool_names in server.STABLE_V1_TOOL_GROUPS.items()
    }
    assert payload["stable"]["tools"] == list(server.STABLE_V1_TOOLS)
    assert payload["experimental"]["tools"] == list(server.EXPERIMENTAL_TOOLS)
    assert payload["experimental"]["compatibility_guaranteed"] is False
    assert "graph_build" in payload["experimental"]["tools"]
    assert "mcp_contract" in payload["stable"]["tools"]


def test_mcp_contract_classifies_every_registered_tool_without_hiding_broad_surface():
    payload = json.loads(server.mcp_contract())
    classified_tools = set(payload["stable"]["tools"]) | set(payload["experimental"]["tools"])

    assert classified_tools == set(server.mcp.registered_tools)
    assert {"search", "graph_build", "execute_python", "set_secret"} <= set(server.mcp.registered_tools)


def test_mcp_contract_rejects_unknown_version_with_actionable_json_error():
    payload = json.loads(server.mcp_contract("v2"))

    assert payload["ok"] is False
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_arguments"
    assert payload["error"]["tool"] == "mcp_contract"
    assert payload["error"]["details"] == {"argument": "contract_version"}
    assert "Use `v1`" in payload["error"]["hint"]


def test_mcp_readme_lists_stable_and_experimental_tools():
    readme = README_PATH.read_text(encoding="utf-8")

    assert "## Stable V1 Tools" in readme
    assert "## Experimental Tools" in readme
    assert "`mcp_contract`" in readme
    assert "`doctor`: `doctor`" in readme
    assert "`tasks`: `create_task`, `list_tasks`, `dispatch_task`" in readme
    assert "`permissions`: `permissions_doctor`" in readme


def test_mcp_pyproject_tracks_current_pre_v1_krail_range():
    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")

    assert '"krail>=0.2.4,<0.3.0"' in pyproject


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


def test_mcp_mount_list_calls_project(monkeypatch):
    class _Project:
        def mount_list(self):
            return {"mounts": [{"id": "child", "ok": True}], "summary": {"healthy": 1}}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.mount_list()

    payload = json.loads(result)
    assert payload["summary"]["healthy"] == 1


def test_mcp_search_can_call_federated_project_search(monkeypatch):
    class _Project:
        def federated_search(self, query, *, limit=10, mounts=None, explain=False):
            return {"query": query, "hits": [{"path": "child:topics/public.md"}], "mounts": mounts, "limit": limit}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.search("robotics", limit=5, federated=True, mounts_json='["child"]')

    payload = json.loads(result)
    assert payload["hits"][0]["path"] == "child:topics/public.md"


def test_mcp_think_can_call_federated_project_think(monkeypatch):
    class _Project:
        def federated_think(self, query, *, limit=5, mounts=None, mode="deterministic", runner="auto", dry_run=False):
            return {"query": query, "consulted_mounts": mounts, "limit": limit}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.think("robotics", limit=4, federated=True, mounts_json='["child"]')

    payload = json.loads(result)
    assert payload["consulted_mounts"] == ["child"]


def test_mcp_federated_graph_summary_calls_project(monkeypatch):
    class _Project:
        def federated_graph_summary(self, *, mounts=None):
            return {"summaries": [{"mount": "child"}], "requested": mounts}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.federated_graph_summary('["child"]')

    payload = json.loads(result)
    assert payload["summaries"][0]["mount"] == "child"


def test_mcp_think_sessions_calls_project(monkeypatch):
    class _Project:
        def think_sessions(self, *, limit=20):
            return {"sessions": [{"session_id": "think_123", "status": "prepared"}], "limit": limit}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.think_sessions(5)

    payload = json.loads(result)
    assert payload["sessions"][0]["session_id"] == "think_123"
    assert payload["limit"] == 5


def test_mcp_think_session_status_calls_project(monkeypatch):
    class _Project:
        def think_session(self, session_id):
            return {"session_id": session_id, "status": "done"}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.think_session_status("think_123")

    payload = json.loads(result)
    assert payload["session_id"] == "think_123"
    assert payload["status"] == "done"


def test_mcp_register_think_result_calls_project(monkeypatch):
    class _Project:
        def register_think_result(self, result, *, artifact_path, title=None):
            return {"status": "registered", "artifact_path": artifact_path, "title": title, "query": result["query"]}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.register_think_result(json.dumps({"query": "What changed?"}), "artifacts/think.json", "Weekly synthesis")

    payload = json.loads(result)
    assert payload["status"] == "registered"
    assert payload["artifact_path"] == "artifacts/think.json"
    assert payload["title"] == "Weekly synthesis"


def test_mcp_doctor_returns_actionable_json_when_project_is_unavailable(monkeypatch):
    monkeypatch.setattr(server, "_project", None)

    def _raise_project_error():
        raise RuntimeError("rail.yaml not found in /tmp/demo")

    monkeypatch.setattr(server, "_get_project", _raise_project_error)

    payload = json.loads(server.doctor())

    assert payload["status"] == "error"
    assert payload["error"]["code"] == "project_unavailable"
    assert payload["error"]["tool"] == "doctor"
    assert "rail.yaml not found" in payload["error"]["message"]
    assert "RAIL_PROJECT" in payload["error"]["hint"] or "RAIL_PATH" in payload["error"]["hint"]


def test_mcp_agent_prompt_calls_project(monkeypatch):
    class _Project:
        def agent_prompt(self, role, *, task=""):
            return {"role": role, "prompt": f"{role}: {task}"}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.agent_prompt("doctor", "check platform")

    payload = json.loads(result)
    assert payload["role"] == "doctor"
    assert "check platform" in payload["prompt"]


def test_mcp_create_task_calls_project(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["create_task", "write_repo"]))
    monkeypatch.setenv("KRAIL_ALLOWED_WRITE_PATHS", json.dumps(["research_plan/tasks"]))

    class _Project:
        def create_task(self, title, *, description="", runner="codex_cli", role="research"):
            return {
                "task_id": "task-123",
                "title": title,
                "description": description,
                "runner": runner,
                "role": role,
            }

    monkeypatch.setattr(server, "_project", _Project())

    result = server.create_task("Review new captures", description="Summarize inbox", runner="codex_cli", role="research")

    payload = json.loads(result)
    assert payload["task_id"] == "task-123"
    assert payload["title"] == "Review new captures"


def test_mcp_list_tasks_calls_project(monkeypatch):
    class _Project:
        def list_tasks(self):
            return {"tasks": [{"task_id": "task-123", "title": "Review new captures"}]}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.list_tasks()

    payload = json.loads(result)
    assert payload["tasks"][0]["task_id"] == "task-123"


def test_mcp_execute_workflow_defaults_to_dry_run(monkeypatch):
    class _Project:
        def execute_workflow(self, workflow_id, *, dry_run=True, force=False):
            return {"workflow": workflow_id, "dry_run": dry_run, "force": force}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.execute_workflow("weekly_review")

    payload = json.loads(result)
    assert payload == {"workflow": "weekly_review", "dry_run": True, "force": False}


def test_mcp_list_workflows_calls_project(monkeypatch):
    class _Project:
        def list_workflows(self):
            return {"workflows": ["weekly_review", "literature_refresh"]}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.list_workflows()

    payload = json.loads(result)
    assert payload["workflows"] == ["weekly_review", "literature_refresh"]


def test_mcp_run_workflow_defaults_to_dry_run(monkeypatch):
    class _Project:
        def run_workflow(self, workflow_id, *, runner="codex_cli", dry_run=True):
            return {"workflow": workflow_id, "runner": runner, "dry_run": dry_run}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.run_workflow("weekly_review")

    payload = json.loads(result)
    assert payload == {"workflow": "weekly_review", "runner": "codex_cli", "dry_run": True}


def test_mcp_init_workflow_passes_template(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["write_repo"]))
    monkeypatch.setenv("KRAIL_ALLOWED_WRITE_PATHS", json.dumps(["research_plan/workflows"]))

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


def test_mcp_listener_list_calls_project(monkeypatch):
    class _Project:
        def listener_list(self):
            return {"listeners": [{"id": "watch"}]}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.listener_list()

    payload = json.loads(result)
    assert payload["listeners"][0]["id"] == "watch"


def test_mcp_listener_poll_defaults_to_safe_preview(monkeypatch):
    class _Project:
        def listener_poll(self, listener_id=None, *, dry_run=False, execute=True):
            return {"listener_id": listener_id, "dry_run": dry_run, "execute": execute}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.listener_poll("watch")

    payload = json.loads(result)
    assert payload == {"listener_id": "watch", "dry_run": True, "execute": False}


def test_mcp_event_replay_defaults_to_dry_run(monkeypatch):
    class _Project:
        def event_replay(self, event_id, *, dry_run=False):
            return {"event_id": event_id, "dry_run": dry_run}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.event_replay("evt_123")

    payload = json.loads(result)
    assert payload == {"event_id": "evt_123", "dry_run": True}


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


def test_mcp_execute_python_denies_without_explicit_scope(monkeypatch):
    _clear_scope_env(monkeypatch)

    class _Project:
        def __init__(self):
            self.called = False

        def execute(self, code, timeout=60):
            self.called = True
            return {"stdout": code, "timeout": timeout}

    project = _Project()
    monkeypatch.setattr(server, "_project", project)

    result = server.execute_python("print('hi')")

    payload = json.loads(result)
    assert payload["status"] == "denied"
    assert payload["error"]["tool"] == "execute_python"
    assert project.called is False


def test_mcp_execute_python_allows_runner_scoped_tool(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["execute_python"]))

    class _Project:
        def execute(self, code, timeout=60):
            return {"stdout": code, "timeout": timeout}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.execute_python("print('hi')", 30)

    payload = json.loads(result)
    assert payload["stdout"] == "print('hi')"
    assert payload["timeout"] == 30


def test_mcp_execute_python_denies_runner_scoped_tool_in_deny_list(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["execute_python"]))
    monkeypatch.setenv("KRAIL_DENIED_TOOLS", json.dumps(["execute_python"]))

    class _Project:
        def __init__(self):
            self.called = False

        def execute(self, code, timeout=60):
            self.called = True
            return {"stdout": code, "timeout": timeout}

    project = _Project()
    monkeypatch.setattr(server, "_project", project)

    result = server.execute_python("print('hi')")

    payload = json.loads(result)
    assert payload["status"] == "denied"
    assert payload["error"]["reason"] == "tool_denied_by_runner_scope"
    assert project.called is False


def test_mcp_capture_denies_path_outside_runner_scope(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["write_repo"]))
    monkeypatch.setenv("KRAIL_ALLOWED_WRITE_PATHS", json.dumps(["artifacts"]))

    class _Project:
        def __init__(self):
            self.called = False

        def capture(self, *_args, **_kwargs):
            self.called = True
            return {"status": "captured"}

    project = _Project()
    monkeypatch.setattr(server, "_project", project)

    result = server.capture("raw note")

    payload = json.loads(result)
    assert payload["status"] == "denied"
    assert payload["error"]["reason"] == "path_not_allowed_by_runner_scope"
    assert project.called is False


def test_mcp_capture_allows_runner_scoped_write(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["write_repo"]))
    monkeypatch.setenv("KRAIL_ALLOWED_WRITE_PATHS", json.dumps(["topics/inbox"]))

    class _Project:
        def capture(self, text, **kwargs):
            return {"status": "captured", "text": text, "kind": kwargs["kind"]}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.capture("raw note", type="note")

    payload = json.loads(result)
    assert payload["status"] == "captured"
    assert payload["text"] == "raw note"


def test_mcp_list_secrets_filters_to_allowed_secret_names(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["manage_secrets"]))
    monkeypatch.setenv("KRAIL_ALLOWED_SECRETS", json.dumps(["OPENAI_API_KEY"]))

    class _Project:
        def list_secrets(self):
            return [
                {"keyName": "OPENAI_API_KEY"},
                {"keyName": "ANTHROPIC_API_KEY"},
            ]

    monkeypatch.setattr(server, "_project", _Project())

    result = server.list_secrets()

    payload = json.loads(result)
    assert payload == [{"keyName": "OPENAI_API_KEY"}]


def test_mcp_set_secret_denies_unscoped_secret_name(monkeypatch):
    _clear_scope_env(monkeypatch)
    monkeypatch.setenv("KRAIL_ALLOWED_TOOLS", json.dumps(["manage_secrets"]))
    monkeypatch.setenv("KRAIL_ALLOWED_SECRETS", json.dumps(["OPENAI_API_KEY"]))

    class _Project:
        def __init__(self):
            self.called = False

        def set_secret(self, key, value):
            self.called = True
            return {"key": key, "value": value}

    project = _Project()
    monkeypatch.setattr(server, "_project", project)

    result = server.set_secret("ANTHROPIC_API_KEY", "secret")

    payload = json.loads(result)
    assert payload["status"] == "denied"
    assert payload["error"]["reason"] == "secret_not_allowed_by_runner_scope"
    assert project.called is False


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


def test_mcp_integrity_claim_candidates_calls_project(monkeypatch):
    class _Project:
        def integrity_claim_candidates(self):
            return [{"candidate_key": "claim:candidate-001", "status": "candidate"}]

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_claim_candidates()

    payload = json.loads(result)
    assert payload[0]["candidate_key"] == "claim:candidate-001"


def test_mcp_find_calls_project(monkeypatch):
    class _Project:
        def find(self, query, **kwargs):
            assert query == "repo intake"
            assert kwargs["types"] == ["document", "claim"]
            assert kwargs["workflow"] == "corptech_repo_architecture_intake"
            return {"query": query, "results": [{"type": "document", "title": "Repo Intake"}]}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.find(
        "repo intake",
        types_json='["document", "claim"]',
        workflow="corptech_repo_architecture_intake",
    )

    payload = json.loads(result)
    assert payload["results"][0]["title"] == "Repo Intake"


def test_mcp_permissions_doctor_calls_project(monkeypatch):
    class _Project:
        def permissions_doctor(self):
            return {"public_by_default": True, "restricted_records": []}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.permissions_doctor()

    payload = json.loads(result)
    assert payload["public_by_default"] is True


def test_mcp_integrity_artifacts_calls_project(monkeypatch):
    class _Project:
        def integrity_artifact_lineage(self):
            return [{"artifact_path": "artifacts/think.json", "promotion_state": "draft"}]

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_artifacts()

    payload = json.loads(result)
    assert payload[0]["artifact_path"] == "artifacts/think.json"


def test_mcp_integrity_promote_claim_candidate_calls_project(monkeypatch):
    class _Project:
        def apply_integrity_claim_candidate_promotion(self, candidate_key, *, status):
            return {"status": "promoted", "candidate_key": candidate_key, "claim_status": status}

    monkeypatch.setattr(server, "_project", _Project())

    result = server.integrity_promote_claim_candidate("claim:candidate-001", status="needs_evidence")

    payload = json.loads(result)
    assert payload["status"] == "promoted"
    assert payload["candidate_key"] == "claim:candidate-001"
    assert payload["claim_status"] == "needs_evidence"


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


def test_mcp_integrity_reproducibility_rerun_invalid_json_returns_actionable_error():
    payload = json.loads(server.integrity_reproducibility_rerun("not-json"))

    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_arguments"
    assert payload["error"]["tool"] == "integrity_reproducibility_rerun"
    assert payload["error"]["details"]["argument"] == "outputs_json"


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
