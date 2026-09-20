"""K15-OBSERVED-CANDIDATE: observed bytes need fresh review before guidance."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from krail.provider.v1 import ResourceRef
from rail.authorized_context import (
    HostedProcedureReviewAuthorizer,
    PROCEDURE_REVIEW_CAPABILITY_ID,
    PROCEDURE_REVIEW_CAPABILITY_VERSION,
)
from rail.core_provenance import (
    CoreProvenanceRepository, CoreProvenanceService,
    ObservedProcedureCandidateService, ProcedureExplanationRequest,
    ProcedureExplanationService, ProcedureReviewService,
    create_core_provenance_receipt,
)
from rail.hosted.access import AccessClaims, AccessContextAuthority
from rail.observed_run_artifact import (
    ObservedRunArtifactRepository, RunArtifactObservation,
    record_observed_run_artifact,
)


NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def ref(kind: str, identity: str, value: bytes, *, authority: str = "opensaddle://core",
        version: str = "1") -> ResourceRef:
    return ResourceRef(authority=authority, resource_type=kind, resource_id=identity,
                       version=version, digest="sha256:" + sha256(value).hexdigest())


class CurrentAccess:
    def __init__(self):
        self.denied: set[str] = set()

    def authorize(self, resource: ResourceRef, *, at=None) -> None:
        if resource.resource_id in self.denied:
            raise PermissionError("current source access withdrawn")


class CoreArtifact:
    def __init__(self, data: bytes):
        self.data = data
        self.withdrawn = False
        self.identifies = 0
        self.observation = RunArtifactObservation(
            project_id="project-a", run_id="run_1",
            run_ref=ref("run", "run_1", b"completed Run"),
            artifact_ref=ref("artifact", "art_1", data),
            core_run_updated_at=NOW,
        )

    def identify(self, run_id: str) -> RunArtifactObservation:
        assert run_id == "run_1"
        self.identifies += 1
        if self.withdrawn:
            raise PermissionError("Core denies current artifact access")
        return self.observation

    def read_artifact(self, artifact_ref: ResourceRef, *, max_bytes: int) -> bytes:
        assert artifact_ref == self.observation.artifact_ref
        assert len(self.data) <= max_bytes
        return self.data


class CoreTrust:
    def verify(self, receipt) -> None:
        return None


class InvalidationAction:
    def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at) -> None:
        return None


def signed_review(candidate, evidence: ResourceRef, *, nonce: str):
    reviewer = ref("reviewer", "user/reviewer", b"current reviewer",
                   authority="https://review.example.test")
    exact_refs = tuple(dict.fromkeys((
        *candidate.package_refs, *candidate.command_refs, *candidate.environment_refs,
        *candidate.dependency_refs, reviewer, evidence,
    )))
    authority = AccessContextAuthority(
        {"key": b"bounded-review-secret"}, issuer=reviewer.authority,
        required_capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
    )
    claims = AccessClaims(
        issuer=reviewer.authority, tenant_id="personal", project_id="project-a",
        subject=reviewer.resource_id, delegator="user/operator",
        delegation_id="delegation/" + nonce,
        capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
        capability_version=PROCEDURE_REVIEW_CAPABILITY_VERSION,
        capability_digest="sha256:" + "a" * 64,
        actions=("procedure.review",),
        source_ids=tuple(dict.fromkeys(item.resource_id for item in exact_refs)),
        classifications=("internal",),
        policy_digest="sha256:" + "b" * 64,
        issued_at=NOW, not_before=NOW, expires_at=NOW + timedelta(hours=1),
        nonce=nonce,
    )
    action = HostedProcedureReviewAuthorizer(
        authority, authority.issue(claims, key_id="key"),
        tenant_id="personal", project_id="project-a", exact_refs=exact_refs,
        allowed_candidate_digests=(candidate.record_digest,),
        capability_digest="sha256:" + "a" * 64, clock=lambda: NOW,
    )
    return reviewer, action


def test_observed_candidate_reopens_for_signed_review_then_stales_without_losing_history(tmp_path, monkeypatch):
    path = str(tmp_path / "semantic.json")
    access = CurrentAccess()
    source = CoreArtifact(b"actual native text artifact\n")
    repository = CoreProvenanceRepository(
        path, tenant_id="personal", project_id="project-a",
        observed_source=source, observed_authorizer=access,
    )
    command = ref("command", "opensaddle/command/review", b"command")
    environment = ref("environment", "opensaddle/environment-revision/project-a",
                      b"env-revision-1")
    receipt = create_core_provenance_receipt(
        receipt_id="receipt:initial", command_ref=command, environment_ref=environment,
        observed_at=NOW,
    )
    initial = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        receipt, authorizer=access, trust=CoreTrust(),
    ).record
    first_test = ref("test", "tests/initial", b"initial review")
    reviewer, action = signed_review(initial, first_test, nonce="initial")
    first_review = ProcedureReviewService(repository=repository, clock=lambda: NOW).review(
        initial.record_digest, decision_id="review:initial", reviewer_ref=reviewer,
        evidence_refs=(first_test,), accepted=True, authorizer=access,
        review_authorizer=action,
    )
    assert first_review.promoted_record is not None

    document = ref("document", "sources/review-guide", b"source revision one")
    package = ref("package", "packages/review-extension", b"package revision one")
    evidence = record_observed_run_artifact(
        "run_1", source=source, authorizer=access, input_refs=(document, package),
    )
    observations = ObservedRunArtifactRepository(
        path, tenant_id="personal", project_id="project-a",
    )
    observations.save(evidence, source=source, authorizer=access, at=NOW)
    candidate = ObservedProcedureCandidateService(
        repository=repository, clock=lambda: NOW,
    ).propose(
        parent_decision_id="review:initial", evidence_ref=evidence.exact_ref(),
        procedure_version="2-observed", rationale="Native result suggests a revision; review required.",
    )
    assert candidate.lifecycle == "desired"
    assert candidate.review_ref is None and candidate.test_evidence_refs == ()
    assert candidate.activation_ref is None
    assert candidate.supersedes_digest == first_review.promoted_record.record_digest
    assert repository.save_observed_candidate(
        candidate, parent_decision_id="review:initial",
        evidence_ref=evidence.exact_ref(), at=NOW,
    ) == candidate
    assert len(repository.store.list(
        "personal", "project-a", kind="observed_procedure_candidate",
    )) == 1

    reopened = CoreProvenanceRepository(
        path, tenant_id="personal", project_id="project-a",
        observed_source=source, observed_authorizer=access,
    )
    assert reopened.find_procedure(candidate.record_digest) == candidate
    unconfigured = CoreProvenanceRepository(path, tenant_id="personal", project_id="project-a")
    with pytest.raises(PermissionError, match="current access is not configured"):
        unconfigured.find_procedure(candidate.record_digest)
    second_test = ref("test", "tests/observed-revision", b"independent verification")
    reviewer, signed_action = signed_review(candidate, second_test, nonce="observed")
    with pytest.raises(PermissionError, match="current access is not configured"):
        ProcedureReviewService(repository=unconfigured, clock=lambda: NOW).review(
            candidate.record_digest, decision_id="review:offline",
            reviewer_ref=reviewer, evidence_refs=(second_test,), accepted=True,
            authorizer=access, review_authorizer=signed_action,
        )
    reviewed = ProcedureReviewService(repository=reopened, clock=lambda: NOW).review(
        candidate.record_digest, decision_id="review:observed",
        reviewer_ref=reviewer, evidence_refs=(second_test,), accepted=True,
        authorizer=access, review_authorizer=signed_action,
    )
    assert reviewed.promoted_record is not None
    assert reviewed.promoted_record.lifecycle == "reviewed"
    assert reviewed.promoted_record.activation_ref is None
    request = ProcedureExplanationRequest(candidate_digest=candidate.record_digest)
    read = ProcedureExplanationService(repository=reopened, clock=lambda: NOW)
    guidance = read.actionable_guidance(request, authorizer=access)
    assert guidance is not None
    assert guidance.reviewed_digest == reviewed.promoted_record.record_digest
    with pytest.raises(PermissionError, match="current access is not configured"):
        ProcedureExplanationService(repository=unconfigured, clock=lambda: NOW).actionable_guidance(
            request, authorizer=access,
        )
    source.identifies = 0
    assert read.explain(request, authorizer=access).review_status == "accepted"
    assert source.identifies == 4, "one entry and one release recheck, not one download per review row"

    # A Core withdrawal while actual review rows are assembled must fence the
    # public list response, even though each stored review is structurally sound.
    original_list = reopened.store.list
    def withdraw_during_review_rows(tenant_id, project_id, *, kind=None):
        rows = original_list(tenant_id, project_id, kind=kind)
        if kind == reopened.review_kind:
            source.withdrawn = True
        return rows
    with monkeypatch.context() as patch:
        patch.setattr(reopened.store, "list", withdraw_during_review_rows)
        with pytest.raises(PermissionError, match="observed candidate current access denied"):
            reopened.list_reviews_for_candidate(candidate.record_digest)
    source.withdrawn = False

    for sequence, changed in enumerate((document, package, environment), start=1):
        reopened.record_invalidation(
            event_id=f"invalidation:{sequence}", changed_ref=changed,
            reason="exact source revision replaced", at=NOW,
            authorizer=InvalidationAction(),
        )
    explanation = read.explain(request, authorizer=access)
    assert explanation.support_status == "stale"
    assert len(explanation.invalidation_refs) == 3
    assert read.actionable_guidance(request, authorizer=access) is None
    assert reopened.load_review("review:observed") == reviewed
    assert reopened.find_procedure(initial.record_digest) == initial

    access.denied.add(document.resource_id)
    with pytest.raises(PermissionError):
        read.actionable_guidance(request, authorizer=access)
    access.denied.clear()
    source.withdrawn = True
    # Rebuild may preserve a stale signal without releasing protected bytes.
    rebuilt = reopened.rebuild_freshness_projection(at=NOW)
    assert any(item.procedure_digest == candidate.record_digest for item in rebuilt)
    with pytest.raises(PermissionError):
        read.explain(request, authorizer=access)
    with pytest.raises(PermissionError):
        unconfigured.load_review("review:observed")

    # Fault-injected stored lineage cannot make a reviewed observation its own
    # predecessor. Public reads reject it before recursively resolving rows.
    source.withdrawn = False
    with reopened.store.transaction():
        row = reopened.store.get(
            "personal", "project-a", "observed_procedure_candidate",
            candidate.record_digest,
        )
        assert row is not None
        reopened.store.put(row.model_copy(update={
            "revision": row.revision + 1,
            "payload": {**row.payload, "parent_decision_id": "review:observed"},
        }), expected_revision=row.revision)
    with pytest.raises(ValueError, match="predecessor is not an accepted Core receipt"):
        read.explain(request, authorizer=access)


def test_observed_candidate_storage_rejects_fabricated_parent_or_evidence(tmp_path):
    path = str(tmp_path / "semantic.json")
    access = CurrentAccess()
    source = CoreArtifact(b"opaque result")
    repository = CoreProvenanceRepository(
        path, tenant_id="personal", project_id="project-a",
        observed_source=source, observed_authorizer=access,
    )
    evidence = record_observed_run_artifact("run_1", source=source, authorizer=access)
    ObservedRunArtifactRepository(
        path, tenant_id="personal", project_id="project-a",
    ).save(evidence, source=source, authorizer=access, at=NOW)
    with pytest.raises(ValueError, match="predecessor"):
        ObservedProcedureCandidateService(repository=repository, clock=lambda: NOW).propose(
            parent_decision_id="nonexistent", evidence_ref=evidence.exact_ref(),
            procedure_version="2", rationale="Unreviewed observation is insufficient.",
        )
    assert repository.store.list(
        "personal", "project-a", kind="observed_procedure_candidate",
    ) == []
