#!/usr/bin/env python3
"""Regenerate deterministic packaged wire bundles and their manifests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGE = ROOT / "packages" / "rail-py"
sys.path.insert(0, str(PYTHON_PACKAGE))

from krail.provider.opensaddle_v1 import canonical_json  # noqa: E402
from rail.wire_bundle import bundle_json  # noqa: E402


RESOURCE_ROOT = PYTHON_PACKAGE / "krail" / "resources" / "contracts"


def _manifest(bundle: str, paths: list[str], **metadata: object) -> None:
    root = RESOURCE_ROOT / bundle
    files = []
    for relative in paths:
        payload = (root / relative).read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    content_digest = hashlib.sha256(canonical_json(files)).hexdigest()
    payload = {
        "manifest_version": "krail.resource-manifest.v1",
        "bundle": bundle,
        **metadata,
        "files": files,
        "content_digest": f"sha256:{content_digest}",
    }
    (root / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    provider_root = RESOURCE_ROOT / "krail.provider.v1"
    canonical_provider_files = {
        path: canonical_json(json.loads((provider_root / path).read_text(encoding="utf-8")))
        for path in (
            "schemas/protocol.schema.json",
            "fixtures/conformance.json",
            "fixtures/semver-compatibility.json",
        )
    }
    canonical_provider_digests = {
        path: "sha256:" + hashlib.sha256(payload).hexdigest()
        for path, payload in canonical_provider_files.items()
    }
    canonical_bundle_digest = hashlib.sha256(
        b"\0".join(canonical_provider_files.values())
    ).hexdigest()
    _manifest(
        "krail.provider.v1",
        [
            "README.md",
            "schemas/protocol.schema.json",
            "fixtures/conformance.json",
            "fixtures/semver-compatibility.json",
        ],
        source_repository="https://github.com/AkeBoss-tech/opensaddle",
        source_commit="377af5a5a60a4cffd21819e7bd657881e13ba9e7",
        source_path="contracts/krail.provider.v1",
        canonical_json_digests=canonical_provider_digests,
        canonical_bundle_digest=f"sha256:{canonical_bundle_digest}",
    )
    context_root = RESOURCE_ROOT / "krail.context-brief.v1"
    context_root.mkdir(parents=True, exist_ok=True)
    context_payload = bundle_json()
    context_bundle = json.loads(context_payload)
    (context_root / "bundle.json").write_bytes(context_payload)
    provider_manifest = json.loads(
        (RESOURCE_ROOT / "krail.provider.v1" / "manifest.json").read_text(encoding="utf-8")
    )
    _manifest(
        "krail.context-brief.v1",
        ["bundle.json"],
        generator="scripts/generate_wire_bundles.py",
        generator_base_commit="d1edac8039a1103a97c6177b981617bd1eb64f31",
        descriptor_digest=context_bundle["descriptor_digest"],
        golden_request_digest=context_bundle["golden"]["request_digest"],
        golden_result_digest=context_bundle["golden"]["result_digest"],
        provider_contract_content_digest=provider_manifest["content_digest"],
        provider_contract_canonical_bundle_digest=provider_manifest[
            "canonical_bundle_digest"
        ],
        provider_contract_source_commit=provider_manifest["source_commit"],
    )


if __name__ == "__main__":
    main()
