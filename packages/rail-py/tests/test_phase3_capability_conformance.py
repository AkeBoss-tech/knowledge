from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from krail.provider.capabilities import CapabilityNegotiationRequest
from krail.provider.v1 import ResourceRef
from rail import cli as rail_cli
from rail.bootstrap import bootstrap_future_project
from rail.capability_publication import (
    OUTCOME_CAPABILITY_ID,
    VERIFICATION_CAPABILITY_ID,
    outcome_evidence_descriptor,
    verification_evidence_descriptor,
)
from rail.local import LocalEngine
from rail.outcome_observations import OutcomeIngestEnvelope, OutcomeIngestRequest
from rail.project import Project
from rail.verification_evidence import (
    UNAVAILABLE_CHECK_DISCLOSURES,
    VerificationEvidenceRequest,
    VerificationGap,
)


FIXTURES = Path(__file__).parent / "fixtures"
FOUNDATION = FIXTURES / "evidence_foundation"
SCENARIOS = FIXTURES / "phase3_conformance" / "scenarios.json"


def _project(tmp_path: Path) -> Project:
    root = bootstrap_future_project(tmp_path, name="Phase 3", slug="phase-3")
    return Project(slug="phase-3", backend=LocalEngine(project_path=root))


def _verification() -> VerificationEvidenceRequest:
    return VerificationEvidenceRequest.model_validate_json(
        (FOUNDATION / "verification_request.json").read_text(encoding="utf-8")
    )


def _outcome() -> OutcomeIngestRequest:
    return OutcomeIngestRequest.model_validate_json(
        (FOUNDATION / "outcome_request.json").read_text(encoding="utf-8")
    )


def _outcome_envelope(*, prior_observation=None) -> OutcomeIngestEnvelope:
    return OutcomeIngestEnvelope(
        request=_outcome(),
        prior_observation=prior_observation,
    )


def _scenarios() -> dict:
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def _superseding_request(
    request: OutcomeIngestRequest,
    prior_observation,
) -> OutcomeIngestRequest:
    prior_ref = request.observation.resource_ref
    assert prior_ref is not None
    newer_ref = prior_ref.model_copy(
        update={
            "version": _scenarios()["provider_versions"]["ci_drift"],
            "digest": "sha256:" + "d" * 64,
        }
    )
    return request.model_copy(
        update={
            "observation": request.observation.model_copy(
                update={
                    "observed_at": request.observation.observed_at + timedelta(minutes=5),
                    "resource_ref": newer_ref,
                    "provider_payload_digest": "sha256:" + "e" * 64,
                    "supersedes_observation_digest": prior_observation.observation_digest,
                }
            ),
            "semantic_assertions": tuple(
                item.model_copy(update={"source_ref": newer_ref})
                for item in request.semantic_assertions
            ),
        }
    )


@pytest.mark.parametrize(
    ("descriptor_factory", "capability_id", "operation_id", "classification"),
    [
        (verification_evidence_descriptor, VERIFICATION_CAPABILITY_ID, "assemble_verification_evidence", "read-only"),
        (outcome_evidence_descriptor, OUTCOME_CAPABILITY_ID, "ingest_outcome_evidence", "read-only"),
    ],
)
def test_phase3_descriptors_are_stable_bounded_and_non_authorizing(
    descriptor_factory, capability_id: str, operation_id: str, classification: str
) -> None:
    first = descriptor_factory()
    assert first == descriptor_factory()
    assert first.capability_id == capability_id
    assert first.semantic_version == "1.0.0"
    assert first.descriptor_digest == _scenarios()["descriptor_digests"][capability_id]
    assert first.operations[0].operation_id == operation_id
    assert first.operations[0].input_schema["additionalProperties"] is False
    assert first.operations[0].output_schema["additionalProperties"] is False
    assert first.effects.classification == classification
    assert first.effects.external_effects is False
    assert first.authorization.descriptor_grants_authorization is False
    assert first.limits and first.semantic_processing_versions
    serialized = first.model_dump_json().lower()
    for forbidden in ("github execution", "runtime scheduling", "opensaddle"):
        assert forbidden not in serialized


