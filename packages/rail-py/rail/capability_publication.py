"""Provider-issued publication for bounded KRAIL semantic capabilities."""

from __future__ import annotations

from krail.provider.capabilities import (
    CapabilityDescriptor,
    CapabilityLimit,
    CapabilityNegotiationRequest,
    CapabilityNegotiationResult,
    CapabilityOperation,
    EffectDeclaration,
    SemanticProcessingVersion,
    negotiate,
)
from krail.provider.v1 import (
    MAX_EVIDENCE_CONTENT_BYTES,
    MAX_EVIDENCE_ITEMS,
    MAX_EVIDENCE_ITEM_BYTES,
    MAX_EVIDENCE_PACKET_BYTES,
    RetrieveEvidenceRequest,
    RetrieveEvidenceResult,
)
from rail.context_brief import (
    CONFLICT_VERSION,
    CONTEXT_BRIEF_VERSION,
    FRESHNESS_VERSION,
    MAX_CONTEXT_ITEMS,
    RANKING_VERSION,
    ContextBrief,
    ContextBriefRequest,
)
from rail.authorized_context import (
    AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
    AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
    AUTHORIZED_CONTEXT_PACKET_SCHEMA_VERSION,
    MAX_AUTHORIZED_CONTEXT_PACKET_BYTES,
    MAX_AUTHORIZED_CONTEXT_TOKENS,
    AuthorizedContextPacketCreateRequest,
    AuthorizedContextPacketReadRequest,
    AuthorizedContextPacketReadResult,
    SharedAuthorizedContextPacket,
)
from rail.core_provenance import (
    PROCEDURE_EXPLANATION_CAPABILITY_ID,
    PROCEDURE_EXPLANATION_CAPABILITY_VERSION,
    PROCEDURE_EXPLANATION_VERSION,
    ProcedureActionableGuidanceRequest,
    ProcedureActionableGuidanceResult,
    ProcedureExplanation,
    ProcedureExplanationRequest,
)
from rail.outcome_observations import (
    OUTCOME_OBSERVATION_VERSION,
    OUTCOME_PROCESSING_VERSION,
    OutcomeIngestEnvelope,
    OutcomeObservation,
)
from rail.verification_evidence import (
    MAX_CHANGED_FILES,
    MAX_CHECKS,
    MAX_CLAIMS,
    MAX_CONFLICTS,
    MAX_GAPS,
    VERIFICATION_EVIDENCE_VERSION,
    VERIFICATION_PROCESSING_VERSION,
    VerificationEvidence,
    VerificationEvidenceRequest,
)
from rail.company_guidance_packet import (
    CAPABILITY_ID as COMPANY_GUIDANCE_PACKET_CAPABILITY_ID,
    CAPABILITY_VERSION as COMPANY_GUIDANCE_PACKET_CAPABILITY_VERSION,
    CompanyGuidancePacket,
    CompanyGuidancePacketCreateRequest,
    CompanyGuidancePacketReadRequest,
    CompanyGuidancePacketReadResult,
)
from krail.provider.semantic import (
    AssembleCrossSourceEvidenceRequest,
    AssembleCrossSourceEvidenceResult,
    CompareObservationsRequest,
    CompareObservationsResult,
    ExplainConflictRequest,
    ExplainConflictResult,
    GetEntityRequest,
    GetEntityResult,
    ListOntologyPackagesRequest,
    ListOntologyPackagesResult,
    ListOntologyProposalHistoryRequest,
    ListOntologyProposalHistoryResult,
    ResolveEntityRequest,
    ResolveEntityResult,
    TraverseRelationshipsRequest,
    TraverseRelationshipsResult,
    MAX_BYTES as SEMANTIC_MAX_BYTES,
    MAX_DEPTH as SEMANTIC_MAX_DEPTH,
    MAX_EDGES as SEMANTIC_MAX_EDGES,
    MAX_ITEMS as SEMANTIC_MAX_ITEMS,
    MAX_NODES as SEMANTIC_MAX_NODES,
    MAX_TIME_MS as SEMANTIC_MAX_TIME_MS,
)
from rail.semantic.operations import SEMANTIC_PROCESSING_VERSION


CAPABILITY_ID = "krail.context-brief"
CAPABILITY_VERSION = "1.0.0"
VERIFICATION_CAPABILITY_ID = "krail.verification-evidence"
OUTCOME_CAPABILITY_ID = "krail.outcome-evidence"
SEMANTIC_CAPABILITY_ID = "krail.semantic-operations"


