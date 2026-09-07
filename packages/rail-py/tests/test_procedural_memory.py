from datetime import UTC, datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from krail.provider.v1 import ResourceRef
from rail.procedural_memory import (
    authorize_procedure,
    create_procedure,
    invalidate_for_dependency,
    procedure_temporal_record,
    procedure_temporal_history,
    replay_procedure_history,
    supersede,
    verify_procedure_integrity,
)
from rail.temporal_records import query_temporal_records


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _ref(resource_id: str, version: str = "git:abc") -> ResourceRef:
    content = f"{resource_id}:{version}".encode()
    return ResourceRef(
        authority="git+https://example.test/project",
        resource_type="artifact",
        resource_id=resource_id,
        version=version,
        digest="sha256:" + sha256(content).hexdigest(),
    )


def _procedure(**changes):
    values = {
        "procedure_id": "environment/researcher",
        "procedure_version": "1.0.0",
        "lifecycle": "reviewed",
        "authority": "krail://project/example",
        "writer_family": "human-reviewed",
        "valid_from": NOW,
        "recorded_at": NOW,
        "package_refs": (_ref("packages/researcher"),),
        "command_refs": (_ref("commands/review"),),
        "environment_refs": (_ref("environments/mac"),),
        "test_evidence_refs": (_ref("tests/researcher"),),
        "dependency_refs": (_ref("sources/method"),),
        "rationale": "Keep cited research review reproducible.",
    }
    values.update(changes)
    if values["lifecycle"] == "reviewed":
        values.setdefault("review_ref", _ref("reviews/bootstrap"))
    return create_procedure(**values)


def test_procedure_binds_exact_revisions_and_digest() -> None:
    record = _procedure()
    assert record.record_digest.startswith("sha256:")
    assert record.lifecycle == "reviewed"
    assert record.package_refs[0].version == "git:abc"
    temporal = procedure_temporal_record(record)
    assert temporal.payload_schema == "krail.procedure-memory.v1"
    assert temporal.kind == "approved_state"
    assert {item.exact_key for item in temporal.source_refs} == {
        item.exact_key
        for item in record.package_refs + record.command_refs + record.environment_refs + record.test_evidence_refs + record.dependency_refs
    }


def test_desired_and_observed_activation_are_distinct() -> None:
    desired = _procedure(lifecycle="desired", test_evidence_refs=())
    assert desired.activation_ref is None
    with pytest.raises(ValueError, match="observed activations"):
        _procedure(lifecycle="observed_activation", test_evidence_refs=())


def test_dependency_invalidation_is_selective_and_stable() -> None:
    record = _procedure()
    unrelated = invalidate_for_dependency(record, _ref("sources/other"))
    assert unrelated == record
    stale = invalidate_for_dependency(record, record.dependency_refs[0], reason="method revision changed")
    assert stale.freshness == "stale"
    assert stale.stale_reasons == ("method revision changed",)
    assert invalidate_for_dependency(stale, record.dependency_refs[0]) == stale


def test_supersession_requires_exact_prior_digest_and_identity() -> None:
    previous = _procedure()
    replacement = _procedure(procedure_version="1.1.0", supersedes_digest=previous.record_digest)
    assert supersede(previous, replacement) == replacement
    with pytest.raises(ValueError, match="exact prior"):
        supersede(previous, _procedure(procedure_version="1.2.0"))
    with pytest.raises(ValueError, match="same procedure identity"):
        supersede(previous, _procedure(procedure_id="environment/other", supersedes_digest=previous.record_digest))
    with pytest.raises(ValueError, match="authority or writer"):
        supersede(
            previous,
            _procedure(
                procedure_version="1.1.0",
                authority="krail://other",
                supersedes_digest=previous.record_digest,
            ),
        )


def test_invalid_time_and_duplicate_lineage_are_rejected() -> None:
    with pytest.raises(ValueError, match="valid_to"):
        _procedure(valid_to=NOW.replace(year=2025))
    with pytest.raises(ValueError, match="unique exact"):
        _procedure(dependency_refs=(_ref("sources/method"), _ref("sources/method")))


