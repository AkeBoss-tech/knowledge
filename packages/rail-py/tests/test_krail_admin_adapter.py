from __future__ import annotations

from pathlib import Path

import rfc8785

from krail.admin import CONTRACT, exchange


def request(root: Path, operation: str, key: str, parameters: dict | None = None) -> dict:
    return {
        "contract_version": CONTRACT,
        "operation": operation,
        "operation_id": f"op-{key}",
        "project_root": str(root),
        "idempotency_key": key,
        "parameters": parameters or {},
    }


def test_admin_plan_apply_doctor_and_reindex(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    planned = exchange(request(root, "init_plan", "plan-key"))
    assert planned["status"] == "planned"
    plan = planned["result"]["plan"]

    applied = exchange(request(root, "init", "apply-key", {"plan": plan}))
    assert applied["status"] == "succeeded"
    assert (root / "rail.yaml").is_file()

    replayed = exchange(request(root, "init", "apply-key", {"plan": plan}))
    assert replayed["result"]["replayed"] is True
    assert exchange(request(root, "doctor", "doctor-key"))["status"] == "succeeded"
    assert exchange(request(root, "reindex", "index-key"))["status"] == "succeeded"


def test_admin_receipt_digest_uses_canonical_request(tmp_path: Path) -> None:
    payload = request(tmp_path / "workspace", "init_plan", "digest-key")
    receipt = exchange(payload)
    import hashlib

    assert receipt["request_digest"] == hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


def test_admin_rejects_unreviewed_init(tmp_path: Path) -> None:
    receipt = exchange(request(tmp_path / "workspace", "init", "apply-key"))
    assert receipt["status"] == "failed"
    assert receipt["error"]["code"] == "invalid_request"


def test_admin_returns_a_structured_error_for_noncanonical_values(tmp_path: Path) -> None:
    payload = request(tmp_path / "workspace", "doctor", "bad-number")
    payload["parameters"] = {"unsupported": float("nan")}
    receipt = exchange(payload)
    assert receipt["status"] == "failed"
    assert receipt["error"]["code"] == "invalid_request"
    assert len(receipt["request_digest"]) == 64
