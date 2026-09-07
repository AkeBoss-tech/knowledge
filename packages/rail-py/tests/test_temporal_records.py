from datetime import UTC, datetime
from hashlib import sha256

import pytest
import rail.temporal_records as temporal_records

from krail.provider.v1 import ResourceRef
from rail.temporal_records import (
    create_temporal_record,
    replay_temporal_records,
    supersede_temporal_record,
    verify_temporal_record_integrity,
)


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _ref(resource_id: str, version: str = "v1") -> ResourceRef:
    return ResourceRef(
        authority="https://knowledge.example.test",
        resource_type="evidence",
        resource_id=resource_id,
        version=version,
        digest="sha256:" + sha256(f"{resource_id}:{version}".encode()).hexdigest(),
    )


def _record(
    *,
    entity: str,
    schema: str,
    valid_hour: int,
    revision: str,
    supersedes: str | None = None,
    recorded_hour: int | None = None,
    ingested_hour: int | None = None,
    valid_to_hour: int | None = None,
):
    return create_temporal_record(
        record_id=f"{entity}:{revision}",
        entity_id=entity,
        entity_authority="https://authority.example.test",
        payload_schema=schema,
        payload_schema_version="1.0.0",
        kind="observation" if schema.startswith("robotics.") else "reported_claim",
        authority="https://authority.example.test",
        writer_family="fixture",
        valid_from=NOW.replace(hour=valid_hour),
        valid_to=NOW.replace(hour=valid_to_hour) if valid_to_hour is not None else None,
        recorded_at=NOW.replace(hour=recorded_hour if recorded_hour is not None else valid_hour + 1),
        ingested_at=NOW.replace(hour=ingested_hour) if ingested_hour is not None else None,
        source_refs=(_ref(f"sources/{entity}", revision),),
        provenance_refs=(),
        revision=revision,
        payload={"entity": entity, "value": revision},
        supersedes_digest=supersedes,
    )


def test_temporal_envelope_is_domain_neutral_for_robotics_and_company_records() -> None:
    robot = _record(entity="mug-7", schema="robotics.object_observation.v1", valid_hour=9, revision="r1")
    company = _record(entity="billing", schema="company.service_ownership.v1", valid_hour=9, revision="r1")
    assert robot.payload_schema != company.payload_schema
    assert robot.kind == "observation"
    assert company.kind == "reported_claim"
    assert robot.valid_from < robot.recorded_at


def test_out_of_order_replay_and_exact_supersession_are_deterministic() -> None:
    first = _record(entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=9, revision="r1")
    second = _record(entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=10, revision="r2", supersedes=first.record_digest)
    assert replay_temporal_records([second, first]) == (first, second)
    assert supersede_temporal_record(first, second) == second
    with pytest.raises(ValueError, match="supplied exact"):
        replay_temporal_records([second])


def test_temporal_supersession_requires_full_replay_identity() -> None:
    first = _record(entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=9, revision="r1")
    authority_mismatch = create_temporal_record(
        **{
            **first.model_dump(mode="python"),
            "record_id": "mug-7:r2",
            "revision": "r2",
            "authority": "https://other-authority.example.test",
            "supersedes_digest": first.record_digest,
        }
    )
    with pytest.raises(ValueError, match="authority"):
        supersede_temporal_record(first, authority_mismatch)


def test_temporal_rejects_invalid_time_and_mixed_identity() -> None:
    with pytest.raises(ValueError, match="valid_to"):
        create_temporal_record(
            record_id="x",
            entity_id="x",
            entity_authority="https://authority.example.test",
            payload_schema="company.policy_revision.v1",
            payload_schema_version="1.0.0",
            kind="approved_state",
            authority="https://authority.example.test",
            writer_family="fixture",
            valid_from=NOW,
            valid_to=NOW.replace(hour=11),
            recorded_at=NOW,
            source_refs=(_ref("sources/x"),),
            revision="r1",
            payload={},
        )
    first = _record(entity="a", schema="company.service_ownership.v1", valid_hour=9, revision="r1")
    other = _record(entity="b", schema="company.service_ownership.v1", valid_hour=10, revision="r1")
    with pytest.raises(ValueError, match="mix entity"):
        replay_temporal_records([first, other])


