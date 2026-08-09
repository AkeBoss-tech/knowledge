from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.outcome_observations import (
    OutcomeIngestRequest,
    OutcomeObservation,
    OutcomeObservationService,
    ProviderObservationInput,
)


FIXTURE = Path(__file__).parent / "fixtures" / "evidence_foundation" / "outcome_request.json"
NOW = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)


def _request() -> OutcomeIngestRequest:
    return OutcomeIngestRequest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_provider_observation_is_authoritative_linked_and_deterministic() -> None:
    request = _request()
    service = OutcomeObservationService()

    first = service.ingest(request)
    second = service.ingest(OutcomeIngestRequest.model_validate_json(request.model_dump_json()))

    assert first == second
    assert first.authority == "provider-observed"
    assert first.resource_ref == request.observation.resource_ref
    assert first.provider_payload_digest == request.observation.provider_payload_digest
    assert first.links.context_brief_digest == request.links.context_brief_digest
    assert first.links.verification_evidence_digest == request.links.verification_evidence_digest
    assert first.links.operation_receipt_ref is not None
    assert first.links.operation_receipt_ref.model_fields_set <= {
        "event_type", "event_digest", "operation_id", "correlation_id", "causation_id"
    }
    assert first.semantic_assertions[0].source_ref == first.resource_ref
    assert first.domain_event_ref.event_digest == first.observation_digest


def test_new_provider_state_requires_explicit_supersession_and_records_drift() -> None:
    service = OutcomeObservationService()
    original_request = _request()
    original = service.ingest(original_request)
    old_ref = original_request.observation.resource_ref
    assert old_ref is not None
    new_ref = old_ref.model_copy(
        update={
            "version": "etag:pr-184-head-cccccccc",
            "digest": "sha256:" + "d" * 64,
        }
    )
    updated_observation = original_request.observation.model_copy(
        update={
            "observed_at": NOW + timedelta(minutes=5),
            "resource_ref": new_ref,
            "provider_payload_digest": "sha256:" + "e" * 64,
            "supersedes_observation_digest": original.observation_digest,
        }
    )
    updated_request = original_request.model_copy(
        update={
            "observation": updated_observation,
            "semantic_assertions": (
                original_request.semantic_assertions[0].model_copy(update={"source_ref": new_ref}),
            ),
        }
    )
    updated = service.ingest(updated_request, previous=original)

    assert updated.observation_digest != original.observation_digest
    assert updated.supersedes_observation_digest == original.observation_digest
    assert updated.drift is not None
    assert set(updated.drift.changed_fields) == {
        "observed-at",
        "resource-version",
        "resource-digest",
        "provider-payload",
    }
    assert original.resource_ref == old_ref

    with pytest.raises(ValueError, match="explicitly supersede"):
        service.ingest(
            updated_request.model_copy(
                update={"observation": updated_observation.model_copy(update={"supersedes_observation_digest": None})}
            ),
            previous=original,
        )

    alien_ref = new_ref.model_copy(update={"resource_id": "northstar/pulls/185"})
    with pytest.raises(ValueError, match="resource identity"):
        service.ingest(
            updated_request.model_copy(
                update={
                    "observation": updated_observation.model_copy(update={"resource_ref": alien_ref}),
                    "semantic_assertions": (
                        original_request.semantic_assertions[0].model_copy(update={"source_ref": alien_ref}),
                    ),
                }
            ),
            previous=original,
        )


@pytest.mark.parametrize("state", ["missing", "inaccessible", "redacted"])
def test_unavailable_provider_states_are_non_leaking(state: str) -> None:
    request = _request()
    hidden = request.observation.model_copy(
        update={
            "state": state,
            "resource_ref": None,
            "provider_payload_digest": None,
            "bounded_summary": None,
        }
    )
    result = OutcomeObservationService().ingest(
        request.model_copy(update={"observation": hidden, "semantic_assertions": ()})
    )

    assert result.resource_ref is None
    assert result.provider_payload_digest is None
    assert result.bounded_summary is None
    assert "northstar/pulls/184" not in result.model_dump_json()

    with pytest.raises(ValidationError, match="must not expose"):
        ProviderObservationInput.model_validate(
            {**hidden.model_dump(), "resource_ref": request.observation.resource_ref}
        )


def test_partial_correction_retention_and_erasure_are_append_only_and_deterministic() -> None:
    service = OutcomeObservationService()
    request = _request()
    original = service.ingest(request)

    partial_input = request.observation.model_copy(
        update={
            "state": "partial",
            "observed_at": NOW + timedelta(minutes=1),
            "supersedes_observation_digest": original.observation_digest,
        }
    )
    partial = service.ingest(
        request.model_copy(update={"observation": partial_input}),
        previous=original,
    )
    corrected_input = partial_input.model_copy(
        update={
            "state": "corrected",
            "observed_at": NOW + timedelta(minutes=2),
            "supersedes_observation_digest": partial.observation_digest,
        }
    )
    corrected_request = request.model_copy(update={"observation": corrected_input})
    corrected = service.ingest(corrected_request, previous=partial)
    repeated = service.ingest(corrected_request, previous=partial)
    assert corrected == repeated

    retained_input = corrected_input.model_copy(
        update={
            "state": "retained",
            "observed_at": NOW + timedelta(minutes=3),
            "retention_until": NOW + timedelta(days=30),
            "supersedes_observation_digest": corrected.observation_digest,
        }
    )
    retained = service.ingest(request.model_copy(update={"observation": retained_input}), previous=corrected)
    erased_input = retained_input.model_copy(
        update={
            "state": "erased",
            "observed_at": NOW + timedelta(days=31),
            "resource_ref": None,
            "provider_payload_digest": None,
            "bounded_summary": None,
            "retention_until": None,
            "supersedes_observation_digest": retained.observation_digest,
            "erasure_reason_digest": "sha256:" + "f" * 64,
        }
    )
    erased = service.ingest(
        request.model_copy(update={"observation": erased_input, "semantic_assertions": ()}),
        previous=retained,
    )

    assert partial.state == "partial"
    assert corrected.state == "corrected"
    assert retained.state == "retained"
    assert erased.state == "erased"
    assert erased.resource_ref is None
    assert erased.erasure_reason_digest == "sha256:" + "f" * 64


def test_outcomes_remain_standalone_without_external_correlation() -> None:
    request = _request()
    links = request.links.model_copy(update={"operation_receipt_ref": None, "operation_context": None})
    result = OutcomeObservationService().ingest(request.model_copy(update={"links": links}))

    assert result.domain_event_ref.operation_id is None
    assert result.links.operation_receipt_ref is None

    import rail.outcome_observations as module

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "opensaddle" not in source
    assert OutcomeIngestRequest.model_json_schema()["additionalProperties"] is False
    assert OutcomeObservation.model_json_schema()["additionalProperties"] is False