def context_brief_descriptor() -> CapabilityDescriptor:
    """Return the immutable provider-issued descriptor for this source tree."""
    return CapabilityDescriptor.issue(
        provider="krail.local",
        capability_id=CAPABILITY_ID,
        semantic_version=CAPABILITY_VERSION,
        operations=(
            CapabilityOperation(
                operation_id="retrieve_evidence",
                input_schema=RetrieveEvidenceRequest.model_json_schema(),
                output_schema=RetrieveEvidenceResult.model_json_schema(),
            ),
            CapabilityOperation(
                operation_id="context_brief",
                input_schema=ContextBriefRequest.model_json_schema(),
                output_schema=ContextBrief.model_json_schema(),
            ),
        ),
        limits=(
            CapabilityLimit(name="max_context_items", value=MAX_CONTEXT_ITEMS, unit="items"),
            CapabilityLimit(name="max_evidence_items", value=MAX_EVIDENCE_ITEMS, unit="items"),
            CapabilityLimit(name="max_evidence_item_bytes", value=MAX_EVIDENCE_ITEM_BYTES, unit="utf8-bytes"),
            CapabilityLimit(name="max_evidence_content_bytes", value=MAX_EVIDENCE_CONTENT_BYTES, unit="utf8-bytes"),
            CapabilityLimit(name="max_evidence_packet_bytes", value=MAX_EVIDENCE_PACKET_BYTES, unit="utf8-bytes"),
        ),
        semantic_processing_versions=(
            SemanticProcessingVersion(component="provider-contract", version="krail.provider.model.v1"),
            SemanticProcessingVersion(component="context-brief", version=CONTEXT_BRIEF_VERSION),
            SemanticProcessingVersion(component="ranking", version=RANKING_VERSION),
            SemanticProcessingVersion(component="freshness", version=FRESHNESS_VERSION),
            SemanticProcessingVersion(component="conflict", version=CONFLICT_VERSION),
        ),
    )


def authorized_context_packet_descriptor() -> CapabilityDescriptor:
    """Publish immutable packet creation and fresh reauthorization reads."""

    return CapabilityDescriptor.issue(
        provider="krail.local",
        capability_id=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
        semantic_version=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
        operations=(
            CapabilityOperation(
                operation_id="create_authorized_context_packet",
                input_schema=AuthorizedContextPacketCreateRequest.model_json_schema(),
                output_schema=SharedAuthorizedContextPacket.model_json_schema(),
            ),
            CapabilityOperation(
                operation_id="read_authorized_context_packet",
                input_schema=AuthorizedContextPacketReadRequest.model_json_schema(),
                output_schema=AuthorizedContextPacketReadResult.model_json_schema(),
            ),
        ),
        effects=EffectDeclaration(),
        limits=(
            CapabilityLimit(
                name="max_authorized_context_packet_bytes",
                value=MAX_AUTHORIZED_CONTEXT_PACKET_BYTES,
                unit="utf8-bytes",
            ),
            CapabilityLimit(
                name="max_authorized_context_tokens",
                value=MAX_AUTHORIZED_CONTEXT_TOKENS,
                unit="items",
            ),
            CapabilityLimit(
                name="max_exact_resource_refs", value=256, unit="items"
            ),
            CapabilityLimit(
                name="max_reauthorization_receipt_bytes", value=8192, unit="utf8-bytes"
            ),
        ),
        semantic_processing_versions=(
            SemanticProcessingVersion(
                component="provider-contract", version="krail.provider.v1"
            ),
            SemanticProcessingVersion(
                component="authorized-context-packet",
                version=AUTHORIZED_CONTEXT_PACKET_SCHEMA_VERSION,
            ),
            SemanticProcessingVersion(
                component="context-brief", version=CONTEXT_BRIEF_VERSION
            ),
        ),
    )