def test_payload_mutation_is_detected_at_consumption_boundary() -> None:
    record = _record(entity="mug-7", schema="robotics.object_observation.v1", valid_hour=9, revision="r1")
    record.payload["value"] = "tampered"
    with pytest.raises(ValueError, match="mutated"):
        verify_temporal_record_integrity(record)
    with pytest.raises(ValueError, match="mutated"):
        replay_temporal_records([record])


def test_string_and_datetime_inputs_normalize_to_same_digest() -> None:
    values = dict(
        record_id="x",
        entity_id="x",
        entity_authority="https://authority.example.test",
        payload_schema="company.policy_revision.v1",
        payload_schema_version="1.0.0",
        kind="approved_state",
        authority="https://authority.example.test",
        writer_family="fixture",
        valid_from=NOW,
        recorded_at=NOW,
        source_refs=(_ref("sources/x"),),
        revision="r1",
        payload={"ok": True},
    )
    assert create_temporal_record(**values).record_digest == create_temporal_record(
        **{**values, "valid_from": NOW.isoformat(), "recorded_at": NOW.isoformat()}
    ).record_digest


def test_temporal_query_separates_effective_and_known_time_for_retrospective_correction() -> None:
    first = _record(entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=10, revision="r1")
    correction = _record(
        entity="mug-7",
        schema="robotics.object_state_estimate.v1",
        valid_hour=9,
        revision="r2",
        supersedes=first.record_digest,
    ).model_copy(update={"recorded_at": NOW.replace(hour=13)})
    # The correction keeps a valid immutable digest after changing known time.
    correction = correction.model_copy(
        update={
            "record_digest": "sha256:" + "0" * 64,
        }
    )
    from rail.temporal_records import _record_digest

    correction = correction.model_copy(update={"record_digest": _record_digest(correction)})
    before = temporal_records.query_temporal_records([first, correction], valid_at=NOW.replace(hour=9), known_at=NOW.replace(hour=12))
    after = temporal_records.query_temporal_records([first, correction], valid_at=NOW.replace(hour=9), known_at=NOW.replace(hour=14))
    assert [item.revision for item in before] == []
    assert [item.revision for item in after] == ["r2"]


def test_temporal_query_handles_correction_chains_and_half_open_validity() -> None:
    first = _record(
        entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=10,
        valid_to_hour=12, revision="r1", recorded_hour=11,
    )
    second = _record(
        entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=9,
        revision="r2", supersedes=first.record_digest, recorded_hour=12,
    )
    third = _record(
        entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=8,
        revision="r3", supersedes=second.record_digest, recorded_hour=13,
    )
    assert [item.revision for item in temporal_records.query_temporal_records(
        [first, second, third], valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=11, minute=30)
    )] == ["r1"]
    assert [item.revision for item in temporal_records.query_temporal_records(
        [first, second, third], valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=12, minute=30)
    )] == ["r2"]
    assert [item.revision for item in temporal_records.query_temporal_records(
        [first, second, third], valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=13, minute=30)
    )] == ["r3"]
    assert temporal_records.query_temporal_records(
        [first], valid_at=NOW.replace(hour=12), known_at=NOW.replace(hour=12)
    ) == ()


def test_temporal_query_uses_ingested_time_and_validates_future_records() -> None:
    delayed = _record(
        entity="mug-7", schema="robotics.object_state_estimate.v1", valid_hour=9,
        revision="r1", recorded_hour=10, ingested_hour=13,
    )
    assert temporal_records.query_temporal_records(
        [delayed], valid_at=NOW.replace(hour=9), known_at=NOW.replace(hour=12)
    ) == ()
    assert [item.revision for item in temporal_records.query_temporal_records(
        [delayed], valid_at=NOW.replace(hour=9), known_at=NOW.replace(hour=13)
    )] == ["r1"]
    delayed.payload["value"] = "tampered"
    with pytest.raises(ValueError, match="mutated"):
        temporal_records.query_temporal_records(
            [delayed], valid_at=NOW.replace(hour=9), known_at=NOW.replace(hour=10)
        )
