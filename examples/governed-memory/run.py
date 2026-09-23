"""Offline observed-candidate lifecycle; all authority and Core data are fixtures."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from krail.provider.v1 import ResourceRef
from rail.authorized_context import (
    HostedProcedureReviewAuthorizer,
    PROCEDURE_REVIEW_CAPABILITY_ID,
    PROCEDURE_REVIEW_CAPABILITY_VERSION,
)
from rail.core_provenance import (
    CoreProvenanceRepository,
    CoreProvenanceService,
    ObservedProcedureCandidateService,
    ProcedureExplanationRequest,
    ProcedureExplanationService,
    ProcedureReviewService,
    create_core_provenance_receipt,
)
from rail.hosted.access import AccessClaims, AccessContextAuthority
from rail.observed_run_artifact import (
    ObservedRunArtifactRepository,
    RunArtifactObservation,
    record_observed_run_artifact,
)


NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)
HERE = Path(__file__).resolve().parent


def ref(kind, identity, data, *, authority="opensaddle://core"):
    return ResourceRef(
        authority=authority, resource_type=kind, resource_id=identity,
        version="1", digest="sha256:" + sha256(data).hexdigest(),
    )


class Access:
    def authorize(self, resource, *, at=None):
        return None  # Explicit fixture access, not production authorization.


class CoreArtifact:
    def __init__(self, data):
        self.data = data
        self.observation = RunArtifactObservation(
            project_id="example", run_id="run-1",
            run_ref=ref("run", "run-1", b"completed"),
            artifact_ref=ref("artifact", "artifact-1", data),
            core_run_updated_at=NOW,
        )

    def identify(self, run_id):
        assert run_id == "run-1"
        return self.observation

    def read_artifact(self, artifact_ref, *, max_bytes):
        assert artifact_ref == self.observation.artifact_ref
        assert len(self.data) <= max_bytes
        return self.data


class CoreTrust:
    def verify(self, receipt):
        return None  # Fixture predecessor trust only.


class FixtureInvalidation:
    def __init__(self, target):
        self.target = target.exact_key

    def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
        assert event_id and changed_ref.exact_key == self.target
        assert event_digest.startswith("sha256:") and at.tzinfo is not None


def review_action(candidate, evidence, nonce):
    reviewer = ref(
        "reviewer", "user/fixture-reviewer", b"fixture reviewer",
        authority="https://fixture-review.example.test",
    )
    exact_refs = tuple(dict.fromkeys((
        *candidate.package_refs, *candidate.command_refs,
        *candidate.environment_refs, *candidate.dependency_refs,
        reviewer, evidence,
    )))
    authority = AccessContextAuthority(
        {"fixture": b"bounded-fixture-review"}, issuer=reviewer.authority,
        required_capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
    )
    claims = AccessClaims(
        issuer=reviewer.authority, tenant_id="example", project_id="example",
        subject=reviewer.resource_id, delegator="user/operator",
        delegation_id=f"delegation/{nonce}",
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
        authority, authority.issue(claims, key_id="fixture"),
        tenant_id="example", project_id="example", exact_refs=exact_refs,
        allowed_candidate_digests=(candidate.record_digest,),
        capability_digest="sha256:" + "a" * 64, clock=lambda: NOW,
    )
    return reviewer, action


def accept(repository, candidate, *, decision, nonce):
    test_ref = ref("test", f"tests/{nonce}", f"independent {nonce}".encode())
    reviewer, action = review_action(candidate, test_ref, nonce)
    return ProcedureReviewService(repository=repository, clock=lambda: NOW).review(
        candidate.record_digest, decision_id=decision, reviewer_ref=reviewer,
        evidence_refs=(test_ref,), accepted=True, authorizer=Access(),
        review_authorizer=action,
    )


def main():
    data = (HERE / "fixtures/observed-run.txt").read_bytes()
    source = CoreArtifact(data)
    access = Access()
    with tempfile.TemporaryDirectory(prefix="krail-governed-memory-") as temp:
        path = str(Path(temp) / "semantic.json")
        repository = CoreProvenanceRepository(
            path, tenant_id="example", project_id="example",
            observed_source=source, observed_authorizer=access,
        )
        command = ref("command", "opensaddle/command/review", b"command-v1")
        environment = ref("environment", "opensaddle/environment-revision/example", b"environment-v1")
        initial = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
            create_core_provenance_receipt(
                receipt_id="receipt:fixture", command_ref=command,
                environment_ref=environment, observed_at=NOW,
            ), authorizer=access, trust=CoreTrust(),
        ).record
        accepted_initial = accept(repository, initial, decision="review:initial", nonce="initial")
        assert accepted_initial.promoted_record is not None

        document = ref("document", "sources/review-guide", b"source-v1")
        package = ref("package", "packages/review-extension", b"package-v1")
        evidence = record_observed_run_artifact(
            "run-1", source=source, authorizer=access,
            input_refs=(document, package),
        )
        ObservedRunArtifactRepository(
            path, tenant_id="example", project_id="example",
        ).save(evidence, source=source, authorizer=access, at=NOW)
        candidate = ObservedProcedureCandidateService(
            repository=repository, clock=lambda: NOW,
        ).propose(
            parent_decision_id="review:initial", evidence_ref=evidence.exact_ref(),
            procedure_version="2-observed",
            rationale="Observed bytes suggest a revision; review required.",
        )
        assert candidate.lifecycle == "desired"
        assert candidate.review_ref is None and candidate.test_evidence_refs == ()
        assert candidate.activation_ref is None
        repository.save_observed_candidate(
            candidate, parent_decision_id="review:initial",
            evidence_ref=evidence.exact_ref(), at=NOW,
        )
        reopened = CoreProvenanceRepository(
            path, tenant_id="example", project_id="example",
            observed_source=source, observed_authorizer=access,
        )
        assert reopened.find_procedure(candidate.record_digest) == candidate
        request = ProcedureExplanationRequest(candidate_digest=candidate.record_digest)
        explanation = ProcedureExplanationService(repository=reopened, clock=lambda: NOW)
        assert explanation.actionable_guidance(request, authorizer=access) is None
        print("Observed candidate: desired; review and activation absent")

        reviewed = accept(reopened, candidate, decision="review:observed", nonce="observed")
        assert reviewed.promoted_record is not None
        assert reviewed.promoted_record.lifecycle == "reviewed"
        assert reviewed.promoted_record.activation_ref is None
        assert explanation.actionable_guidance(request, authorizer=access) is not None
        print("Fixture review: guidance available; activation absent")

        reopened.record_invalidation(
            event_id="invalidation:document", changed_ref=document,
            reason="exact source revision withdrawn", at=NOW,
            authorizer=FixtureInvalidation(document),
        )
        after = explanation.explain(request, authorizer=access)
        assert after.support_status == "stale"
        assert explanation.actionable_guidance(request, authorizer=access) is None
        assert reopened.load_review("review:observed") == reviewed
        print("After exact source invalidation: stale; guidance withheld; review retained")


if __name__ == "__main__":
    main()
