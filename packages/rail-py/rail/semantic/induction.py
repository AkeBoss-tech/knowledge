"""Governed ontology induction: propose, review, then explicitly publish."""

from __future__ import annotations

from datetime import datetime

from rail.hosted.repository import ConcurrencyConflict
from rail.semantic.models import (
    CandidateConcept,
    CandidateMapping,
    CandidateRelationship,
    DraftAuthorship,
    EvidenceProvenance,
    ObservedStructure,
    OntologyChangeComparison,
    OntologyChangeOperation,
    OntologyChangeSet,
    OntologyMigrationProposal,
    OntologyPackage,
    OntologyPackageVersion,
    ReviewerQuestion,
    ValidationFinding,
    canonical_digest,
)
from rail.semantic.repository import SemanticRepository


class OntologyInductionService:
    """Persists proposals without inferring authorization to publish or migrate."""

    def __init__(self, repository: SemanticRepository) -> None:
        self.repository = repository

    def build_version(
        self,
        *,
        package_id: str,
        version: str,
        observations: tuple[ObservedStructure, ...],
        concepts: tuple[CandidateConcept, ...] = (),
        relationships: tuple[CandidateRelationship, ...] = (),
        mappings: tuple[CandidateMapping, ...] = (),
        findings: tuple[ValidationFinding, ...] = (),
        reviewer_questions: tuple[ReviewerQuestion, ...] = (),
        migration_proposals: tuple[OntologyMigrationProposal, ...] = (),
        change_set: OntologyChangeSet,
        provenance: EvidenceProvenance,
    ) -> OntologyPackageVersion:
        groups = (
            ("observations", observations, "observation_id"),
            ("concepts", concepts, "candidate_id"),
            ("relationships", relationships, "candidate_id"),
            ("mappings", mappings, "candidate_id"),
            ("findings", findings, "finding_id"),
            ("reviewer questions", reviewer_questions, "question_id"),
            ("migration proposals", migration_proposals, "migration_id"),
        )
        for label, items, attribute in groups:
            identifiers = [getattr(item, attribute) for item in items]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} IDs must be unique")
        observed_ids = {item.observation_id for item in observations}
        referenced = {
            observation_id
            for candidate in (*concepts, *relationships, *mappings)
            for observation_id in candidate.observation_ids
        }
        if not referenced.issubset(observed_ids):
            raise ValueError("ontology candidates reference unknown observations")
        candidate_ids = {
            item.candidate_id for item in (*concepts, *relationships, *mappings)
        }
        candidate_count = len(concepts) + len(relationships) + len(mappings)
        if len(candidate_ids) != candidate_count:
            raise ValueError("candidate IDs must be globally unique")
        review_references = {
            candidate_id
            for item in (*findings, *reviewer_questions)
            for candidate_id in item.candidate_ids
        }
        if not review_references.issubset(candidate_ids):
            raise ValueError("review records reference unknown candidates")
        concept_type_ids = {item.type_id for item in concepts}
        if any(
            item.source_type_id not in concept_type_ids
            or item.target_type_id not in concept_type_ids
            for item in relationships
        ):
            raise ValueError("candidate relationships require proposed concept types")
        if any(item.target_type_id not in concept_type_ids for item in mappings):
            raise ValueError("candidate mappings require a proposed concept type")
        if any(
            item.tenant_id != self.repository.tenant_id
            or item.project_id != self.repository.project_id
            for item in observations
        ):
            raise ValueError("observations belong to a different scope")
        values = {
            "tenant_id": self.repository.tenant_id,
            "project_id": self.repository.project_id,
            "package_id": package_id,
            "version": version,
            "observations": observations,
            "concepts": concepts,
            "relationships": relationships,
            "mappings": mappings,
            "findings": findings,
            "reviewer_questions": reviewer_questions,
            "migration_proposals": migration_proposals,
            "change_set_digest": change_set.change_digest,
            "provenance": provenance,
        }
        digest = canonical_digest(
            OntologyPackageVersion.model_construct(
                **values, content_digest="sha256:" + "0" * 64
            ).model_dump(mode="json", exclude={"content_digest"})
        )
        return OntologyPackageVersion(**values, content_digest=digest)

    def build_change_set(
        self,
        *,
        change_set_id: str,
        package_id: str,
        operations: tuple[OntologyChangeOperation, ...],
        authorship: DraftAuthorship,
        created_at: datetime,
        base_version: str | None = None,
        base_digest: str | None = None,
        supersedes_change_set_id: str | None = None,
        rollback_of_version: str | None = None,
    ) -> OntologyChangeSet:
        values = {
            "tenant_id": self.repository.tenant_id,
            "project_id": self.repository.project_id,
            "change_set_id": change_set_id,
            "package_id": package_id,
            "base_version": base_version,
            "base_digest": base_digest,
            "operations": operations,
            "authorship": authorship,
            "state": "draft",
            "supersedes_change_set_id": supersedes_change_set_id,
            "rollback_of_version": rollback_of_version,
            "created_at": created_at,
            "updated_at": created_at,
            "revision": 1,
        }
        digest = canonical_digest(
            OntologyChangeSet.model_construct(
                **values, change_digest="sha256:" + "0" * 64
            ).model_dump(
                mode="json",
                exclude={"change_digest", "state", "updated_at", "revision"},
            )
        )
        return OntologyChangeSet(**values, change_digest=digest)

    @staticmethod
    def compare(
        left: OntologyChangeSet, right: OntologyChangeSet
    ) -> OntologyChangeComparison:
        left_by_id = {item.operation_id: item for item in left.operations}
        right_by_id = {item.operation_id: item for item in right.operations}
        added = tuple(sorted(set(right_by_id) - set(left_by_id)))
        removed = tuple(sorted(set(left_by_id) - set(right_by_id)))
        changed = tuple(
            sorted(
                operation_id
                for operation_id in set(left_by_id) & set(right_by_id)
                if left_by_id[operation_id] != right_by_id[operation_id]
            )
        )
        body = {
            "left_change_set_id": left.change_set_id,
            "right_change_set_id": right.change_set_id,
            "added_operation_ids": added,
            "removed_operation_ids": removed,
            "changed_operation_ids": changed,
        }
        return OntologyChangeComparison(
            **body, comparison_digest=canonical_digest(body)
        )

    def propose(
        self,
        version: OntologyPackageVersion,
        change_set: OntologyChangeSet,
        *,
        proposed_at: datetime,
    ) -> OntologyPackage:
        if change_set.state != "draft":
            raise ConcurrencyConflict("only a draft change set can be proposed")
        if (
            change_set.package_id != version.package_id
            or change_set.change_digest != version.change_set_digest
        ):
            raise ValueError("package version must bind the exact change set")
        with self.repository.store.transaction():
            stored_change_set = self.repository.store.get(
                self.repository.tenant_id,
                self.repository.project_id,
                "ontology_change_set",
                change_set.change_set_id,
            )
            stored_package = self.repository.store.get(
                self.repository.tenant_id,
                self.repository.project_id,
                "ontology_package",
                version.package_id,
            )
        if stored_change_set is not None:
            persisted_change_set = OntologyChangeSet.model_validate(
                stored_change_set.payload
            )
            if persisted_change_set != change_set:
                raise ConcurrencyConflict(
                    "persisted ontology draft differs from caller"
                )
        else:
            persisted_change_set = change_set
        expected_change_revision = (
            stored_change_set.revision if stored_change_set else change_set.revision
        )
        proposed_change_set = OntologyChangeSet.model_validate(
            {
                **persisted_change_set.model_dump(mode="python"),
                "state": "proposed",
                "updated_at": proposed_at,
                "revision": expected_change_revision + 1,
            }
        )
        current_package = (
            OntologyPackage.model_validate(stored_package.payload)
            if stored_package is not None
            else None
        )
        if current_package is not None and current_package.state not in {
            "published",
            "rejected",
        }:
            raise ConcurrencyConflict("ontology package already has an active proposal")
        if current_package is not None and current_package.state == "published":
            prior_version = self._version(current_package)
            if (
                change_set.base_version != current_package.published_version
                or change_set.base_digest != prior_version.content_digest
            ):
                raise ConcurrencyConflict(
                    "new proposal must bind the published package head"
                )
        expected_package_revision = stored_package.revision if stored_package else 0
        package = OntologyPackage(
            tenant_id=self.repository.tenant_id,
            project_id=self.repository.project_id,
            package_id=version.package_id,
            state="proposal",
            proposed_version=version.version,
            change_set_id=change_set.change_set_id,
            change_set_digest=change_set.change_digest,
            base_version=change_set.base_version,
            base_digest=change_set.base_digest,
            authorship=change_set.authorship,
            created_at=current_package.created_at if current_package else proposed_at,
            updated_at=proposed_at,
            revision=expected_package_revision + 1,
        )
        change_items = (
            (
                (
                    "ontology_change_set",
                    change_set.change_set_id,
                    change_set,
                    0,
                ),
            )
            if stored_change_set is None
            else ()
        )
        self.repository._save_many(
            change_items
            + (
                (
                    "ontology_change_set",
                    change_set.change_set_id,
                    proposed_change_set,
                    expected_change_revision,
                ),
                (
                    "ontology_package_version",
                    f"{version.package_id}@{version.version}",
                    version,
                    0,
                ),
                (
                    "ontology_package",
                    package.package_id,
                    package,
                    expected_package_revision,
                ),
            ),
            at=proposed_at,
        )
        return package

    def rebase_draft(
        self,
        current: OntologyChangeSet,
        *,
        new_change_set_id: str,
        new_base_version: str,
        new_base_digest: str,
        at: datetime,
    ) -> OntologyChangeSet:
        if current.state != "draft":
            raise ConcurrencyConflict("only drafts can be rebased")
        rebased = self.build_change_set(
            change_set_id=new_change_set_id,
            package_id=current.package_id,
            base_version=new_base_version,
            base_digest=new_base_digest,
            operations=current.operations,
            authorship=current.authorship,
            supersedes_change_set_id=current.change_set_id,
            rollback_of_version=current.rollback_of_version,
            created_at=at,
        )
        with self.repository.store.transaction():
            stored_current = self.repository.store.get(
                self.repository.tenant_id,
                self.repository.project_id,
                "ontology_change_set",
                current.change_set_id,
            )
        if stored_current is None:
            persisted_current = current
            expected_current_revision = current.revision
        else:
            persisted_current = OntologyChangeSet.model_validate(stored_current.payload)
            if persisted_current != current:
                raise ConcurrencyConflict(
                    "persisted ontology draft differs from caller"
                )
            expected_current_revision = stored_current.revision
        superseded = OntologyChangeSet.model_validate(
            {
                **persisted_current.model_dump(mode="python"),
                "state": "superseded",
                "updated_at": at,
                "revision": expected_current_revision + 1,
            }
        )
        current_items = (
            (("ontology_change_set", current.change_set_id, current, 0),)
            if stored_current is None
            else ()
        )
        self.repository._save_many(
            current_items
            + (
                (
                    "ontology_change_set",
                    current.change_set_id,
                    superseded,
                    expected_current_revision,
                ),
                ("ontology_change_set", rebased.change_set_id, rebased, 0),
            ),
            at=at,
        )
        return rebased

    def build_rollback_draft(
        self,
        *,
        change_set_id: str,
        package_id: str,
        published_version: str,
        published_digest: str,
        inverse_operations: tuple[OntologyChangeOperation, ...],
        authorship: DraftAuthorship,
        created_at: datetime,
    ) -> OntologyChangeSet:
        """Create a proposal to reverse a published version; execute nothing."""
        return self.build_change_set(
            change_set_id=change_set_id,
            package_id=package_id,
            base_version=published_version,
            base_digest=published_digest,
            operations=inverse_operations,
            authorship=authorship,
            rollback_of_version=published_version,
            created_at=created_at,
        )

    def _package(self, package_id: str) -> OntologyPackage:
        with self.repository.store.transaction():
            row = self.repository.store.get(
                self.repository.tenant_id,
                self.repository.project_id,
                "ontology_package",
                package_id,
            )
        if row is None:
            raise KeyError(package_id)
        return OntologyPackage.model_validate(row.payload)

    def _version(self, package: OntologyPackage) -> OntologyPackageVersion:
        record_id = f"{package.package_id}@{package.proposed_version}"
        with self.repository.store.transaction():
            row = self.repository.store.get(
                self.repository.tenant_id,
                self.repository.project_id,
                "ontology_package_version",
                record_id,
            )
        if row is None:
            raise KeyError(record_id)
        return OntologyPackageVersion.model_validate(row.payload)

    @staticmethod
    def review_decision_digest(
        package: OntologyPackage,
        version: OntologyPackageVersion,
        *,
        reviewer: str,
        accepted: bool,
    ) -> str:
        """Bind a review attestation to the exact proposed semantic content."""
        return canonical_digest(
            {
                "schema_version": "krail.ontology-review-decision.v1",
                "package_id": package.package_id,
                "proposed_version": package.proposed_version,
                "change_set_digest": package.change_set_digest,
                "content_digest": version.content_digest,
                "reviewer": reviewer,
                "decision": "accept" if accepted else "reject",
            }
        )

    def begin_review(self, package_id: str, *, at: datetime) -> OntologyPackage:
        current = self._package(package_id)
        if current.state != "proposal":
            raise ConcurrencyConflict("only proposals can enter review")
        updated = OntologyPackage.model_validate(
            {
                **current.model_dump(mode="python"),
                "state": "in-review",
                "updated_at": at,
                "revision": current.revision + 1,
            }
        )
        return self.repository._save(
            "ontology_package",
            package_id,
            updated,
            expected_revision=current.revision,
            at=at,
        )

    def review(
        self,
        package_id: str,
        *,
        reviewer: str,
        review_digest: str,
        accepted: bool,
        at: datetime,
    ) -> OntologyPackage:
        current = self._package(package_id)
        version = self._version(current)
        if current.state != "in-review":
            raise ConcurrencyConflict("package is not in review")
        if accepted and any(item.severity == "blocking" for item in version.findings):
            raise ValueError("blocking validation findings prevent acceptance")
        if accepted and any(
            item.state == "open" for item in version.reviewer_questions
        ):
            raise ValueError("open reviewer questions prevent acceptance")
        expected_review_digest = self.review_decision_digest(
            current, version, reviewer=reviewer, accepted=accepted
        )
        if review_digest != expected_review_digest:
            raise ValueError("review digest does not bind the exact review decision")
        updated = OntologyPackage.model_validate(
            {
                **current.model_dump(mode="python"),
                "state": "reviewed" if accepted else "rejected",
                "reviewer": reviewer,
                "review_digest": review_digest,
                "reviewed_content_digest": version.content_digest if accepted else None,
                "updated_at": at,
                "revision": current.revision + 1,
            }
        )
        return self.repository._save(
            "ontology_package",
            package_id,
            updated,
            expected_revision=current.revision,
            at=at,
        )

    def publish(
        self,
        package_id: str,
        *,
        reviewed_content_digest: str,
        at: datetime,
    ) -> OntologyPackage:
        current = self._package(package_id)
        version = self._version(current)
        if current.state != "reviewed":
            raise ConcurrencyConflict("only reviewed ontology packages can publish")
        if reviewed_content_digest != version.content_digest:
            raise ConcurrencyConflict("reviewed ontology digest is stale")
        if current.reviewed_content_digest != version.content_digest:
            raise ConcurrencyConflict("durable review does not bind ontology content")
        published = OntologyPackage.model_validate(
            {
                **current.model_dump(mode="python"),
                "state": "published",
                "published_version": current.proposed_version,
                "updated_at": at,
                "revision": current.revision + 1,
            }
        )
        return self.repository._save(
            "ontology_package",
            package_id,
            published,
            expected_revision=current.revision,
            at=at,
        )
