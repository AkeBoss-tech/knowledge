from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from krail.provider.resources import (
    CONTEXT_BRIEF_BUNDLE,
    PROVIDER_CONTRACT,
    resource_bytes,
    resource_json,
    verify_manifest,
)
from rail.wire_bundle import build_context_brief_bundle, bundle_json


PINNED = {
    "README.md": "1b34d47a8a9f50c5e28082f35e51d8cd8e10e9ed9d3e630eb1ab2caa2bd298c2",
    "schemas/protocol.schema.json": "c5404109a9ae2abe3e96658f46d8173245ac5a9b3b436a3f6f44626e1b5ddca7",
    "fixtures/conformance.json": "f3800b21e930885bf40037e10d8554e0bf5daf907d1c44256e2e9106aeb44e1a",
    "fixtures/semver-compatibility.json": "c19780fa3211e53ca226cfa4d85dd781845f91203f3676c4d3a8be8856d38279",
}

CANONICAL = {
    "schemas/protocol.schema.json": "sha256:4d58cf1315887da25f8c7e4d45003161ff1e06483744d24823b2ea26d5946f13",
    "fixtures/conformance.json": "sha256:aaab3c0a79ce538f0126cf6c54816c2ae3ef61175c050dc69644b7e9c8dbc06b",
    "fixtures/semver-compatibility.json": "sha256:f7283e6f438de425b77351451807c9b4772e0f47e56fc5532da0b81ced048d18",
}


def test_vendored_normative_contract_is_exactly_pinned_and_manifest_verified() -> None:
    manifest = verify_manifest(PROVIDER_CONTRACT)

    assert manifest["source_commit"] == "377af5a5a60a4cffd21819e7bd657881e13ba9e7"
    for path, expected in PINNED.items():
        assert hashlib.sha256(resource_bytes(PROVIDER_CONTRACT, path)).hexdigest() == expected
    assert manifest["canonical_json_digests"] == CANONICAL
    assert manifest["canonical_bundle_digest"] == (
        "sha256:e40c7240dadc8125d73949e353b20e2aef1b09d9ed3303277ab93cf3eb032c7c"
    )


def test_normative_locator_bounds_are_ijson_and_rfc8785_safe() -> None:
    protocol = resource_json(PROVIDER_CONTRACT, "schemas/protocol.schema.json")
    variants = protocol["$defs"]["Locator"]["oneOf"]
    span = next(item for item in variants if item["properties"]["kind"].get("const") == "span")

    assert span["properties"]["start"]["maximum"] == 9_007_199_254_740_990
    assert span["properties"]["end"]["maximum"] == 9_007_199_254_740_991


def test_context_brief_bundle_is_reproducible_manifested_and_digest_pinned() -> None:
    manifest = verify_manifest(CONTEXT_BRIEF_BUNDLE)
    packaged = resource_bytes(CONTEXT_BRIEF_BUNDLE, "bundle.json")
    bundle = json.loads(packaged)

    assert packaged == bundle_json()
    assert bundle == build_context_brief_bundle()
    assert bundle["bundle_version"] == "krail.context-brief.bundle.v1"
    assert bundle["descriptor_digest"] == bundle["provider_descriptor"]["descriptor_digest"]
    assert manifest["files"][0]["sha256"] == hashlib.sha256(packaged).hexdigest()


def test_context_brief_golden_values_validate_projected_embedded_schemas() -> None:
    bundle = resource_json(CONTEXT_BRIEF_BUNDLE, "bundle.json")
    checker = FormatChecker()

    Draft202012Validator(bundle["schemas"]["request"], format_checker=checker).validate(
        bundle["golden"]["request"]
    )
    Draft202012Validator(bundle["schemas"]["result"], format_checker=checker).validate(
        bundle["golden"]["result"]
    )
    repository = bundle["golden"]["request"]["repository"]
    issue = bundle["golden"]["request"]["issue"]
    assert repository["source"]["version"] == repository["version"]
    assert issue["source"]["digest"] == issue["digest"]
    assert bundle["golden"]["result"]["repository"] == repository
    assert bundle["golden"]["result"]["issue"] == issue
    protocol = resource_json(PROVIDER_CONTRACT, "schemas/protocol.schema.json")
    Draft202012Validator(protocol, format_checker=checker).validate(
        bundle["golden"]["provider_evidence_response"]
    )
    assert "contract_version" not in bundle["golden"]["request"]
    assert bundle["wire_projection"]["context_brief_contract"] == bundle["bundle_version"]


def test_regenerator_is_idempotent() -> None:
    root = Path(__file__).resolve().parents[3]
    before = {
        bundle: resource_bytes(bundle, "manifest.json")
        for bundle in (PROVIDER_CONTRACT, CONTEXT_BRIEF_BUNDLE)
    }
    subprocess.run(
        [sys.executable, "scripts/generate_wire_bundles.py"],
        cwd=root,
        check=True,
    )
    after = {
        bundle: resource_bytes(bundle, "manifest.json")
        for bundle in (PROVIDER_CONTRACT, CONTEXT_BRIEF_BUNDLE)
    }
    assert after == before
