"""Deterministic Context Brief fixture for non-Python OpenSaddle consumers."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from krail.epistemic_history import DomainEventRef, OperationContext
from krail.provider.opensaddle_v1 import (
    AuthorizationProjection,
    DirectSourceBinding,
    bindings_by_exact_key,
    canonical_json,
    project_refs_in_value,
)
from krail.provider.resources import PROVIDER_CONTRACT, resource_json
from krail.provider.v1 import EvidenceItem, EvidencePacket, ResourceRef
from rail.capability_publication import context_brief_descriptor
from rail.context_brief import (
    ContextAssertion,
    ContextBrief,
    ContextBriefRequest,
    EvidenceGap,
    FreshnessAssessment,
    ProcessingVersion,
    RankingTraceEntry,
)


BUNDLE_VERSION = "krail.context-brief.bundle.v1"
_EVALUATED_AT = datetime(2026, 8, 7, 15, 30, tzinfo=UTC)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _ref(resource_id: str, resource_type: str, content: str, revision: str) -> ResourceRef:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ResourceRef(
        authority="https://knowledge.example.test/provider",
        resource_id=resource_id,
        resource_type=resource_type,
        version=f"git:{revision}",
        digest=f"sha256:{digest}",
    )


def _binding(ref: ResourceRef) -> DirectSourceBinding:
    return DirectSourceBinding(
        source_id=f"source/{ref.resource_id}",
        origin="git:https://example.test/acme/northstar.git",
        version=ref.version,
        digest=ref.digest,
    )


def _projected_context_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace only ref/evidence definitions with the normative wire shapes."""

    projected = copy.deepcopy(schema)
    protocol = resource_json(PROVIDER_CONTRACT, "schemas/protocol.schema.json")
    target_defs = projected.setdefault("$defs", {})
    normative_defs = protocol["$defs"]
    for name in (
        "Identifier",
        "TypeName",
        "Sha256",
        "Digest",
        "SourceVersion",
        "ResourceRef",
    ):
        target_defs[name] = copy.deepcopy(normative_defs[name])
    if "EvidencePacket" in target_defs:
        for name in ("AuthorizationSummary", "Locator", "Citation", "EvidenceResult"):
            target_defs[name] = copy.deepcopy(normative_defs[name])
        target_defs["EvidencePacket"] = copy.deepcopy(normative_defs["EvidencePacket"])
    return projected


