from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.artifact_references import ArtifactReference
from rail.verification_evidence import (
    VerificationEvidence,
    VerificationEvidenceRequest,
    VerificationEvidenceService,
)


FIXTURE = Path(__file__).parent / "fixtures" / "evidence_foundation" / "verification_request.json"


def _request() -> VerificationEvidenceRequest:
    return VerificationEvidenceRequest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_verification_fixture_replays_exact_digest_trace_and_bounded_packet() -> None:
    request = _request()
    service = VerificationEvidenceService()

    first = service.assemble(request)
    second = service.assemble(VerificationEvidenceRequest.model_validate_json(request.model_dump_json()))

    assert first == second
    assert first.evidence_digest == second.evidence_digest
    assert first.evidence_packet == second.evidence_packet
    assert first.context_brief.brief_digest == request.context_brief.brief_digest
    assert first.diff.ref == request.diff.ref
    assert first.claims[0].source_refs[0].version == "etag:issue-184-v3"
    assert first.claims[0].diff_ref.digest == request.diff.ref.digest
    assert first.claims[0].file_refs == request.changed_files
    assert first.claims[0].check_ids == ("pytest-focused",)
    assert first.claims[0].check_artifact_refs == tuple(
        item.ref for item in request.checks[0].artifact_refs
    )
    assert first.claims[0].tool_versions == request.tool_versions
    assert first.claims[0].environment_versions == request.environment_versions
    assert first.claims[0].processing_versions == request.processing_versions
    assert first.domain_event_ref.event_digest == first.evidence_digest
    assert first.domain_event_ref.operation_id == "op-184"

    serialized = first.model_dump_json()
    assert "unbounded-log-sentinel" not in serialized.lower()
    assert "patch changes" in serialized.lower()
    assert len(first.evidence_packet.items[0].excerpt.encode("utf-8")) <= 16_384


def test_unavailable_and_partial_verification_are_explicit_and_non_leaking() -> None:
    request = _request()
    missing_check = request.checks[0].model_copy(
        update={
            "check_id": "pytest-restricted",
            "status": "inaccessible",
            "summary": "Check evidence is inaccessible in the supplied authorization context.",
            "artifact_refs": (),
        }
    )
    explicit = VerificationEvidenceRequest.model_validate(
        {
            **request.model_dump(mode="python"),
            "checks": (*request.checks, missing_check),
            "gaps": (
                {
                    "state": "inaccessible",
                    "code": "check-inaccessible",
                    "disclosure": "Check evidence is inaccessible in the supplied authorization context.",
                },
            ),
        }
    )
    result = VerificationEvidenceService().assemble(explicit)

    assert result.evidence_packet.truncated is True
    assert result.checks[1].check_id == "pytest-restricted"
    assert result.checks[1].artifact_refs == ()
    assert "count" not in json.dumps(result.gaps[0].model_dump())

    with pytest.raises(ValidationError, match="observed checks"):
        VerificationEvidenceRequest.model_validate(
            {
                **explicit.model_dump(mode="python"),
                "claims": (
                    request.claims[0].model_copy(update={"check_ids": (missing_check.check_id,)}),
                ),
            }
        )

    with pytest.raises(ValidationError, match="explicit corresponding verification gaps"):
        VerificationEvidenceRequest.model_validate(
            {**explicit.model_dump(mode="python"), "gaps": ()}
        )

    partial_check = request.checks[0].model_copy(update={"status": "partial"})
    with pytest.raises(ValidationError, match="explicit corresponding verification gaps"):
        VerificationEvidenceRequest.model_validate(
            {**request.model_dump(mode="python"), "checks": (partial_check,)}
        )

    with pytest.raises(ValidationError, match="must not leak"):
        request.checks[0].model_validate({**request.checks[0].model_dump(), "status": "redacted"})

    artifact = request.checks[0].artifact_refs[0]
    with pytest.raises(ValidationError, match="partial artifacts require"):
        ArtifactReference.model_validate({**artifact.model_dump(), "state": "partial"})


def test_verification_rejects_unlinked_sources_files_and_checks() -> None:
    request = _request()
    claim = request.claims[0]
    alien = request.changed_files[0].model_copy(update={"resource_id": "src/unobserved.py"})

    with pytest.raises(ValidationError, match="Context Brief sources"):
        VerificationEvidenceRequest.model_validate(
            {**request.model_dump(mode="python"), "claims": [claim.model_copy(update={"source_refs": (alien,)})]}
        )
    with pytest.raises(ValidationError, match="changed-file versions"):
        VerificationEvidenceRequest.model_validate(
            {**request.model_dump(mode="python"), "claims": [claim.model_copy(update={"file_refs": (alien,)})]}
        )
    with pytest.raises(ValidationError, match="declared checks"):
        VerificationEvidenceRequest.model_validate(
            {**request.model_dump(mode="python"), "claims": [claim.model_copy(update={"check_ids": ("unknown",)})]}
        )


def test_verification_module_has_no_executor_or_external_write_dependency() -> None:
    import rail.verification_evidence as module

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "opensaddle" not in source

    assert ArtifactReference.model_json_schema()["additionalProperties"] is False
    assert VerificationEvidenceRequest.model_json_schema()["additionalProperties"] is False
    assert VerificationEvidence.model_json_schema()["additionalProperties"] is False
