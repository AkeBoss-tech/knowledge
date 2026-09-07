"""Pure semantic checks for asset-read conformance fixtures, not a transport."""

from __future__ import annotations

import base64
import hashlib


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def validate_response(value: dict[str, object]) -> None:
    """Validate relationships JSON Schema cannot express for a read response."""

    encoded = value["content_base64"]
    if not isinstance(encoded, str):
        raise ValueError("content_base64 must be a string")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("content_base64 must be valid base64") from exc
    returned = value["returned_bytes"]
    byte_range = value["range"]
    if not isinstance(returned, int) or not isinstance(byte_range, dict):
        raise ValueError("returned bytes and range are required")
    if returned != len(decoded):
        raise ValueError("returned_bytes must equal decoded base64 length")
    if returned > byte_range["length"]:
        raise ValueError("returned bytes exceed requested range")
    if value["chunk_digest"] != _digest(decoded):
        raise ValueError("chunk digest does not match decoded bytes")
    total = value.get("total_bytes")
    if total is not None and (not isinstance(total, int) or byte_range["start"] + returned > total):
        raise ValueError("returned range exceeds declared asset length")
    if value["complete"]:
        if total is None or byte_range["start"] != 0 or returned != total:
            raise ValueError("complete response must cover exactly the whole asset")
        asset_ref = value["asset_ref"]
        if not isinstance(asset_ref, dict) or asset_ref["digest"] != _digest(decoded):
            raise ValueError("complete response does not match immutable asset digest")
