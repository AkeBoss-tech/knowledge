"""Public trusted-local observation to procedure-candidate journey."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from krail.provider.v1 import ResourceRef
from rail.extension_registry import DomainExtensionRegistry, describe_extension, describe_operator
from rail.observed_invocation import (
    MAX_OBSERVED_OUTPUT_BYTES,
    ObservationAwareProcedureAuthorizer,
    ObservedInvocationRepository,
    RunObservation,
    record_observed_invocation,
    verify_observed_invocation_integrity,
)
from rail.procedural_memory import (
    add_observed_evidence_to_procedure_candidate,
    authorize_procedure,
    create_procedure,
    ProcedureRecord,
    verify_procedure_integrity,
)


NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _ref(kind: str, resource_id: str, content: bytes) -> ResourceRef:
    return ResourceRef(
        authority="opensaddle://core" if kind in {"run", "artifact"} else "krail://project/personal",
        resource_type=kind,
        resource_id=resource_id,
        version="1",
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
    )


class Authorize:
    def __init__(self, denied: str | None = None, *, revoke_after_read: bool = False):
        self.denied = denied
        self.revoke_after_read = revoke_after_read
        self.source = None

    def authorize(self, ref: ResourceRef) -> None:
        if ref.resource_id == self.denied or (self.revoke_after_read and self.source is not None and self.source.reads):
            raise PermissionError("source unavailable")


class LocalObservationSource:
    def __init__(self, observation: RunObservation, artifact: bytes):
        self.observation = observation
        self.artifact = artifact
        self.reads = 0
        self.identifications = 0

    def identify(self, invocation):
        self.identifications += 1
        return self.observation

    def read_artifact(self, ref, *, max_bytes):
        self.reads += 1
        assert ref == self.observation.output_artifact_ref and max_bytes == MAX_OBSERVED_OUTPUT_BYTES
        return self.artifact


def _journey():
    operator = describe_operator(
        operator_id="personal.model-summary", version="1.0.0",
        input_schema="personal.note.v1", output_schema="personal.summary.v1",
        deterministic=False,
    )
    extension = describe_extension(
        extension_id="personal", version="1.0.0",
        payload_schemas=("personal.note.v1", "personal.summary.v1"), operators=(operator,),
    )
    registry = DomainExtensionRegistry()
    registry.register(extension, {operator.operator_id: lambda inputs, config: {"summary": inputs[0]["text"][:7]}})
    input_ref = _ref("note", "notes/one", b"reviewed note")
    result = registry.dispatch(
        operator.operator_id, operator.version, ((input_ref, {"text": "reviewed note"}),),
        config={"model": "local-model-v1"}, authorizer=Authorize(),
    )
    artifact = json.dumps(result.output, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    observation = RunObservation(
        run_ref=_ref("run", "run-123", b"run-123"),
        output_artifact_ref=_ref("artifact", "artifact-123", artifact),
        model_provider="local", model_id="summary-model", model_version="2026-09-19",
        execution_config_digest=result.config_digest, observed_at=NOW,
    )
    return operator, result, LocalObservationSource(observation, artifact), input_ref


def test_observed_model_output_becomes_source_bound_candidate_without_human_review(tmp_path):
    operator, result, source, input_ref = _journey()
    evidence = record_observed_invocation(result, operator, source=source, authorizer=Authorize())
    assert evidence.evidence_state == "caller_observed_unverified"
    assert evidence.observation.output_artifact_ref.digest == result.output_digest
    assert verify_observed_invocation_integrity(evidence, operator) == evidence
    assert source.reads == 1 and source.identifications == 2
    path = str(tmp_path / "observed.json")
    repository = ObservedInvocationRepository(path, tenant_id="personal", project_id="journal")
    repository.save(evidence, operator, source=source, authorizer=Authorize(), at=NOW)
    reopened = ObservedInvocationRepository(path, tenant_id="personal", project_id="journal")
    evidence = reopened.read_exact(evidence.exact_ref(), operator, source=source, authorizer=Authorize())
    assert evidence.observation.run_ref.resource_id == "run-123"

    command = _ref("command", "commands/summary", b"command")
    environment = _ref("environment", "environments/current", b"environment")
    old_test = _ref("test", "tests/old", b"old test")
    old_review = _ref("review", "reviews/old", b"old review")
    previous = create_procedure(
        procedure_id="personal/review-summary", procedure_version="1.0.0", lifecycle="reviewed",
        authority="krail://project/personal", writer_family="procedure-owner",
        valid_from=NOW, recorded_at=NOW, package_refs=(), command_refs=(command,),
        environment_refs=(environment,), test_evidence_refs=(old_test,), dependency_refs=(),
        rationale="Previously reviewed instructions.", review_ref=old_review,
    )
    candidate = add_observed_evidence_to_procedure_candidate(
        previous, evidence, operator, procedure_version="1.1.0-candidate",
        rationale="Observed a summary; human review and verification remain pending.", recorded_at=NOW,
    )
    assert candidate.lifecycle == "desired" and candidate.review_ref is None
    assert candidate.activation_ref is None and candidate.test_evidence_refs == ()
    assert candidate.supersedes_digest == previous.record_digest
    assert {ref.exact_key for ref in candidate.dependency_refs} == {
        evidence.exact_ref().exact_key, input_ref.exact_key,
        source.observation.run_ref.exact_key, source.observation.output_artifact_ref.exact_key,
    }
    reopened_candidate = ProcedureRecord.model_validate(candidate.model_dump(mode="json"))
    reader = ObservationAwareProcedureAuthorizer(reopened, operator, source, Authorize())
    verify_procedure_integrity(authorize_procedure(reopened_candidate, reader))
    with pytest.raises(PermissionError, match="procedure access denied"):
        authorize_procedure(reopened_candidate, ObservationAwareProcedureAuthorizer(
            reopened, operator, source, Authorize(denied="artifact-123")))
    with pytest.raises(LookupError, match="not available"):
        ObservedInvocationRepository(path, tenant_id="personal", project_id="other").read_exact(
            evidence.exact_ref(), operator, source=source, authorizer=Authorize())


def test_observation_refuses_mismatched_or_lost_artifact_and_rechecks_live_authority():
    operator, result, source, _ = _journey()
    source.artifact = b'{"summary":"different"}'
    with pytest.raises(ValueError, match="exact canonical output"):
        record_observed_invocation(result, operator, source=source, authorizer=Authorize())

    operator, result, source, _ = _journey()
    source.artifact = source.artifact + b" "
    with pytest.raises(ValueError, match="exact canonical output"):
        record_observed_invocation(result, operator, source=source, authorizer=Authorize())

    operator, result, source, _ = _journey()
    access = Authorize(revoke_after_read=True)
    access.source = source
    with pytest.raises(PermissionError, match="observed invocation access denied"):
        record_observed_invocation(result, operator, source=source, authorizer=access)
    assert source.reads == 1

    operator, result, source, _ = _journey()
    with pytest.raises(PermissionError, match="observed invocation access denied"):
        record_observed_invocation(result, operator, source=source, authorizer=Authorize(denied="artifact-123"))
    assert source.reads == 0

    operator, result, source, _ = _journey()
    source.artifact = b"x" * (MAX_OBSERVED_OUTPUT_BYTES + 1)
    with pytest.raises(ValueError, match="bounded bytes"):
        record_observed_invocation(result, operator, source=source, authorizer=Authorize())

    operator, result, source, _ = _journey()
    class DriftingSource(LocalObservationSource):
        def read_artifact(self, ref, *, max_bytes):
            data = super().read_artifact(ref, max_bytes=max_bytes)
            self.observation = self.observation.model_copy(update={
                "run_ref": _ref("run", "different-run", b"different-run"),
            })
            return data
    drifting = DriftingSource(source.observation, source.artifact)
    with pytest.raises(ValueError, match="Run identity changed"):
        record_observed_invocation(result, operator, source=drifting, authorizer=Authorize())


def test_observation_rejects_metadata_drift_and_tampering_without_changing_v1_dispatch():
    operator, result, source, _ = _journey()
    source.observation = source.observation.model_copy(update={
        "execution_config_digest": "sha256:" + "0" * 64,
    })
    with pytest.raises(ValueError, match="configuration"):
        record_observed_invocation(result, operator, source=source, authorizer=Authorize())

    operator, result, source, _ = _journey()
    evidence = record_observed_invocation(result, operator, source=source, authorizer=Authorize())
    evidence.invocation.output["summary"] = "altered"
    with pytest.raises(ValueError, match="mutated"):
        verify_observed_invocation_integrity(evidence, operator)

    operator, result, source, _ = _journey()
    class MutateAfterArtifactRead(Authorize):
        def authorize(self, ref):
            if source.reads and ref.resource_type == "artifact":
                result.output["summary"] = "changed during final authorization"
    with pytest.raises(ValueError, match="output was mutated"):
        record_observed_invocation(result, operator, source=source,
                                   authorizer=MutateAfterArtifactRead())
    assert source.reads == 1

    deterministic = describe_operator(
        operator_id="personal.fixed", version="1.0.0", input_schema="personal.note.v1",
        output_schema="personal.summary.v1", deterministic=True,
    )
    with pytest.raises(ValueError, match="deterministic operators"):
        record_observed_invocation(result, deterministic, source=source, authorizer=Authorize())
