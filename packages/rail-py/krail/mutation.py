"""Bounded OpenSaddle-to-KRAIL candidate mutation contract.

This surface is intentionally separate from lifecycle administration.  Its
only v1 operation moves a human-reviewed OpenSaddle candidate into KRAIL's raw
inbox; it cannot write stable topics or mark knowledge as trusted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import rail
import rfc8785

from krail.management.v1 import workspace_state


CONTRACT = "krail.mutation.v1"
OPERATION = "capture_candidate"
MAX_SUMMARY_BYTES = 16_384
MAX_SOURCE_IDS = 32


def _canonical(value: object) -> bytes:
    return rfc8785.dumps(value)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _provider_version() -> str:
    try:
        return version("krail")
    except PackageNotFoundError:
        return "1.2.0rc1"


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: candidate[key] for key in ("candidate_id", "kind", "title", "summary", "source_ids")}


def _validate(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    fields = {
        "contract_version", "operation", "operation_id", "project_root",
        "idempotency_key", "expected_workspace_digest", "candidate",
    }
    if set(payload) != fields or payload.get("contract_version") != CONTRACT:
        raise ValueError("request fields do not match krail.mutation.v1")
    if payload.get("operation") != OPERATION:
        raise ValueError("unsupported mutation operation")
    for key in ("operation_id", "project_root", "idempotency_key", "expected_workspace_digest"):
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError(f"{key} must be a bounded non-empty string")
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) != {
        "candidate_id", "kind", "title", "summary", "source_ids", "candidate_digest",
    }:
        raise ValueError("candidate fields do not match krail.mutation.v1")
    if not isinstance(candidate["candidate_id"], str) or not 1 <= len(candidate["candidate_id"]) <= 200:
        raise ValueError("candidate_id is invalid")
    if candidate["kind"] not in {"source", "claim", "artifact"}:
        raise ValueError("candidate kind is invalid")
    if not isinstance(candidate["title"], str) or not 1 <= len(candidate["title"].strip()) <= 300:
        raise ValueError("candidate title is invalid")
    if not isinstance(candidate["summary"], str) or not candidate["summary"].strip():
        raise ValueError("candidate summary is required")
    if len(candidate["summary"].encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise ValueError("candidate summary exceeds its byte limit")
    source_ids = candidate["source_ids"]
    if not isinstance(source_ids, list) or len(source_ids) > MAX_SOURCE_IDS or not all(
        isinstance(item, str) and 1 <= len(item) <= 200 for item in source_ids
    ):
        raise ValueError("candidate source IDs are invalid")
    if candidate["candidate_digest"] != _digest(_candidate_payload(candidate)):
        raise ValueError("candidate digest does not match its reviewed content")
    return payload, candidate


def _receipt_path(root: Path, idempotency_key: str) -> Path:
    name = hashlib.sha256(idempotency_key.encode()).hexdigest() + ".json"
    return root / ".krail" / "management" / "v1" / "mutation-receipts" / name


def _response(request: dict[str, Any], request_digest: str, *, status: str, result: dict[str, Any], error: dict[str, Any] | None = None) -> dict[str, Any]:
    value = {
        "contract_version": CONTRACT,
        "operation": OPERATION,
        "operation_id": request.get("operation_id", "invalid"),
        "status": status,
        "provider_version": _provider_version(),
        "request_digest": request_digest,
        "result": result,
    }
    if error is not None:
        value["error"] = error
    return value


def exchange(payload: Any) -> dict[str, Any]:
    try:
        request_digest = _digest(payload)
    except (TypeError, rfc8785.CanonicalizationError):
        request_digest = hashlib.sha256(b"invalid-krail-mutation-request").hexdigest()
        return _response({}, request_digest, status="failed", result={}, error={
            "code": "invalid_request", "message": "request is not canonical JSON", "retryable": False,
        })
    request = payload if isinstance(payload, dict) else {}
    try:
        request, candidate = _validate(payload)
        root = Path(request["project_root"]).expanduser().resolve(strict=True)
        receipt_path = _receipt_path(root, request["idempotency_key"])
        if receipt_path.is_file():
            stored = json.loads(receipt_path.read_text("utf-8"))
            if stored.get("request_digest") != request_digest:
                return _response(request, request_digest, status="failed", result={}, error={
                    "code": "conflict", "message": "idempotency key is bound to another request", "retryable": False,
                })
            replay = dict(stored)
            replay["result"] = {**replay["result"], "replayed": True}
            return replay
        before = workspace_state(root)
        if not before["initialized"]:
            raise RuntimeError("KRAIL workspace is not initialized")
        if before["workspace_digest"] != request["expected_workspace_digest"]:
            return _response(request, request_digest, status="failed", result={}, error={
                "code": "stale_version", "message": "workspace digest changed after proposal review", "retryable": False,
            })

        body = candidate["summary"].strip()
        provenance = [f"OpenSaddle candidate: {candidate['candidate_id']}"]
        if candidate["source_ids"]:
            provenance.append("Source IDs: " + ", ".join(candidate["source_ids"]))
        capture = rail.local(str(root)).capture(
            text=body + "\n\n" + "\n".join(provenance),
            title=candidate["title"].strip(),
            kind=candidate["kind"],
        )
        if capture.get("status") != "captured":
            raise PermissionError(str(capture.get("message") or "KRAIL inbox capture was denied"))
        after = workspace_state(root)
        result = {
            "candidate_id": candidate["candidate_id"],
            "capture_path": capture["path"],
            "workspace_digest_before": before["workspace_digest"],
            "workspace_digest_after": after["workspace_digest"],
            "replayed": False,
            "trust_state": "raw_inbox",
        }
        receipt = _response(request, request_digest, status="succeeded", result=result)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(_canonical(receipt) + b"\n")
        os.replace(temporary, receipt_path)
        return receipt
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
        code = "unauthorized" if isinstance(exc, PermissionError) else "invalid_request"
        return _response(request, request_digest, status="failed", result={}, error={
            "code": code, "message": str(exc), "retryable": False,
        })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KRAIL reviewed-candidate mutation adapter")
    parser.add_argument("--contract", default=CONTRACT)
    parser.add_argument("action", nargs="?", default="exchange", choices=["exchange"])
    args = parser.parse_args(argv)
    if args.contract != CONTRACT:
        parser.error(f"only {CONTRACT} is supported")
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        payload = {"invalid_json": str(exc)}
    print(json.dumps(exchange(payload), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
