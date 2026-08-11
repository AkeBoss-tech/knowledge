from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from krail.epistemic_history import OperationContext
from krail.provider.opensaddle_v1 import canonical_json
from krail.provider.resources import (
    CONTEXT_BRIEF_BUNDLE,
    PROVIDER_CONTRACT,
    resource_bytes,
    resource_json,
    verify_manifest,
)
from rail.context_brief import context_brief_digest
from rail.wire_bundle import (
    build_context_brief_bundle,
    build_context_brief_models,
    bundle_json,
)


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
    wire = bundle["wire_capability_descriptor"]
    assert bundle["wire_descriptor_digest"] == wire["descriptor_digest"]
    wire_body = {key: value for key, value in wire.items() if key != "descriptor_digest"}
    assert wire["descriptor_digest"] == "sha256:" + hashlib.sha256(
        canonical_json(wire_body)
    ).hexdigest()
    assert bundle["internal_descriptor_digest"] == bundle[
        "internal_provider_descriptor_provenance"
    ]["descriptor_digest"]
    assert bundle["wire_descriptor_digest"] != bundle["internal_descriptor_digest"]
    assert manifest["wire_descriptor_digest"] == bundle["wire_descriptor_digest"]
    assert manifest["files"][0]["sha256"] == hashlib.sha256(packaged).hexdigest()


def test_context_brief_golden_values_validate_projected_embedded_schemas() -> None:
    bundle = resource_json(CONTEXT_BRIEF_BUNDLE, "bundle.json")
    checker = FormatChecker()
    wire_operation = bundle["wire_capability_descriptor"]["operations"][0]

    Draft202012Validator(wire_operation["input_schema"], format_checker=checker).validate(
        bundle["golden"]["request"]
    )
    Draft202012Validator(wire_operation["output_schema"], format_checker=checker).validate(
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
    assert bundle["capability_mapping"]["context_brief_contract"] == bundle["bundle_version"]


def test_internal_descriptor_is_provenance_only_and_rejects_wire_golden() -> None:
    bundle = resource_json(CONTEXT_BRIEF_BUNDLE, "bundle.json")
    internal = bundle["internal_provider_descriptor_provenance"]
    operation = next(
        item for item in internal["operations"] if item["operation_id"] == "context_brief"
    )

    assert list(
        Draft202012Validator(operation["input_schema"]).iter_errors(
            bundle["golden"]["request"]
        )
    )
    assert list(
        Draft202012Validator(operation["output_schema"]).iter_errors(
            bundle["golden"]["result"]
        )
    )
    assert operation["input_schema"] != bundle["wire_capability_descriptor"][
        "operations"
    ][0]["input_schema"]


def test_golden_brief_uses_complete_production_digest_semantics() -> None:
    request, brief, _bindings, _authorization = build_context_brief_models()
    bundle = build_context_brief_bundle()

    values = {
        "repository": brief.repository,
        "issue": brief.issue,
        "evaluated_at": brief.evaluated_at,
        "evidence": brief.evidence,
        "assertions": brief.assertions,
        "freshness": brief.freshness,
        "conflicts": brief.conflicts,
        "gaps": brief.gaps,
        "omissions": brief.omissions,
        "ranking_trace": brief.ranking_trace,
        "processing_versions": brief.processing_versions,
        "operation_context": request.operation_context,
        "truncated": brief.truncated,
    }
    recomputed = context_brief_digest(**values)

    assert recomputed == brief.brief_digest
    assert recomputed == bundle["golden"]["result"]["brief_digest"]
    assert recomputed == brief.domain_event_ref.event_digest
    assert recomputed == bundle["golden"]["result"]["domain_event_ref"]["event_digest"]
    changed_correlation = context_brief_digest(
        **{
            **values,
            "operation_context": OperationContext(
                operation_id="operation/fixture-context-brief",
                correlation_id="correlation/changed",
            ),
        }
    )
    changed_gaps = context_brief_digest(
        **{**values, "gaps": brief.gaps + (brief.gaps[0].model_copy(update={"code": "changed-gap"}),)}
    )
    changed_ranking = context_brief_digest(
        **{**values, "ranking_trace": tuple(reversed(brief.ranking_trace))}
    )
    assert len({recomputed, changed_correlation, changed_gaps, changed_ranking}) == 4


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
