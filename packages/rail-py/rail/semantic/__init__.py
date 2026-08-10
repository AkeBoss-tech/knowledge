"""Evidence-backed semantic foundation for the software-change vertical.

This package owns durable semantic records and reviewable ontology proposals.
Graph traversal and public provider operations are intentionally implemented by
the later Phase 5 operations layer.
"""

from rail.semantic.induction import OntologyInductionService
from rail.semantic.models import (
    Alias,
    CandidateConcept,
    CandidateMapping,
    CandidateRelationship,
    Conflict,
    Entity,
    EntityMerge,
    Fact,
    ObservedStructure,
    OntologyMigrationProposal,
    OntologyChangeOperation,
    OntologyChangeComparison,
    OntologyChangeSet,
    OntologyPackage,
    OntologyPackageVersion,
    ReviewerQuestion,
    SemanticType,
    ValidationFinding,
)
from rail.semantic.packs import (
    PackEvaluation,
    PackMapping,
    PackSignature,
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
    "OntologyInductionService",
    "OntologyMigrationProposal",
    "OntologyChangeOperation",
    "OntologyChangeComparison",
    "OntologyChangeSet",
    "OntologyPackage",
    "OntologyPackageVersion",
    "PackEvaluation",
    "PackMapping",
    "PackSignature",
    "PostgresSemanticStore",
    "ReviewerQuestion",
    "SemanticPack",
    "SemanticPackService",
    "SemanticRepository",
    "SemanticType",
    "ValidationFinding",
]
