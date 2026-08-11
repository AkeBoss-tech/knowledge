"""Bounded, implementation-independent semantic graph read contracts.

These models deliberately describe six fixed operations rather than a query
language.  A provider descriptor advertises availability; authorization is
still evaluated for every returned record and evidence reference.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from krail.provider.v1 import ResourceRef


SEMANTIC_OPERATIONS_CONTRACT = "krail.semantic-operations.v1"
MAX_DEPTH = 8
MAX_NODES = 200
MAX_EDGES = 400
MAX_ITEMS = 100
MAX_BYTES = 262_144
MAX_TIME_MS = 10_000

NonEmpty = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]
EntityId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
OpaqueCursor = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=16, max_length=4096)
]
Digest = Annotated[
    str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")
]


class SemanticContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract: Literal["krail.semantic-operations.v1"] = SEMANTIC_OPERATIONS_CONTRACT


class SemanticSourceGrant(SemanticContractModel):
    """Requested exact evidence binding; the provider must verify it live."""

    source: ResourceRef
    source_id: NonEmpty
    classification: Literal["public", "internal", "confidential", "restricted"]


class SemanticReadScope(SemanticContractModel):
    """Exact caller-visible policy scope; providers still recheck live policy."""

    tenant_id: NonEmpty
    project_id: NonEmpty
    subject_id: NonEmpty
    allowed_authorities: tuple[NonEmpty, ...] = Field(min_length=1, max_length=64)
    allowed_resource_types: tuple[NonEmpty, ...] = Field(default=(), max_length=64)
    allowed_classifications: tuple[
        Literal["public", "internal", "confidential", "restricted"], ...
    ] = Field(min_length=1, max_length=4)
    allowed_sources: tuple[SemanticSourceGrant, ...] = Field(
        min_length=1, max_length=1024
    )
    policy_digest: Digest
    scope_digest: Digest

    @model_validator(mode="after")
    def digest_matches_scope(self) -> "SemanticReadScope":
        import hashlib, json

        body = self.model_dump(mode="json", exclude={"contract", "scope_digest"})
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        if digest != self.scope_digest:
            raise ValueError(
                "semantic scope digest does not match its authority context"
            )
        return self

    @field_validator("allowed_authorities")
    @classmethod
    def authorities_are_absolute_and_unique(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        import re

        if len(value) != len(set(value)) or any(
            re.fullmatch(r"[a-z][a-z0-9+.-]*:[^\s?#]+", item) is None for item in value
        ):
            raise ValueError("semantic authorities must be unique absolute URIs")
        return value

    @model_validator(mode="after")
    def exact_sources_fit_coarse_scope(self) -> "SemanticReadScope":
        keys = [item.source.exact_key for item in self.allowed_sources]
        if len(keys) != len(set(keys)):
            raise ValueError("semantic source grants must be unique exact references")
        authorities = set(self.allowed_authorities)
        resource_types = set(self.allowed_resource_types)
        if any(
            item.source.authority not in authorities
            or (resource_types and item.source.resource_type not in resource_types)
            or item.classification not in self.allowed_classifications
            for item in self.allowed_sources
        ):
            raise ValueError(
                "semantic source grants exceed their coarse authority scope"
            )
        return self


class OperationBudget(SemanticContractModel):
    max_depth: int = Field(default=3, ge=0, le=MAX_DEPTH)
    max_nodes: int = Field(default=100, ge=1, le=MAX_NODES)
    max_edges: int = Field(default=200, ge=1, le=MAX_EDGES)
    max_items: int = Field(default=50, ge=1, le=MAX_ITEMS)
    max_bytes: int = Field(default=131_072, ge=1024, le=MAX_BYTES)
    max_time_ms: int = Field(default=2_000, ge=1, le=MAX_TIME_MS)


GapCode = Literal[
    "not-found",
    "ambiguous",
    "missing-evidence",
    "partial-evidence",
    "stale-observation",
    "superseded-observation",
    "node-budget",
    "edge-budget",
    "item-budget",
    "byte-budget",
    "time-budget",
    "cursor-stale",
]


class OperationGap(SemanticContractModel):
    code: GapCode
    message: NonEmpty
    subject: NonEmpty | None = None


class OperationOmissions(SemanticContractModel):
    present: bool = False
    reasons: tuple[Literal["policy", "authority", "classification"], ...] = ()

    @model_validator(mode="after")
    def shape_is_consistent(self) -> "OperationOmissions":
        if self.present != bool(self.reasons) or len(self.reasons) != len(
            set(self.reasons)
        ):
            raise ValueError("semantic omission shape is inconsistent")
        return self


class OperationTrace(SemanticContractModel):
    operation: NonEmpty
    request_digest: Digest
    snapshot_digest: Digest
    processing_version: NonEmpty
    processing_digest: Digest
    pack_id: NonEmpty | None = None
    pack_version: NonEmpty | None = None
    pack_digest: Digest | None = None


class ProvenanceView(SemanticContractModel):
    evidence: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    observed_at: datetime
    processing_version: NonEmpty
    processing_digest: Digest

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value


class SemanticLineage(SemanticContractModel):
    semantic_type_id: NonEmpty
    semantic_type_revision: int = Field(ge=1)
    semantic_type_digest: Digest
    semantic_type_provenance: ProvenanceView
    pack_id: NonEmpty
    pack_version: NonEmpty
    pack_digest: Digest
    pack_revision: int = Field(ge=1)
    pack_provenance: ProvenanceView
    signature_verification_digest: Digest
    signature_verifier_digest: Digest
    trust_policy_digest: Digest


class EntityView(SemanticContractModel):
    entity_id: EntityId
    type_id: NonEmpty
    canonical_name: NonEmpty
    state: Literal["active", "merged"]
    merged_into: EntityId | None = None
    revision: int = Field(ge=1)
    provenance: ProvenanceView
    lineage: SemanticLineage


class AliasView(SemanticContractModel):
    alias_id: NonEmpty
    entity_id: EntityId
    value: NonEmpty
    normalized_value: NonEmpty
    revision: int = Field(ge=1)
    provenance: ProvenanceView
    lineage: SemanticLineage


class FactObjectView(SemanticContractModel):
    entity_id: EntityId | None = None
    literal: Any = None

    @model_validator(mode="after")
    def exactly_one_value(self) -> "FactObjectView":
        if (self.entity_id is None) == (self.literal is None):
            raise ValueError("fact object view requires exactly one entity or literal")
        return self


class FactView(SemanticContractModel):
    fact_id: NonEmpty
    subject_entity_id: EntityId
    relationship_type_id: NonEmpty
    object: FactObjectView
    assertion_kind: Literal["observation", "claim"]
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    revision: int = Field(ge=1)
    provenance: ProvenanceView
    lineage: SemanticLineage


class RelationshipHop(SemanticContractModel):
    depth: int = Field(ge=1, le=MAX_DEPTH)
    source: EntityView
    relationship: FactView
    target: EntityView


class ResolveEntityRequest(SemanticContractModel):
    scope: SemanticReadScope
    query: NonEmpty
    type_ids: tuple[NonEmpty, ...] = Field(default=(), max_length=32)
    cursor: OpaqueCursor | None = None
    budget: OperationBudget = Field(default_factory=OperationBudget)


class ResolveCandidate(SemanticContractModel):
    entity: EntityView
    match: Literal["entity-id", "canonical-name", "alias"]
    alias: AliasView | None = None


class ResolveEntityResult(SemanticContractModel):
    candidates: tuple[ResolveCandidate, ...] = Field(default=(), max_length=MAX_ITEMS)
    next_cursor: OpaqueCursor | None = None
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


class GetEntityRequest(SemanticContractModel):
    scope: SemanticReadScope
    entity_id: EntityId
    include_aliases: bool = True
    include_facts: bool = True
    cursor: OpaqueCursor | None = None
    budget: OperationBudget = Field(default_factory=OperationBudget)


class GetEntityResult(SemanticContractModel):
    entity: EntityView | None = None
    aliases: tuple[AliasView, ...] = Field(default=(), max_length=MAX_ITEMS)
    facts: tuple[FactView, ...] = Field(default=(), max_length=MAX_ITEMS)
    next_cursor: OpaqueCursor | None = None
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


class TraverseRelationshipsRequest(SemanticContractModel):
    scope: SemanticReadScope
    root_entity_id: EntityId
    relationship_type_ids: tuple[NonEmpty, ...] = Field(default=(), max_length=32)
    direction: Literal["outgoing", "incoming", "both"] = "both"
    cursor: OpaqueCursor | None = None
    budget: OperationBudget = Field(default_factory=OperationBudget)


class TraverseRelationshipsResult(SemanticContractModel):
    root: EntityView | None = None
    nodes: tuple[EntityView, ...] = Field(default=(), max_length=MAX_NODES)
    hops: tuple[RelationshipHop, ...] = Field(default=(), max_length=MAX_EDGES)
    next_cursor: OpaqueCursor | None = None
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


class CompareObservationsRequest(SemanticContractModel):
    scope: SemanticReadScope
    fact_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=32)
    stale_after_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    as_of: datetime | None = None
    budget: OperationBudget = Field(default_factory=OperationBudget)

    @field_validator("as_of")
    @classmethod
    def as_of_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("comparison as_of must include a timezone")
        return value


class ObservationDifference(SemanticContractModel):
    code: Literal["agree", "conflict", "different-scope", "corrected", "stale"]
    fact_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=32)
    message: NonEmpty


class CompareObservationsResult(SemanticContractModel):
    observations: tuple[FactView, ...] = Field(default=(), max_length=MAX_ITEMS)
    differences: tuple[ObservationDifference, ...] = Field(
        default=(), max_length=MAX_ITEMS
    )
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


class ExplainConflictRequest(SemanticContractModel):
    scope: SemanticReadScope
    conflict_id: NonEmpty
    budget: OperationBudget = Field(default_factory=OperationBudget)


class ExplainConflictResult(SemanticContractModel):
    conflict_id: NonEmpty | None = None
    state: Literal["open", "resolved"] | None = None
    reason: NonEmpty | None = None
    facts: tuple[FactView, ...] = Field(default=(), max_length=MAX_ITEMS)
    resolution_fact_id: NonEmpty | None = None
    explanation: str = ""
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


class AssembleCrossSourceEvidenceRequest(SemanticContractModel):
    scope: SemanticReadScope
    entity_ids: tuple[EntityId, ...] = Field(min_length=1, max_length=32)
    relationship_type_ids: tuple[NonEmpty, ...] = Field(default=(), max_length=32)
    decision: NonEmpty
    cursor: OpaqueCursor | None = None
    budget: OperationBudget = Field(default_factory=OperationBudget)


class EvidenceStatement(SemanticContractModel):
    fact: FactView
    citations: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    stale: bool = False
    conflicted: bool = False


class AssembleCrossSourceEvidenceResult(SemanticContractModel):
    decision: NonEmpty | None = None
    statements: tuple[EvidenceStatement, ...] = Field(default=(), max_length=MAX_ITEMS)
    citations: tuple[ResourceRef, ...] = Field(default=(), max_length=256)
    next_cursor: OpaqueCursor | None = None
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


class ListOntologyPackagesRequest(SemanticContractModel):
    scope: SemanticReadScope
    states: tuple[Literal["reviewed", "published"], ...] = ("reviewed", "published")
    cursor: OpaqueCursor | None = None
    budget: OperationBudget = Field(default_factory=OperationBudget)


class OntologyPackageView(SemanticContractModel):
    package_id: NonEmpty
    state: Literal["reviewed", "published"]
    proposed_version: NonEmpty
    published_version: NonEmpty | None = None
    change_set_id: NonEmpty
    change_set_digest: Digest
    reviewed_content_digest: Digest
    version_content_digest: Digest
    provenance: ProvenanceView
    reviewer: NonEmpty
    review_digest: Digest
    revision: int = Field(ge=1)


class SemanticPackView(SemanticContractModel):
    pack_id: NonEmpty
    version: NonEmpty
    content_digest: Digest
    type_ids: tuple[NonEmpty, ...] = Field(max_length=256)
    signature_issuer: NonEmpty
    signature_key_id: NonEmpty
    verification_digest: Digest
    provenance: ProvenanceView
    revision: int = Field(ge=1)


class ListOntologyPackagesResult(SemanticContractModel):
    packages: tuple[OntologyPackageView, ...] = Field(default=(), max_length=MAX_ITEMS)
    semantic_packs: tuple[SemanticPackView, ...] = Field(
        default=(), max_length=MAX_ITEMS
    )
    next_cursor: OpaqueCursor | None = None
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


class ListOntologyProposalHistoryRequest(SemanticContractModel):
    scope: SemanticReadScope
    package_id: NonEmpty
    cursor: OpaqueCursor | None = None
    budget: OperationBudget = Field(default_factory=OperationBudget)


class OntologyProposalHistoryView(SemanticContractModel):
    change_set_id: NonEmpty
    package_id: NonEmpty
    state: Literal["draft", "proposed", "superseded", "withdrawn"]
    change_digest: Digest
    proposed_version: NonEmpty
    proposal_content_digest: Digest
    provenance: ProvenanceView
    base_version: NonEmpty | None = None
    base_digest: Digest | None = None
    supersedes_change_set_id: NonEmpty | None = None
    revision: int = Field(ge=1)


class ListOntologyProposalHistoryResult(SemanticContractModel):
    proposals: tuple[OntologyProposalHistoryView, ...] = Field(
        default=(), max_length=MAX_ITEMS
    )
    next_cursor: OpaqueCursor | None = None
    truncated: bool = False
    gaps: tuple[OperationGap, ...] = ()
    omissions: OperationOmissions = Field(default_factory=OperationOmissions)
    trace: OperationTrace


@runtime_checkable
class SemanticOperationsProvider(Protocol):
    def resolve_entity(self, request: ResolveEntityRequest) -> ResolveEntityResult: ...
    def get_entity(self, request: GetEntityRequest) -> GetEntityResult: ...
    def traverse_relationships(
        self, request: TraverseRelationshipsRequest
    ) -> TraverseRelationshipsResult: ...
    def compare_observations(
        self, request: CompareObservationsRequest
    ) -> CompareObservationsResult: ...
    def explain_conflict(
        self, request: ExplainConflictRequest
    ) -> ExplainConflictResult: ...
    def assemble_cross_source_evidence(
        self, request: AssembleCrossSourceEvidenceRequest
    ) -> AssembleCrossSourceEvidenceResult: ...
    def list_ontology_packages(
        self, request: ListOntologyPackagesRequest
    ) -> ListOntologyPackagesResult: ...
    def list_ontology_proposal_history(
        self, request: ListOntologyProposalHistoryRequest
    ) -> ListOntologyProposalHistoryResult: ...


__all__ = [
    name for name in globals() if name.endswith(("Request", "Result", "View"))
] + [
    "SEMANTIC_OPERATIONS_CONTRACT",
    "OperationBudget",
    "OperationGap",
    "OperationOmissions",
    "OperationTrace",
    "SemanticSourceGrant",
    "SemanticReadScope",
    "SemanticLineage",
    "ResolveCandidate",
    "RelationshipHop",
    "ObservationDifference",
    "EvidenceStatement",
    "SemanticOperationsProvider",
]