def test_out_of_order_history_replay_is_deterministic_and_checks_supersession() -> None:
    previous = _procedure(valid_from=NOW, recorded_at=NOW)
    replacement = _procedure(
        procedure_version="1.1.0",
        valid_from=NOW.replace(hour=13),
        recorded_at=NOW.replace(hour=14),
        supersedes_digest=previous.record_digest,
    )
    assert replay_procedure_history([replacement, previous]) == (previous, replacement)
    with pytest.raises(ValueError, match="supplied exact revision"):
        replay_procedure_history([_procedure(procedure_version="1.2.0", supersedes_digest="sha256:" + "f" * 64)])


def test_procedure_history_composes_into_generic_temporal_query() -> None:
    previous = _procedure(valid_from=NOW.replace(hour=10), recorded_at=NOW.replace(hour=11))
    replacement = _procedure(
        procedure_version="1.1.0",
        valid_from=NOW.replace(hour=10),
        recorded_at=NOW.replace(hour=13),
        supersedes_digest=previous.record_digest,
    )
    with pytest.raises(ValueError, match="procedure_temporal_history"):
        procedure_temporal_record(replacement)
    temporal = procedure_temporal_history([replacement, previous])
    assert [item.revision for item in temporal] == ["1.0.0", "1.1.0"]
    assert {item.payload_schema_version for item in temporal} == {"1.0.0"}
    assert temporal[1].supersedes_digest == temporal[0].record_digest
    before = query_temporal_records(temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=12))
    after = query_temporal_records(temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=14))
    assert [item.revision for item in before] == ["1.0.0"]
    assert [item.revision for item in after] == ["1.1.0"]


def test_procedure_history_resolves_three_revision_correction_chain() -> None:
    first = _procedure(valid_from=NOW.replace(hour=10), recorded_at=NOW.replace(hour=11))
    second = _procedure(
        procedure_version="1.1.0",
        valid_from=NOW.replace(hour=9),
        recorded_at=NOW.replace(hour=13),
        supersedes_digest=first.record_digest,
    )
    third = _procedure(
        procedure_version="1.2.0",
        valid_from=NOW.replace(hour=8),
        recorded_at=NOW.replace(hour=15),
        supersedes_digest=second.record_digest,
    )
    temporal = procedure_temporal_history([third, first, second])
    assert [item.revision for item in query_temporal_records(
        temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=12)
    )] == ["1.0.0"]
    assert [item.revision for item in query_temporal_records(
        temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=14)
    )] == ["1.1.0"]
    assert [item.revision for item in query_temporal_records(
        temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=16)
    )] == ["1.2.0"]


def test_replay_rejects_conflicting_authority_or_same_version() -> None:
    first = _procedure()
    with pytest.raises(ValueError, match="mix identities"):
        replay_procedure_history([first, _procedure(authority="krail://other")])
    with pytest.raises(ValueError, match="conflicting revisions"):
        replay_procedure_history([first, _procedure(procedure_version=first.procedure_version, rationale="different")])


def test_procedure_integrity_boundary_detects_mutation() -> None:
    record = _procedure()
    with pytest.raises(ValidationError, match="frozen"):
        record.stale_reasons += ("tampered",)
    assert verify_procedure_integrity(record) is record


class _Authorizer:
    def __init__(self, denied: ResourceRef | None = None):
        self.denied = denied
        self.seen: list[ResourceRef] = []

    def authorize(self, ref: ResourceRef, *, at=None) -> None:
        self.seen.append(ref)
        if self.denied is not None and ref.exact_key == self.denied.exact_key:
            raise PermissionError("hidden reason")


def test_all_procedure_refs_are_authorized_before_metadata_exposure() -> None:
    record = _procedure()
    authorizer = _Authorizer()
    assert authorize_procedure(record, authorizer) is record
    assert {ref.exact_key for ref in authorizer.seen} == {
        ref.exact_key
        for ref in record.package_refs + record.command_refs + record.environment_refs + record.test_evidence_refs + record.dependency_refs
    } | {record.review_ref.exact_key}
    denied = _Authorizer(record.environment_refs[0])
    with pytest.raises(PermissionError, match="procedure access denied") as error:
        authorize_procedure(record, denied)
    assert "environments/mac" not in str(error.value)
    assert "Keep cited" not in str(error.value)
