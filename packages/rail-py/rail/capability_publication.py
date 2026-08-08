"""Provider-issued publication for the bounded Context Brief vertical."""

from __future__ import annotations

from krail.provider.capabilities import (
    CapabilityDescriptor,
    CapabilityLimit,
    CapabilityNegotiationRequest,
    CapabilityNegotiationResult,
    CapabilityOperation,
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


CAPABILITY_ID = "krail.context-brief"
CAPABILITY_VERSION = "1.0.0"


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
            SemanticProcessingVersion(component="provider-contract", version="krail.provider.v1"),
            SemanticProcessingVersion(component="context-brief", version=CONTEXT_BRIEF_VERSION),
            SemanticProcessingVersion(component="ranking", version=RANKING_VERSION),
            SemanticProcessingVersion(component="freshness", version=FRESHNESS_VERSION),
            SemanticProcessingVersion(component="conflict", version=CONFLICT_VERSION),
        ),
    )


class LocalCapabilityPublication:
    """Read-only publication and negotiation; it performs no authorization."""

    def descriptor(self) -> CapabilityDescriptor:
        return context_brief_descriptor()

    def negotiate(self, request: CapabilityNegotiationRequest) -> CapabilityNegotiationResult:
        return negotiate(self.descriptor(), request)


__all__ = [
    "CAPABILITY_ID",
    "CAPABILITY_VERSION",
    "LocalCapabilityPublication",
    "context_brief_descriptor",
]