@pytest.mark.parametrize("capability_id", [VERIFICATION_CAPABILITY_ID, OUTCOME_CAPABILITY_ID])
def test_phase3_descriptor_negotiation_supports_version_and_digest_pins(
    tmp_path: Path, capability_id: str
) -> None:
    provider = _project(tmp_path).provider
    descriptor = provider.capability_descriptor(capability_id)
    accepted = provider.negotiate_capability(
        CapabilityNegotiationRequest(
            capability_id=capability_id,
            consumer_version="1.0.0",
            descriptor_digest=descriptor.descriptor_digest,
        )
    )
    rejected = provider.negotiate_capability(
        CapabilityNegotiationRequest(capability_id=capability_id, consumer_version="2.0.0")
    )
    assert accepted.compatible is True
    assert accepted.descriptor == descriptor
    assert rejected.compatible is False


def test_phase3_verification_replay_exact_versions_checks_bounds_and_non_leakage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = _verification()
    scenarios = _scenarios()
    direct = project.assemble_verification_evidence(request)
    provider = project.provider.assemble_verification_evidence(request)
    replay = project.provider.assemble_verification_evidence(
        VerificationEvidenceRequest.model_validate_json(request.model_dump_json())
    )
    assert direct == provider == replay
    assert replay.diff.ref.digest == request.diff.ref.digest
    assert replay.changed_files == request.changed_files
    assert replay.context_brief.source_refs == request.context_brief.source_refs
    assert replay.claims[0].tool_versions == request.tool_versions
    assert replay.claims[0].processing_versions == request.processing_versions
    assert scenarios["diff_digest"] != replay.diff.ref.digest

    source = request.context_brief.issue.model_copy(
        update={"version": scenarios["changed_source_version"], "digest": "sha256:" + "1" * 64}
    )
    source_refs = tuple(source if item.identity == source.identity else item for item in request.context_brief.source_refs)
    context = request.context_brief.model_copy(update={"issue": source, "source_refs": source_refs})
    diff = request.diff.model_copy(
        update={
            "role": "patch",
            "ref": request.diff.ref.model_copy(
                update={"version": "git:" + "c" * 40, "digest": scenarios["patch_digest"]}
            ),
        }
    )
    changed_files = tuple(
        ResourceRef(
            authority=request.changed_files[0].authority,
            resource_type="file",
            resource_id=item["resource_id"],
            version=item["version"],
            digest=item["digest"],
        )
        for item in scenarios["changed_files"]
    )
    passing = request.checks[0].model_copy(update={"check_id": "unit-pass"})
    failing = request.checks[0].model_copy(
        update={"check_id": "lint-fail", "kind": "lint", "status": "failed", "summary": "Lint failed for the pinned patch."}
    )
    partial_artifact = request.checks[0].artifact_refs[0].model_copy(
        update={"state": "partial", "partial_content_digest": "sha256:" + "2" * 64}
    )
    partial = request.checks[0].model_copy(
        update={
            "check_id": "ci-partial",
            "status": "partial",
            "summary": "A bounded partial CI result was supplied.",
            "artifact_refs": (partial_artifact,),
        }
    )
    unavailable = []
    gaps = []
    for item in scenarios["checks"][3:]:
        status = item["status"]
        unavailable.append(
            request.checks[0].model_copy(
                update={
                    "check_id": item["check_id"],
                    "status": status,
                    "summary": UNAVAILABLE_CHECK_DISCLOSURES.get(status, "A bounded partial result was supplied."),
                    "artifact_refs": (),
                }
            )
        )
        gaps.append(
            VerificationGap(
                state=status,
                code=f"{status}-check",
                disclosure="Explicit bounded gap.",
            )
        )
    gaps.insert(
        0,
        VerificationGap(
            state="partial",
            code="partial-check",
            disclosure="Explicit bounded gap.",
        ),
    )
    claim = request.claims[0].model_copy(
        update={"source_refs": (source,), "file_refs": changed_files, "check_ids": ("unit-pass", "lint-fail")}
    )
    bounded = request.model_copy(
        update={
            "context_brief": context,
            "diff": diff,
            "changed_files": changed_files,
            "checks": (passing, failing, partial, *unavailable),
            "claims": (claim,),
            "gaps": tuple(gaps),
        }
    )
    result = project.provider.assemble_verification_evidence(bounded)
    payload = result.model_dump_json()
    assert result.evidence_packet.truncated is True
    assert result.diff.role == "patch"
    assert result.diff.ref.digest == scenarios["patch_digest"]
    assert tuple(item.resource_id for item in result.changed_files) == tuple(
        item["resource_id"] for item in scenarios["changed_files"]
    )
    assert {item.status for item in result.checks} == {
        "passed", "failed", "partial", "missing", "inaccessible", "redacted"
    }
    assert all(check.artifact_refs == () for check in result.checks if check.status in {"missing", "inaccessible", "redacted"})
    for hidden in scenarios["forbidden_hidden_values"]:
        assert hidden not in payload


