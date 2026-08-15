"""Strict semantic and ontology-induction domain records."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any, Literal

import rfc8785
from krail.provider.v1 import ResourceRef
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmpty = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
Digest = Annotated[
    str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")
]
Token = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, pattern=r"^[a-z][a-z0-9._-]{0,127}$"
    ),
]
EntityId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=512,
        pattern=r"^[a-z][a-z0-9+.-]*://[^\s/]+/.+$",
    ),
]
ScalarString = Annotated[str, StringConstraints(max_length=4096)]
ScalarValue = ScalarString | int | FiniteFloat | bool | None


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScopedModel(StrictModel):
    tenant_id: NonEmpty
    project_id: NonEmpty


class EvidenceProvenance(StrictModel):
    evidence: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    processing_version: NonEmpty
    processing_digest: Digest
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("semantic timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def evidence_is_unique(self) -> EvidenceProvenance:
        identities = [
            (
                item.authority,
                item.resource_type,
                item.resource_id,
                item.version,
                item.digest,
            )
            for item in self.evidence
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("evidence references must be unique and exact")
        return self


class SemanticType(ScopedModel):
    schema_version: Literal["krail.semantic-type.v1"] = "krail.semantic-type.v1"
    type_id: Token
    type_kind: Literal["entity", "relationship"]
    label: NonEmpty
    description: NonEmpty
    pack_id: Token
    pack_version: NonEmpty
    provenance: EvidenceProvenance
    revision: int = Field(ge=1)


class Entity(ScopedModel):
    schema_version: Literal["krail.semantic-entity.v1"] = "krail.semantic-entity.v1"
    entity_id: EntityId
    type_id: Token
    canonical_name: NonEmpty
    state: Literal["active", "merged"] = "active"
    merged_into: EntityId | None = None
    provenance: EvidenceProvenance
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def merge_state_is_consistent(self) -> Entity:
        if (self.state == "merged") != (self.merged_into is not None):
            raise ValueError("merged entities require exactly one merge target")
        if self.merged_into == self.entity_id:
            raise ValueError("an entity cannot merge into itself")
        return self


class FactObject(StrictModel):
    entity_id: EntityId | None = None
    literal: ScalarValue = None

    @model_validator(mode="after")
    def exactly_one_value(self) -> FactObject:
        if (self.entity_id is None) == (self.literal is None):
            raise ValueError("fact object requires exactly one entity or literal")
        return self


class Fact(ScopedModel):
    schema_version: Literal["krail.semantic-fact.v1"] = "krail.semantic-fact.v1"
    fact_id: NonEmpty
    subject_entity_id: EntityId
    relationship_type_id: Token
    object: FactObject
    assertion_kind: Literal["observation", "claim"]
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    provenance: EvidenceProvenance
    revision: int = Field(ge=1)

    @field_validator("valid_from", "valid_until")
    @classmethod
    def valid_time_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("fact valid time must include a timezone")
        return value

    @model_validator(mode="after")
    def valid_interval_is_ordered(self) -> Fact:
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self


class Alias(ScopedModel):
    schema_version: Literal["krail.semantic-alias.v1"] = "krail.semantic-alias.v1"
    alias_id: NonEmpty
    entity_id: EntityId
    value: NonEmpty
    normalized_value: NonEmpty
    provenance: EvidenceProvenance
    revision: int = Field(ge=1)


class AliasAssignment(ScopedModel):
    schema_version: Literal["krail.semantic-alias-assignment.v1"] = (
        "krail.semantic-alias-assignment.v1"
    )
    assignment_id: NonEmpty
    alias_id: NonEmpty
    from_entity_id: EntityId
    to_entity_id: EntityId
    state: Literal["applied", "reversed"] = "applied"
    provenance: EvidenceProvenance
    applied_at: datetime
    reversed_at: datetime | None = None
    revision: int = Field(ge=1)

    @field_validator("applied_at", "reversed_at")
    @classmethod
    def assignment_time_requires_timezone(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("alias assignment timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def assignment_is_consistent(self) -> AliasAssignment:
        if self.from_entity_id == self.to_entity_id:
            raise ValueError("alias reassignment entities must differ")
        if (self.state == "reversed") != (self.reversed_at is not None):
            raise ValueError("reversed alias assignments require reversed_at")
        return self


class SemanticRevision(ScopedModel):
    schema_version: Literal["krail.semantic-revision.v1"] = "krail.semantic-revision.v1"
    revision_id: NonEmpty
    source_kind: NonEmpty
    source_record_id: NonEmpty
    source_revision: int = Field(ge=1)
    payload_digest: Digest
    payload: dict[str, Any]
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def revision_time_requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("semantic revision timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def revision_digest_matches(self) -> SemanticRevision:
        if self.payload_digest != canonical_digest(self.payload):
            raise ValueError("semantic revision digest does not match payload")
        return self


class Conflict(ScopedModel):
    schema_version: Literal["krail.semantic-conflict.v1"] = "krail.semantic-conflict.v1"
    conflict_id: NonEmpty
    fact_ids: tuple[NonEmpty, ...] = Field(min_length=2, max_length=32)
    reason: Literal[
        "contradictory-value", "overlapping-valid-time", "identity-ambiguity", "other"
    ]
    state: Literal["open", "resolved"] = "open"
    resolution_fact_id: NonEmpty | None = None
    provenance: EvidenceProvenance
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def resolution_is_consistent(self) -> Conflict:
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("conflict fact IDs must be unique")
        if (self.state == "resolved") != (self.resolution_fact_id is not None):
            raise ValueError("resolved conflicts require a resolution fact")
        return self


class EntityMerge(ScopedModel):
    schema_version: Literal["krail.semantic-entity-merge.v1"] = (
        "krail.semantic-entity-merge.v1"
    )
    merge_id: NonEmpty
    source_entity_id: EntityId
    target_entity_id: EntityId
    source_revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)
    state: Literal["applied", "reversed"] = "applied"
    provenance: EvidenceProvenance
    applied_at: datetime
    reversed_at: datetime | None = None
    revision: int = Field(ge=1)

    @field_validator("applied_at", "reversed_at")
    @classmethod
    def merge_time_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("merge timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def merge_is_consistent(self) -> EntityMerge:
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("merge source and target must differ")
        if (self.state == "reversed") != (self.reversed_at is not None):
            raise ValueError("reversed merges require reversed_at")
        return self


class ObservedStructure(ScopedModel):
    schema_version: Literal["krail.observed-structure.v1"] = (
        "krail.observed-structure.v1"
    )
    observation_id: NonEmpty
    source: ResourceRef
    structure_kind: Literal["record", "field", "edge", "value-pattern"]
    path: NonEmpty
    sample_digest: Digest
    occurrence_count: int = Field(ge=1)
    provenance: EvidenceProvenance

    @model_validator(mode="after")
    def source_is_cited_by_provenance(self) -> ObservedStructure:
        identity = (
            self.source.authority,
            self.source.resource_type,
            self.source.resource_id,
            self.source.version,
            self.source.digest,
        )
        cited = {
            (
                item.authority,
                item.resource_type,
                item.resource_id,
                item.version,
                item.digest,
            )
            for item in self.provenance.evidence
        }
        if identity not in cited:
            raise ValueError(
                "observed structure source must be cited by exact provenance"
            )
        return self


class CandidateConcept(StrictModel):
    candidate_id: NonEmpty
    type_id: Token
    label: NonEmpty
    description: NonEmpty
    observation_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=256)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def observation_ids_are_unique(self) -> CandidateConcept:
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("candidate observation IDs must be unique")
        return self


class CandidateRelationship(StrictModel):
    candidate_id: NonEmpty
    relationship_type_id: Token
    source_type_id: Token
    target_type_id: Token
    observation_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=256)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def observation_ids_are_unique(self) -> CandidateRelationship:
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("candidate observation IDs must be unique")
        return self


class CandidateMapping(StrictModel):
    candidate_id: NonEmpty
    source_path: NonEmpty
    target_type_id: Token
    target_field: Token
    transform: Literal["identity", "normalize-text", "parse-timestamp", "resource-ref"]
    observation_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def observation_ids_are_unique(self) -> CandidateMapping:
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("candidate observation IDs must be unique")
        return self


class ValidationFinding(StrictModel):
    finding_id: NonEmpty
    severity: Literal["info", "warning", "blocking"]
    code: Token
    message: NonEmpty
    candidate_ids: tuple[NonEmpty, ...] = ()
    evidence: tuple[ResourceRef, ...] = ()

    @model_validator(mode="after")
    def finding_is_grounded(self) -> ValidationFinding:
        if not self.candidate_ids and not self.evidence:
            raise ValueError("validation findings require candidates or exact evidence")
        return self


class ReviewerQuestion(StrictModel):
    question_id: NonEmpty
    prompt: NonEmpty
    candidate_ids: tuple[NonEmpty, ...] = ()
    evidence: tuple[ResourceRef, ...] = ()
    state: Literal["open", "answered"] = "open"
    answer: NonEmpty | None = None

    @model_validator(mode="after")
    def answer_is_consistent(self) -> ReviewerQuestion:
        if (self.state == "answered") != (self.answer is not None):
            raise ValueError("answered reviewer questions require an answer")
        if not self.candidate_ids and not self.evidence:
            raise ValueError("reviewer questions require candidates or exact evidence")
        return self


class OntologyMigrationOperation(StrictModel):
    operation_id: NonEmpty
    operation: Literal["add", "update", "deprecate", "restore"]
    target_kind: Literal["concept", "relationship", "mapping"]
    target_id: NonEmpty
    before_digest: Digest | None = None
    after: dict[Token, ScalarValue] | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def operation_shape_is_valid(self) -> OntologyMigrationOperation:
        if self.operation in {"add", "update", "restore"} and self.after is None:
            raise ValueError("add, update, and restore operations require after state")
        if self.operation == "add" and self.before_digest is not None:
            raise ValueError("add operations cannot bind prior state")
        if (
            self.operation in {"update", "deprecate", "restore"}
            and self.before_digest is None
        ):
            raise ValueError("non-add operations require an exact before digest")
        return self


class OntologyMigrationProposal(StrictModel):
    migration_id: NonEmpty
    from_version: NonEmpty
    to_version: NonEmpty
    operations: tuple[OntologyMigrationOperation, ...] = Field(
        min_length=1, max_length=256
    )
    reversible: bool
    rollback_operations: tuple[OntologyMigrationOperation, ...] = ()
    proposal_digest: Digest

    @model_validator(mode="after")
    def reversible_has_rollback(self) -> OntologyMigrationProposal:
        if self.from_version == self.to_version:
            raise ValueError("migration proposal versions must differ")
        for label, operations in (
            ("migration", self.operations),
            ("rollback", self.rollback_operations),
        ):
            operation_ids = [item.operation_id for item in operations]
            if len(operation_ids) != len(set(operation_ids)):
                raise ValueError(f"{label} operation IDs must be unique")
        if self.reversible and not self.rollback_operations:
            raise ValueError(
                "reversible migration proposals require rollback operations"
            )
        if len(rfc8785.dumps(self.model_dump(mode="json"))) > 524_288:
            raise ValueError("migration proposal exceeds the bounded payload size")
        calculated = canonical_digest(
            self.model_dump(mode="json", exclude={"proposal_digest"})
        )
        if self.proposal_digest != calculated:
            raise ValueError("migration proposal digest does not match content")
        return self


class DraftAuthorship(StrictModel):
    actor_id: NonEmpty
    actor_kind: Literal["human", "agent"]
    delegation_id: NonEmpty | None = None
    capability_digest: Digest | None = None
    policy_digest: Digest

    @model_validator(mode="after")
    def agents_require_delegation(self) -> DraftAuthorship:
        if self.actor_kind == "agent" and (
            self.delegation_id is None or self.capability_digest is None
        ):
            raise ValueError("agent-authored drafts require delegated capability")
        return self


class OntologyChangeOperation(StrictModel):
    operation_id: NonEmpty
    operation: Literal["add", "update", "deprecate", "restore"]
    target_kind: Literal["concept", "relationship", "mapping"]
    target_id: NonEmpty
    before_digest: Digest | None = None
    after: dict[Token, ScalarValue] | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def operation_shape_is_valid(self) -> OntologyChangeOperation:
        if self.operation in {"add", "update", "restore"} and self.after is None:
            raise ValueError("add, update, and restore operations require after state")
        if self.operation == "add" and self.before_digest is not None:
            raise ValueError("add operations cannot bind prior state")
        if (
            self.operation in {"update", "deprecate", "restore"}
            and self.before_digest is None
        ):
            raise ValueError("non-add operations require an exact before digest")
        return self


class OntologyChangeSet(ScopedModel):
    schema_version: Literal["krail.ontology-change-set.v1"] = (
        "krail.ontology-change-set.v1"
    )
    change_set_id: NonEmpty
    package_id: Token
    base_version: NonEmpty | None = None
    base_digest: Digest | None = None
    operations: tuple[OntologyChangeOperation, ...] = Field(
        min_length=1, max_length=512
    )
    authorship: DraftAuthorship
    state: Literal["draft", "proposed", "superseded", "withdrawn"] = "draft"
    supersedes_change_set_id: NonEmpty | None = None
    rollback_of_version: NonEmpty | None = None
    change_digest: Digest
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def change_time_requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("change-set timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def change_set_is_bound(self) -> OntologyChangeSet:
        if (self.base_version is None) != (self.base_digest is None):
            raise ValueError("base version and digest must be supplied together")
        if len({item.operation_id for item in self.operations}) != len(self.operations):
            raise ValueError("change operation IDs must be unique")
        if len(rfc8785.dumps(self.model_dump(mode="json"))) > 1_048_576:
            raise ValueError("ontology change set exceeds the bounded payload size")
        calculated = canonical_digest(
            self.model_dump(
                mode="json",
                exclude={"change_digest", "state", "updated_at", "revision"},
            )
        )
        if self.change_digest != calculated:
            raise ValueError("ontology change-set digest does not match content")
        return self


class OntologyChangeComparison(StrictModel):
    left_change_set_id: NonEmpty
    right_change_set_id: NonEmpty
    added_operation_ids: tuple[NonEmpty, ...] = ()
    removed_operation_ids: tuple[NonEmpty, ...] = ()
    changed_operation_ids: tuple[NonEmpty, ...] = ()
    comparison_digest: Digest

    @model_validator(mode="after")
    def comparison_digest_matches(self) -> OntologyChangeComparison:
        calculated = canonical_digest(
            self.model_dump(mode="json", exclude={"comparison_digest"})
        )
        if self.comparison_digest != calculated:
            raise ValueError("ontology comparison digest does not match content")
        return self


class OntologyPackageVersion(ScopedModel):
    schema_version: Literal["krail.ontology-package-version.v1"] = (
        "krail.ontology-package-version.v1"
    )
    package_id: Token
    version: NonEmpty
    observations: tuple[ObservedStructure, ...] = Field(min_length=1, max_length=1024)
    concepts: tuple[CandidateConcept, ...] = ()
    relationships: tuple[CandidateRelationship, ...] = ()
    mappings: tuple[CandidateMapping, ...] = ()
    findings: tuple[ValidationFinding, ...] = ()
    reviewer_questions: tuple[ReviewerQuestion, ...] = ()
    migration_proposals: tuple[OntologyMigrationProposal, ...] = ()
    change_set_digest: Digest
    provenance: EvidenceProvenance
    content_digest: Digest

    def calculated_digest(self) -> str:
        return canonical_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )

    @model_validator(mode="after")
    def digest_matches_content(self) -> OntologyPackageVersion:
        groups = (
            ("observation", self.observations, "observation_id"),
            ("finding", self.findings, "finding_id"),
            ("reviewer question", self.reviewer_questions, "question_id"),
            ("migration proposal", self.migration_proposals, "migration_id"),
        )
        for label, items, attribute in groups:
            identifiers = [getattr(item, attribute) for item in items]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} IDs must be unique")
        candidates = (*self.concepts, *self.relationships, *self.mappings)
        candidate_ids = [item.candidate_id for item in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be globally unique")
        observed_ids = {item.observation_id for item in self.observations}
        if any(
            observation_id not in observed_ids
            for candidate in candidates
            for observation_id in candidate.observation_ids
        ):
            raise ValueError("ontology candidates reference unknown observations")
        if any(
            candidate_id not in set(candidate_ids)
            for review_record in (*self.findings, *self.reviewer_questions)
            for candidate_id in review_record.candidate_ids
        ):
            raise ValueError("review records reference unknown candidates")
        concept_type_ids = {item.type_id for item in self.concepts}
        if any(
            relationship.source_type_id not in concept_type_ids
            or relationship.target_type_id not in concept_type_ids
            for relationship in self.relationships
        ):
            raise ValueError("candidate relationships require proposed concept types")
        if any(
            mapping.target_type_id not in concept_type_ids for mapping in self.mappings
        ):
            raise ValueError("candidate mappings require a proposed concept type")
        if self.content_digest != self.calculated_digest():
            raise ValueError("ontology package version digest does not match content")
        return self


class OntologyPackage(ScopedModel):
    schema_version: Literal["krail.ontology-package.v1"] = "krail.ontology-package.v1"
    package_id: Token
    state: Literal["proposal", "in-review", "reviewed", "published", "rejected"]
    proposed_version: NonEmpty
    change_set_id: NonEmpty
    change_set_digest: Digest
    base_version: NonEmpty | None = None
    base_digest: Digest | None = None
    authorship: DraftAuthorship
    published_version: NonEmpty | None = None
    reviewer: NonEmpty | None = None
    review_digest: Digest | None = None
    reviewed_content_digest: Digest | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def package_time_requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("package timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> OntologyPackage:
        reviewed = self.state in {"reviewed", "published"}
        if reviewed and (
            self.reviewer is None
            or self.review_digest is None
            or self.reviewed_content_digest is None
        ):
            raise ValueError(
                "reviewed packages require reviewer, review, and content digests"
            )
        if not reviewed and self.reviewed_content_digest is not None:
            raise ValueError("only reviewed packages bind reviewed content")
        if (
            self.state == "published"
            and self.published_version != self.proposed_version
        ):
            raise ValueError(
                "published package must bind the reviewed proposed version"
            )
        if self.state != "published" and self.published_version is not None:
            raise ValueError("only published packages have a published version")
        return self
