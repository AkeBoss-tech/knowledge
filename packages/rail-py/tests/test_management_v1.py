"""Contract and lifecycle conformance tests for krail.management.v1."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import yaml

from krail.management.stdio import serve
from krail.management.v1 import CONTRACT, ManagementService


def request(service: ManagementService, operation: str, request_id: str = "request-1", **fields):
    return service.handle({"contract": CONTRACT, "request_id": request_id, "operation": operation, **fields})


def inspect(service: ManagementService) -> dict:
    response = request(service, "inspect")
    assert response["ok"] is True
    return response["result"]


def plan(service: ManagementService, state: dict, **options) -> dict:
    response = request(
        service,
        "plan_init",
        expected_workspace_version=state["workspace_version"],
        expected_workspace_digest=state["workspace_digest"],
        options={"mode": "markdown_graph", "knowledge_mode": "project", **options},
    )
    assert response["ok"] is True, response
    return response["result"]["plan"]


def apply(service: ManagementService, state: dict, init_plan: dict, key: str = "init-key-0001") -> dict:
    return request(
        service,
        "apply_init",
        idempotency_key=key,
        expected_workspace_version=state["workspace_version"],
        expected_workspace_digest=state["workspace_digest"],
        plan=init_plan,
    )


def test_init_preview_is_read_only_and_apply_matches_every_previewed_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "managed-project"
    service = ManagementService(workspace)
    before = inspect(service)
    init_plan = plan(service, before, name="Managed Project")

    assert not workspace.exists()
    assert init_plan["effect_count"] == len(init_plan["effects"])
    assert init_plan["total_write_bytes"] > 0
    assert init_plan["plan_digest"].startswith("sha256:")

    response = apply(service, before, init_plan)
    assert response["ok"] is True, response
    assert response["result"]["status"] == "applied"
    assert response["result"]["applied_effects"] == init_plan["effect_count"]
    for effect in init_plan["effects"]:
        path = workspace / effect["path"]
        if effect["action"] == "create_directory":
            assert path.is_dir()
        else:
            assert path.is_file()
            assert path.stat().st_size == effect["size"]
            actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual == effect["content_digest"]


def test_apply_init_replays_receipt_and_rejects_idempotency_key_reuse(tmp_path: Path) -> None:
    service = ManagementService(tmp_path / "project")
    before = inspect(service)
    init_plan = plan(service, before)
    first = apply(service, before, init_plan)
    replay = apply(service, before, init_plan)

    assert first["result"]["replayed"] is False
    assert replay["ok"] is True
    assert replay["result"]["replayed"] is True
    assert replay["result"]["workspace_digest"] == first["result"]["workspace_digest"]
    assert inspect(service)["workspace_digest"] == first["result"]["workspace_digest"]

    changed = dict(init_plan)
    changed["plan_digest"] = "sha256:" + "0" * 64
    conflict = apply(service, before, changed)
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_plan_and_apply_reject_stale_workspace_versions(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    service = ManagementService(workspace)
    before = inspect(service)
    init_plan = plan(service, before)
    workspace.mkdir()
    (workspace / "unexpected.txt").write_text("changed", encoding="utf-8")

    stale_apply = apply(service, before, init_plan)
    stale_plan = request(
        service,
        "plan_init",
        expected_workspace_version=before["workspace_version"],
        expected_workspace_digest=before["workspace_digest"],
        options={"mode": "markdown_graph"},
    )

    assert stale_apply["error"]["code"] == "STALE_WORKSPACE"
    assert stale_plan["error"]["code"] == "STALE_WORKSPACE"
    assert not (workspace / "rail.yaml").exists()


def test_doctor_and_reindex_use_domain_operations_and_reindex_replays(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    service = ManagementService(workspace)
    before = inspect(service)
    assert apply(service, before, plan(service, before))["ok"] is True

    doctor = request(service, "doctor")
    assert doctor["ok"] is True
    assert isinstance(doctor["result"]["report"]["checks"], list)
    assert doctor["result"]["report"]["version_diagnostics"]["executable"] is None

    topic = workspace / "topics" / "managed.md"
    topic.write_text("---\ntitle: Managed\nkind: topic\n---\n\n# Managed\n", encoding="utf-8")
    current = inspect(service)
    fields = {
        "idempotency_key": "reindex-key-001",
        "expected_workspace_version": current["workspace_version"],
        "expected_workspace_digest": current["workspace_digest"],
    }
    first = request(service, "reindex", **fields)
    replay = request(service, "reindex", **fields)

    assert first["ok"] is True
    assert first["result"]["counts"]["documents"] >= 1
    assert set(first["result"]["written"]) == set(first["result"]["bounded_outputs"])
    assert replay["result"]["replayed"] is True


def test_negative_paths_are_structured_and_reindex_cannot_escape_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    service = ManagementService(workspace)
    uninitialized = request(service, "doctor")
    unsupported = request(service, "shell", command="whoami")
    malformed = service.handle({"contract": CONTRACT, "request_id": "bad", "operation": "inspect", "extra": True})

    assert uninitialized["error"]["code"] == "NOT_INITIALIZED"
    assert unsupported["error"]["code"] == "UNSUPPORTED_OPERATION"
    assert malformed["error"]["code"] == "INVALID_REQUEST"

    before = inspect(service)
    assert apply(service, before, plan(service, before))["ok"] is True
    manifest_path = workspace / "rail.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["graph"]["export"]["json"] = "../escaped.json"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    current = inspect(service)
    response = request(
        service,
        "reindex",
        idempotency_key="reindex-key-escape",
        expected_workspace_version=current["workspace_version"],
        expected_workspace_digest=current["workspace_digest"],
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "UNBOUNDED_PATH"
    assert not (tmp_path / "escaped.json").exists()


def test_json_over_stdio_emits_one_conformant_response_per_line(tmp_path: Path) -> None:
    source = io.StringIO(
        json.dumps({"contract": CONTRACT, "request_id": "one", "operation": "inspect"})
        + "\n{not json}\n"
    )
    sink = io.StringIO()

    assert serve(tmp_path / "project", source, sink) == 0
    responses = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[0]["contract"] == CONTRACT
    assert responses[0]["protocol_version"] == "1.0"
    assert responses[0]["request_id"] == "one"
    assert responses[0]["ok"] is True
    assert responses[1]["error"]["code"] == "INVALID_JSON"
