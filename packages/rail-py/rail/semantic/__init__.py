"""Evidence-backed semantic foundation for the software-change vertical.

This package owns durable semantic records and reviewable ontology proposals.
Graph traversal and public provider operations are intentionally implemented by
the later Phase 5 operations layer.
"""

from rail.semantic.induction import OntologyInductionService
from rail.semantic.models import (
    Alias,
    AliasAssignment,
    CandidateConcept,
    CandidateMapping,
    CandidateRelationship,
    Conflict,
    Entity,
    EntityMerge,
    Fact,
    ObservedStructure,
    OntologyChangeComparison,
    OntologyChangeOperation,
    OntologyChangeSet,
    OntologyMigrationOperation,
    OntologyMigrationProposal,
    OntologyPackage,
    OntologyPackageVersion,
    ReviewerQuestion,
    SemanticRevision,
    SemanticType,
    ValidationFinding,
)
from rail.semantic.packs import (
    PackEvaluation,
    PackMapping,
    PackSignature,
    PackSignatureAdmissionError,
    PackSignatureEnvelope,
    PackSignatureVerification,
    PackSignatureVerificationRequest,
    PackSignatureVerifier,
    SemanticPack,
    SemanticPackService,
)
from rail.semantic.repository import (
    JsonSemanticStore,
    MemorySemanticStore,
    PostgresSemanticStore,
    SemanticRepository,
)

__all__ = [
    "Alias",
    "AliasAssignment",
    "CandidateConcept",
    "CandidateMapping",
    "CandidateRelationship",
    "Conflict",
    "Entity",
    "EntityMerge",
    "Fact",
    "JsonSemanticStore",
    "MemorySemanticStore",
    "ObservedStructure",
    "OntologyChangeComparison",
    "OntologyChangeOperation",
    "OntologyChangeSet",
    "OntologyInductionService",
    "OntologyMigrationOperation",
    "OntologyMigrationProposal",
    "OntologyPackage",
    "OntologyPackageVersion",
    "PackEvaluation",
    "PackMapping",
    "PackSignature",
    "PackSignatureAdmissionError",
    "PackSignatureEnvelope",
    "PackSignatureVerification",
    "PackSignatureVerificationRequest",
    "PackSignatureVerifier",
    "PostgresSemanticStore",
    "ReviewerQuestion",
    "SemanticPack",
    "SemanticPackService",
    "SemanticRepository",
    "SemanticRevision",
    "SemanticType",
    "ValidationFinding",
]
