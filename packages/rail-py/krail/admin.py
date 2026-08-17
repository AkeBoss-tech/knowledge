"""OpenSaddle-facing ``krail.admin.v1`` compatibility adapter.

The local management engine is workspace-bound and uses its own typed request
models.  OpenSaddle launches this short-lived adapter with an exact registered
project root in the request.  The adapter translates only the four public
administration operations and returns a canonical, request-bound receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import rfc8785

from krail.management.v1 import CONTRACT as MANAGEMENT_CONTRACT
from krail.management.v1 import ManagementService


CONTRACT = "krail.admin.v1"
OPERATIONS = frozenset({"init_plan", "init", "doctor", "reindex"})


def _digest(value: object) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _provider_version() -> str:
    try:
        return version("krail")
    except PackageNotFoundError:
        return "1.2.0rc2"


def _error_code(code: str) -> str:
    if code in {"STALE_WORKSPACE", "PLAN_PRECONDITION_MISMATCH", "PLAN_DRIFT"}:
        return "stale_version"
    if code in {"INVALID_REQUEST", "INVALID_JSON", "UNSUPPORTED_OPERATION"}:
        return "invalid_request"
    if code in {"UNAVAILABLE", "RESOURCE_LIMIT"}:
        return "unavailable"
    if code == "INTERNAL_ERROR":
        return "internal_error"
    return "conflict"


def _validate_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    required = {
        "contract_version",
        "operation",
        "operation_id",
        "project_root",
        "idempotency_key",
        "parameters",
    }
    if set(payload) != required:
        raise ValueError("request fields do not match krail.admin.v1")
    if payload["contract_version"] != CONTRACT:
        raise ValueError("unsupported contract version")
    if payload["operation"] not in OPERATIONS:
        raise ValueError("unsupported administration operation")
    if not isinstance(payload["parameters"], dict):
        raise ValueError("parameters must be an object")
    for key in ("operation_id", "project_root", "idempotency_key"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError(f"{key} must be a non-empty string")
    return payload


def _management_request(
    service: ManagementService,
    request: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    operation = request["operation"]
    parameters = request["parameters"]
    request_id = request["operation_id"]

    if operation == "doctor":
        return "doctor", {
            "contract": MANAGEMENT_CONTRACT,
            "request_id": request_id,
            "operation": "doctor",
        }

    inspection = service.handle(
        {
            "contract": MANAGEMENT_CONTRACT,
            "request_id": f"{request_id}:inspect",
            "operation": "inspect",
        }
    )
    if not inspection.get("ok"):
        return "inspect", inspection
    state = inspection["result"]
    expected_version = parameters.get(
        "expected_workspace_version", state["workspace_version"]
    )
    expected_digest = parameters.get(
        "expected_workspace_digest", state["workspace_digest"]
    )

    if operation == "init_plan":
        return "plan_init", {
            "contract": MANAGEMENT_CONTRACT,
            "request_id": request_id,
            "operation": "plan_init",
            "expected_workspace_version": expected_version,
            "expected_workspace_digest": expected_digest,
            "options": parameters.get("options", {}),
        }
    if operation == "init":
        plan = parameters.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("init requires the previously reviewed plan")
        return "apply_init", {
            "contract": MANAGEMENT_CONTRACT,
            "request_id": request_id,
            "operation": "apply_init",
            "idempotency_key": request["idempotency_key"],
            "expected_workspace_version": parameters.get(
                "expected_workspace_version", plan.get("workspace_version")
            ),
            "expected_workspace_digest": parameters.get(
                "expected_workspace_digest", plan.get("workspace_digest")
            ),
            "plan": plan,
        }
    return "reindex", {
        "contract": MANAGEMENT_CONTRACT,
        "request_id": request_id,
        "operation": "reindex",
        "idempotency_key": request["idempotency_key"],
        "expected_workspace_version": expected_version,
        "expected_workspace_digest": expected_digest,
    }


def exchange(payload: Any) -> dict[str, Any]:
    canonical_error: Exception | None = None
    try:
        request_digest = _digest(payload)
    except (TypeError, rfc8785.CanonicalizationError) as exc:
        canonical_error = exc
        request_digest = hashlib.sha256(b"invalid-krail-admin-request").hexdigest()
    operation = payload.get("operation", "doctor") if isinstance(payload, dict) else "doctor"
    operation_id = payload.get("operation_id", "invalid") if isinstance(payload, dict) else "invalid"
    if canonical_error is not None:
        return {
            "contract_version": CONTRACT,
            "operation": operation if operation in OPERATIONS else "doctor",
            "operation_id": operation_id if isinstance(operation_id, str) and operation_id else "invalid",
            "status": "failed",
            "provider_version": _provider_version(),
            "request_digest": request_digest,
            "result": {},
            "error": {
                "code": "invalid_request",
                "message": "request is not canonical JSON",
                "retryable": False,
            },
        }
    try:
        request = _validate_request(payload)
        root = Path(request["project_root"]).expanduser().resolve(strict=False)
        service = ManagementService(root)
        translated_operation, translated = _management_request(service, request)
        if translated_operation == "inspect" and translated.get("ok") is False:
            response = translated
        else:
            response = service.handle(translated)
        if not response.get("ok"):
            error = response.get("error") or {}
            return {
                "contract_version": CONTRACT,
                "operation": request["operation"],
                "operation_id": request["operation_id"],
                "status": "failed",
                "provider_version": _provider_version(),
                "request_digest": request_digest,
                "result": {},
                "error": {
                    "code": _error_code(str(error.get("code", "INTERNAL_ERROR"))),
                    "message": str(error.get("message", "KRAIL administration failed")),
                    "retryable": str(error.get("code", "")) in {"UNAVAILABLE"},
                },
            }
        return {
            "contract_version": CONTRACT,
            "operation": request["operation"],
            "operation_id": request["operation_id"],
            "status": "planned" if request["operation"] == "init_plan" else "succeeded",
            "provider_version": _provider_version(),
            "request_digest": request_digest,
            "result": response["result"],
        }
    except (TypeError, ValueError, rfc8785.CanonicalizationError) as exc:
        return {
            "contract_version": CONTRACT,
            "operation": operation if operation in OPERATIONS else "doctor",
            "operation_id": operation_id if isinstance(operation_id, str) and operation_id else "invalid",
            "status": "failed",
            "provider_version": _provider_version(),
            "request_digest": request_digest,
            "result": {},
            "error": {
                "code": "invalid_request",
                "message": str(exc),
                "retryable": False,
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KRAIL OpenSaddle admin adapter")
    parser.add_argument("--contract", default=CONTRACT)
    parser.add_argument("action", nargs="?", default="exchange", choices=["exchange"])
    args = parser.parse_args(argv)
    if args.contract != CONTRACT:
        parser.error(f"only {CONTRACT} is supported")
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps(exchange({"invalid_json": str(exc)}), separators=(",", ":")))
        return 0
    print(json.dumps(exchange(payload), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