def verification_evidence_descriptor() -> CapabilityDescriptor:
    """Publish deterministic interpretation of supplied verification artifacts."""
    return CapabilityDescriptor.issue(
        provider="krail.local",
        capability_id=VERIFICATION_CAPABILITY_ID,
        semantic_version=CAPABILITY_VERSION,
        operations=(
            CapabilityOperation(
                operation_id="assemble_verification_evidence",
                input_schema=VerificationEvidenceRequest.model_json_schema(),
                output_schema=VerificationEvidence.model_json_schema(),
            ),
        ),
        effects=EffectDeclaration(),
        limits=(
            CapabilityLimit(name="max_changed_files", value=MAX_CHANGED_FILES, unit="items"),
            CapabilityLimit(name="max_checks", value=MAX_CHECKS, unit="items"),
            CapabilityLimit(name="max_claims", value=MAX_CLAIMS, unit="items"),
            CapabilityLimit(name="max_gaps", value=MAX_GAPS, unit="items"),
            CapabilityLimit(name="max_conflicts", value=MAX_CONFLICTS, unit="items"),
            CapabilityLimit(name="max_evidence_content_bytes", value=MAX_EVIDENCE_CONTENT_BYTES, unit="utf8-bytes"),
            CapabilityLimit(name="max_evidence_packet_bytes", value=MAX_EVIDENCE_PACKET_BYTES, unit="utf8-bytes"),
        ),
        semantic_processing_versions=(
            SemanticProcessingVersion(component="provider-contract", version="krail.provider.v1"),
            SemanticProcessingVersion(component="verification-evidence", version=VERIFICATION_EVIDENCE_VERSION),
            SemanticProcessingVersion(component="verification-assembly", version=VERIFICATION_PROCESSING_VERSION),
        ),
    )


def outcome_evidence_descriptor() -> CapabilityDescriptor:
    """Publish pure ingestion of pinned provider observations into KRAIL semantics."""
    return CapabilityDescriptor.issue(
        provider="krail.local",
        capability_id=OUTCOME_CAPABILITY_ID,
        semantic_version=CAPABILITY_VERSION,
        operations=(
            CapabilityOperation(
                operation_id="ingest_outcome_evidence",
                input_schema=OutcomeIngestEnvelope.model_json_schema(),
                output_schema=OutcomeObservation.model_json_schema(),
            ),
        ),
        effects=EffectDeclaration(),
        limits=(
            CapabilityLimit(name="max_semantic_assertions", value=64, unit="items"),
            CapabilityLimit(name="max_processing_versions", value=16, unit="items"),
            CapabilityLimit(name="max_assertion_bytes", value=16_384, unit="utf8-bytes"),
            CapabilityLimit(name="max_summary_bytes", value=4096, unit="utf8-bytes"),
        ),
        semantic_processing_versions=(
            SemanticProcessingVersion(component="provider-contract", version="krail.provider.v1"),
            SemanticProcessingVersion(component="outcome-observation", version=OUTCOME_OBSERVATION_VERSION),
            SemanticProcessingVersion(component="outcome-ingestion", version=OUTCOME_PROCESSING_VERSION),
        ),
    )


def procedure_explanation_descriptor() -> CapabilityDescriptor:
    """Publish the bounded, read-only procedure-history explanation contract."""
    return CapabilityDescriptor.issue(
        provider="krail.local",
        capability_id=PROCEDURE_EXPLANATION_CAPABILITY_ID,
        semantic_version=PROCEDURE_EXPLANATION_CAPABILITY_VERSION,
        operations=(
            CapabilityOperation(
                operation_id="explain_procedure",
                input_schema=ProcedureExplanationRequest.model_json_schema(),
                output_schema=ProcedureExplanation.model_json_schema(),
            ),
            CapabilityOperation(
                operation_id="actionable_guidance",
                input_schema=ProcedureActionableGuidanceRequest.model_json_schema(),
                output_schema=ProcedureActionableGuidanceResult.model_json_schema(),
            ),
        ),
        effects=EffectDeclaration(),
        limits=(
            CapabilityLimit(name="max_reviews", value=32, unit="items"),
            CapabilityLimit(name="max_explanation_bytes", value=131_072, unit="utf8-bytes"),
        ),
        semantic_processing_versions=(
            SemanticProcessingVersion(component="provider-contract", version="krail.provider.v1"),
            SemanticProcessingVersion(component="procedure-explanation", version=PROCEDURE_EXPLANATION_VERSION),
        ),
    )


