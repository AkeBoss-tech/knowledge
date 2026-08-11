from __future__ import annotations

import hashlib
from pathlib import Path

import rfc8785

from krail.management.v1 import CONTRACT as MANAGEMENT_CONTRACT, ManagementService
from krail.mutation import CONTRACT, exchange


def _initialized(root: Path) -> dict:
    service = ManagementService(root)
    state = service.handle({"contract": MANAGEMENT_CONTRACT, "request_id": "inspect", "operation": "inspect"})["result"]
    plan = service.handle({
        "contract": MANAGEMENT_CONTRACT, "request_id": "plan", "operation": "plan_init",
        "expected_workspace_version": state["workspace_version"],
        "expected_workspace_digest": state["workspace_digest"],
        "options": {"mode": "markdown_graph", "knowledge_mode": "project"},
    })["result"]["plan"]
    applied = service.handle({
        "contract": MANAGEMENT_CONTRACT, "request_id": "apply", "operation": "apply_init",
        "idempotency_key": "mutation-init-0001",
        "expected_workspace_version": state["workspace_version"],
        "expected_workspace_digest": state["workspace_digest"], "plan": plan,
    })
    assert applied["ok"] is True
    return service.handle({"contract": MANAGEMENT_CONTRACT, "request_id": "after", "operation": "inspect"})["result"]


def _request(root: Path, state: dict, *, key: str = "candidate-key-0001") -> dict:
    candidate = {
        "candidate_id": "candidate_123", "kind": "artifact", "title": "Reviewed result",
        "summary": "A reviewed but still untrusted run result.", "source_ids": ["run-1"],
    }
    candidate["candidate_digest"] = hashlib.sha256(rfc8785.dumps(candidate)).hexdigest()
    return {
        "contract_version": CONTRACT, "operation": "capture_candidate",
        "operation_id": "op-candidate", "project_root": str(root),
        "idempotency_key": key, "expected_workspace_digest": state["workspace_digest"],
        "candidate": candidate,
    }


def test_reviewed_candidate_moves_only_to_raw_inbox_and_replays(tmp_path: Path) -> None:
    root = tmp_path / "project"
    state = _initialized(root)
    request = _request(root, state)
    first = exchange(request)
    replay = exchange(request)
    assert first["status"] == "succeeded"
    assert first["result"]["trust_state"] == "raw_inbox"
    assert first["result"]["capture_path"].startswith("topics/inbox/")
    assert replay["result"]["replayed"] is True
    assert len(list((root / "topics" / "inbox").glob("*.md"))) == 1


def test_candidate_mutation_rejects_digest_and_workspace_drift(tmp_path: Path) -> None:
    root = tmp_path / "project"
    state = _initialized(root)
    invalid = _request(root, state, key="candidate-key-0002")
    invalid["candidate"]["summary"] = "Changed after review"
    assert exchange(invalid)["error"]["code"] == "invalid_request"

    stale = _request(root, state, key="candidate-key-0003")
    (root / "topics" / "drift.md").write_text("# Drift\n", encoding="utf-8")
    assert exchange(stale)["error"]["code"] == "stale_version"
