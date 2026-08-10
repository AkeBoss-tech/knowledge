"""Access and verify packaged, language-neutral provider boundary resources."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

from krail.provider.opensaddle_v1 import canonical_json


_ROOT = "resources/contracts"
PROVIDER_CONTRACT = "krail.provider.v1"
CONTEXT_BRIEF_BUNDLE = "krail.context-brief.v1"


def resource_bytes(bundle: str, relative_path: str) -> bytes:
    if bundle not in {PROVIDER_CONTRACT, CONTEXT_BRIEF_BUNDLE}:
        raise ValueError("unknown packaged KRAIL contract bundle")
    if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
        raise ValueError("resource path must remain inside the selected bundle")
    return files("krail").joinpath(_ROOT, bundle, relative_path).read_bytes()


def resource_json(bundle: str, relative_path: str) -> Any:
    return json.loads(resource_bytes(bundle, relative_path))


def verify_manifest(bundle: str) -> dict[str, Any]:
    manifest = resource_json(bundle, "manifest.json")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("bundle manifest has no file inventory")
    canonical_entries: list[dict[str, Any]] = []
    for entry in entries:
        path = entry.get("path")
        expected = entry.get("sha256")
        payload = resource_bytes(bundle, path)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected or len(payload) != entry.get("bytes"):
            raise ValueError(f"packaged resource failed integrity verification: {path}")
        canonical_entries.append({"path": path, "sha256": actual, "bytes": len(payload)})
    content_digest = hashlib.sha256(canonical_json(canonical_entries)).hexdigest()
    if manifest.get("content_digest") != f"sha256:{content_digest}":
        raise ValueError("bundle manifest content digest does not match its inventory")
    return manifest


__all__ = [
    "CONTEXT_BRIEF_BUNDLE",
    "PROVIDER_CONTRACT",
    "resource_bytes",
    "resource_json",
    "verify_manifest",
]