def semantic_operations_descriptor() -> CapabilityDescriptor:
    operations = (
        ("resolve_entity", ResolveEntityRequest, ResolveEntityResult),
        ("get_entity", GetEntityRequest, GetEntityResult),
        ("traverse_relationships", TraverseRelationshipsRequest, TraverseRelationshipsResult),
        ("compare_observations", CompareObservationsRequest, CompareObservationsResult),
        ("explain_conflict", ExplainConflictRequest, ExplainConflictResult),
        ("assemble_cross_source_evidence", AssembleCrossSourceEvidenceRequest, AssembleCrossSourceEvidenceResult),
        ("list_ontology_packages", ListOntologyPackagesRequest, ListOntologyPackagesResult),
        ("list_ontology_proposal_history", ListOntologyProposalHistoryRequest, ListOntologyProposalHistoryResult),
    )
    return CapabilityDescriptor.issue(
        provider="krail.local",
        capability_id=SEMANTIC_CAPABILITY_ID,
        semantic_version=CAPABILITY_VERSION,
        operations=tuple(
            CapabilityOperation(
                operation_id=name,
                input_schema=request.model_json_schema(),
                output_schema=result.model_json_schema(),
            )
            for name, request, result in operations
        ),
        effects=EffectDeclaration(),
        limits=(
            CapabilityLimit(name="max_depth", value=SEMANTIC_MAX_DEPTH, unit="items"),
            CapabilityLimit(name="max_nodes", value=SEMANTIC_MAX_NODES, unit="items"),
            CapabilityLimit(name="max_edges", value=SEMANTIC_MAX_EDGES, unit="items"),
            CapabilityLimit(name="max_items", value=SEMANTIC_MAX_ITEMS, unit="items"),
            CapabilityLimit(name="max_bytes", value=SEMANTIC_MAX_BYTES, unit="utf8-bytes"),
            CapabilityLimit(name="max_time_ms", value=SEMANTIC_MAX_TIME_MS, unit="milliseconds"),
        ),
        semantic_processing_versions=(
            SemanticProcessingVersion(component="provider-contract", version="krail.semantic-operations.v1"),
            SemanticProcessingVersion(component="semantic-operations", version=SEMANTIC_PROCESSING_VERSION),
        ),
    )


def company_guidance_packet_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor.issue(
        provider="krail.local",
        capability_id=COMPANY_GUIDANCE_PACKET_CAPABILITY_ID,
        semantic_version=COMPANY_GUIDANCE_PACKET_CAPABILITY_VERSION,
        operations=(
            CapabilityOperation(
                operation_id="create_company_guidance_packet",
                input_schema=CompanyGuidancePacketCreateRequest.model_json_schema(),
                output_schema=CompanyGuidancePacket.model_json_schema(),
            ),
            CapabilityOperation(
                operation_id="read_company_guidance_packet",
                input_schema=CompanyGuidancePacketReadRequest.model_json_schema(),
                output_schema=CompanyGuidancePacketReadResult.model_json_schema(),
            ),
        ),
        effects=EffectDeclaration(),
        limits=(
            CapabilityLimit(name="max_packet_bytes", value=524_288, unit="utf8-bytes"),
            CapabilityLimit(name="max_exact_resource_refs", value=256, unit="items"),
        ),
        semantic_processing_versions=(
            SemanticProcessingVersion(component="provider-contract", version="krail.provider.v1"),
            SemanticProcessingVersion(component="company-guidance-packet", version="krail.company-guidance-packet.v1"),
        ),
    )


def capability_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        context_brief_descriptor(),
        authorized_context_packet_descriptor(),
        verification_evidence_descriptor(),
        outcome_evidence_descriptor(),
        procedure_explanation_descriptor(),
        semantic_operations_descriptor(),
        company_guidance_packet_descriptor(),
    )


class LocalCapabilityPublication:
    """Read-only publication and negotiation; it performs no authorization."""

    def descriptor(self, capability_id: str = CAPABILITY_ID) -> CapabilityDescriptor:
        descriptors = {item.capability_id: item for item in capability_descriptors()}
        try:
            return descriptors[capability_id]
        except KeyError as exc:
            raise ValueError(f"capability is not published by this provider: {capability_id}") from exc

    def negotiate(self, request: CapabilityNegotiationRequest) -> CapabilityNegotiationResult:
        try:
            descriptor = self.descriptor(request.capability_id)
        except ValueError:
            descriptor = self.descriptor()
        return negotiate(descriptor, request)


__all__ = [
    "CAPABILITY_ID",
    "CAPABILITY_VERSION",
    "OUTCOME_CAPABILITY_ID",
    "VERIFICATION_CAPABILITY_ID",
    "SEMANTIC_CAPABILITY_ID",
    "PROCEDURE_EXPLANATION_CAPABILITY_ID",
    "PROCEDURE_EXPLANATION_CAPABILITY_VERSION",
    "LocalCapabilityPublication",
    "authorized_context_packet_descriptor",
    "company_guidance_packet_descriptor",
    "capability_descriptors",
    "context_brief_descriptor",
    "outcome_evidence_descriptor",
    "procedure_explanation_descriptor",
    "semantic_operations_descriptor",
    "verification_evidence_descriptor",
]