def test_phase3_outcomes_pin_provider_authority_and_require_explicit_drift(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = _outcome()
    envelope = OutcomeIngestEnvelope(request=request)
    first = project.ingest_outcome_evidence(envelope)
    replay = project.provider.ingest_outcome_evidence(
        OutcomeIngestEnvelope.model_validate_json(envelope.model_dump_json())
    )
    assert first == replay
    assert first.authority == "provider-observed"
    assert first.resource_ref == request.observation.resource_ref
    assert first.semantic_assertions[0].source_ref == first.resource_ref
    assert first.links.context_brief_digest == request.links.context_brief_digest
    assert first.links.operation_receipt_ref is not None
    assert first.links.operation_receipt_ref.event_digest.startswith("sha256:")

    old_ref = request.observation.resource_ref
    newer_request = _superseding_request(request, first)
    with pytest.raises(ValueError, match="exact prior observation"):
        project.provider.ingest_outcome_evidence(OutcomeIngestEnvelope(request=newer_request))
    updated_envelope = OutcomeIngestEnvelope(
        request=newer_request,
        prior_observation=first,
    )
    updated = project.provider.ingest_outcome_evidence(updated_envelope)
    assert updated == project.ingest_outcome_evidence(
        OutcomeIngestEnvelope.model_validate_json(updated_envelope.model_dump_json())
    )
    assert updated.drift is not None
    assert updated.supersedes_observation_digest == first.observation_digest
    assert first.resource_ref == old_ref


def test_phase3_outcome_lifecycle_and_hidden_states_are_deterministic_non_leaking(tmp_path: Path) -> None:
    provider = _project(tmp_path).provider
    base = _outcome()
    previous = provider.ingest_outcome_evidence(OutcomeIngestEnvelope(request=base))
    observed_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    for index, state in enumerate(("partial", "corrected", "retained"), start=1):
        observation = base.observation.model_copy(
            update={
                "state": state,
                "observed_at": observed_at + timedelta(minutes=index),
                "supersedes_observation_digest": previous.observation_digest,
                "retention_until": observed_at + timedelta(days=30) if state == "retained" else None,
            }
        )
        request = base.model_copy(update={"observation": observation})
        envelope = OutcomeIngestEnvelope(request=request, prior_observation=previous)
        current = provider.ingest_outcome_evidence(envelope)
        assert current == provider.ingest_outcome_evidence(envelope)
        previous = current

    erased_observation = base.observation.model_copy(
        update={
            "state": "erased",
            "observed_at": observed_at + timedelta(days=31),
            "resource_ref": None,
            "provider_payload_digest": None,
            "bounded_summary": None,
            "supersedes_observation_digest": previous.observation_digest,
            "erasure_reason_digest": "sha256:" + "f" * 64,
        }
    )
    erased = provider.ingest_outcome_evidence(
        OutcomeIngestEnvelope(
            request=base.model_copy(
                update={"observation": erased_observation, "semantic_assertions": ()}
            ),
            prior_observation=previous,
        )
    )
    serialized = erased.model_dump_json()
    assert erased.resource_ref is None
    for hidden in _scenarios()["forbidden_hidden_values"]:
        assert hidden not in serialized


def test_phase3_python_provider_and_cli_are_equivalent(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path)
    verification = _verification()
    expected_verification = project.assemble_verification_evidence(verification).model_dump(mode="json")
    rail_cli.cmd_provider(
        project,
        argparse.Namespace(
            provider_command="assemble-verification-evidence",
            request=verification.model_dump_json(),
        ),
    )
    assert json.loads(capsys.readouterr().out) == expected_verification

    outcome = _outcome()
    envelope = OutcomeIngestEnvelope(request=outcome)
    expected_outcome = project.ingest_outcome_evidence(envelope).model_dump(mode="json")
    rail_cli.cmd_provider(
        project,
        argparse.Namespace(
            provider_command="ingest-outcome-evidence",
            request=envelope.model_dump_json(),
        ),
    )
    assert json.loads(capsys.readouterr().out) == expected_outcome

    first = project.ingest_outcome_evidence(envelope)
    superseding = OutcomeIngestEnvelope(
        request=_superseding_request(outcome, first),
        prior_observation=first,
    )
    expected_superseding = project.provider.ingest_outcome_evidence(superseding).model_dump(
        mode="json"
    )
    rail_cli.cmd_provider(
        project,
        argparse.Namespace(
            provider_command="ingest-outcome-evidence",
            request=superseding.model_dump_json(),
        ),
    )
    assert json.loads(capsys.readouterr().out) == expected_superseding


def test_phase3_outcome_envelope_is_strict_and_requires_exact_prior(tmp_path: Path) -> None:
    request = _outcome()
    envelope = OutcomeIngestEnvelope(request=request)
    payload = envelope.model_dump(mode="json")
    payload["unexpected"] = "not-accepted"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        OutcomeIngestEnvelope.model_validate(payload)

    missing_prior = request.model_copy(
        update={
            "observation": request.observation.model_copy(
                update={"supersedes_observation_digest": "sha256:" + "9" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="exact prior observation"):
        _project(tmp_path).ingest_outcome_evidence(
            OutcomeIngestEnvelope(request=missing_prior)
        )


def test_phase3_outcome_utf8_bounds_are_equivalent_across_python_provider_and_cli(
    tmp_path: Path,
    capsys,
) -> None:
    request_payload = _outcome().model_dump(mode="json")
    request_payload["observation"]["bounded_summary"] = "é" * 2048
    request_payload["semantic_assertions"][0]["text"] = "é" * 8192
    envelope = OutcomeIngestEnvelope(request=OutcomeIngestRequest.model_validate(request_payload))
    project = _project(tmp_path)
    python_result = project.ingest_outcome_evidence(envelope)
    provider_result = project.provider.ingest_outcome_evidence(envelope)
    rail_cli.cmd_provider(
        project,
        argparse.Namespace(
            provider_command="ingest-outcome-evidence",
            request=envelope.model_dump_json(),
        ),
    )
    assert python_result == provider_result
    assert json.loads(capsys.readouterr().out) == python_result.model_dump(mode="json")

    oversized = envelope.model_dump(mode="json")
    oversized["request"]["semantic_assertions"][0]["text"] = "é" * 8193
    with pytest.raises(ValidationError, match="16384 UTF-8 bytes"):
        rail_cli.cmd_provider(
            project,
            argparse.Namespace(
                provider_command="ingest-outcome-evidence",
                request=json.dumps(oversized),
            ),
        )


def test_phase3_publication_has_no_external_runtime_or_codec_dependency() -> None:
    import rail.capability_publication as module

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("subprocess", "github", "credential", "scheduler", "opensaddle"):
        assert forbidden not in source