def build_context_brief_bundle() -> dict[str, Any]:
    repository_text = "# Northstar\n\nRelease controller repository.\n"
    issue_text = "# Issue 184\n\nlinux-arm64 verification is required before merge.\n"
    repository = _ref("repository/northstar", "repository", repository_text, "8e4a1f0")
    issue = _ref("issue/184", "issue", issue_text, "9f7b2c1")
    bindings = bindings_by_exact_key(((repository, _binding(repository)), (issue, _binding(issue))))
    authorization = AuthorizationProjection(authorized=True)
    versions = (
        ProcessingVersion(component="conflict", version="krail.context-conflict.v1"),
        ProcessingVersion(component="context-brief", version="krail.context-brief.v1"),
        ProcessingVersion(component="freshness", version="krail.context-freshness.v1"),
        ProcessingVersion(component="ranking", version="krail.context-ranking.v1"),
    )
    request = ContextBriefRequest(
        repository=repository,
        issue=issue,
        evaluated_at=_EVALUATED_AT,
        query="Northstar issue 184 linux-arm64 verification",
        max_items=2,
        max_total_bytes=4096,
        operation_context=OperationContext(
            operation_id="operation/fixture-context-brief",
            correlation_id="correlation/fixture-context-brief",
        ),
        processing_versions=versions,
    )
    items = [
        EvidenceItem(
            source=repository,
            locator=f"{repository.resource_id}#utf8:0-{len(repository_text.encode('utf-8'))}",
            excerpt=repository_text,
            media_type="text/markdown",
        ),
        EvidenceItem(
            source=issue,
            locator=f"{issue.resource_id}#utf8:0-{len(issue_text.encode('utf-8'))}",
            excerpt=issue_text,
            media_type="text/markdown",
        ),
    ]
    packet_fingerprint = {
        "query": request.query,
        "items": [item.model_dump(mode="json") for item in items],
        "versions": [item.model_dump(mode="json") for item in versions],
    }
    packet = EvidencePacket(
        packet_id="packet/" + _digest(packet_fingerprint).removeprefix("sha256:")[:24],
        query=request.query or "",
        generated_at=_EVALUATED_AT,
        items=items,
    )
    assertions = tuple(
        ContextAssertion(
            assertion_id=_digest(
                {
                    "source": item.source.model_dump(mode="json"),
                    "locator": item.locator,
                    "text": item.excerpt,
                    "versions": [version.model_dump(mode="json") for version in versions],
                }
            ),
            text=item.excerpt,
            source=item.source,
            locator=item.locator,
            processing_versions=versions,
        )
        for item in items
    )
    brief_body = {
        "repository": repository.model_dump(mode="json"),
        "issue": issue.model_dump(mode="json"),
        "evidence": packet.model_dump(mode="json"),
        "evaluated_at": _EVALUATED_AT.isoformat(),
        "processing_versions": [item.model_dump(mode="json") for item in versions],
    }
    brief_digest = _digest(brief_body)
    brief = ContextBrief(
        brief_digest=brief_digest,
        repository=repository,
        issue=issue,
        evaluated_at=_EVALUATED_AT,
        evidence=packet,
        assertions=assertions,
        freshness=(
            FreshnessAssessment(
                source=repository,
                status="fresh",
                basis="fixture source is pinned to an immutable revision",
            ),
            FreshnessAssessment(
                source=issue,
                status="fresh",
                basis="fixture source is pinned to an immutable revision",
            ),
        ),
        conflicts=(),
        gaps=(
            EvidenceGap(
                code="supplementary-evidence-missing",
                message="The bounded fixture contains only its exact repository and issue inputs.",
            ),
        ),
        omissions=(),
        ranking_trace=(
            RankingTraceEntry(rank=1, source=issue),
            RankingTraceEntry(rank=2, source=repository),
        ),
        processing_versions=versions,
        domain_event_ref=DomainEventRef.for_digest(
            event_type="krail.context-brief-assembled.v1",
            digest=brief_digest,
            context=request.operation_context,
        ),
    )
    descriptor = context_brief_descriptor()
    context_operation = next(
        operation for operation in descriptor.operations if operation.operation_id == "context_brief"
    )
    projected_request = project_refs_in_value(
        request,
        source_bindings=bindings,
        authorization=authorization,
    )
    projected_result = project_refs_in_value(
        brief,
        source_bindings=bindings,
        authorization=authorization,
    )
    return {
        "bundle_version": BUNDLE_VERSION,
        "provider_descriptor": descriptor.model_dump(mode="json"),
        "descriptor_digest": descriptor.descriptor_digest,
        "wire_projection": {
            "context_brief_contract": BUNDLE_VERSION,
            "provider_contract_components": [
                "ResourceRef",
                "SourceVersion",
                "EvidencePacket",
            ],
            "authorization": "explicit-authorized-direct-sources-only",
            "unsupported": [
                "derived-resource-projection",
                "truncated-evidence-packet-projection",
                "query-based-retrieve-evidence-as-ref-based-retrieve-evidence",
            ],
        },
        "schemas": {
            "request": _projected_context_schema(context_operation.input_schema),
            "result": _projected_context_schema(context_operation.output_schema),
        },
        "golden": {
            "request": projected_request,
            "result": projected_result,
            "provider_evidence_response": {
                "contract_version": "krail.provider.v1",
                "operation": "retrieve_evidence",
                "packet": projected_result["evidence"],
            },
            "request_digest": _digest(projected_request),
            "result_digest": _digest(projected_result),
        },
    }


def bundle_json() -> bytes:
    return json.dumps(
        build_context_brief_bundle(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


__all__ = ["BUNDLE_VERSION", "build_context_brief_bundle", "bundle_json"]
