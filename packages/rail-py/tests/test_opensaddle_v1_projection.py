from __future__ import annotations

import hashlib

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from krail.provider.opensaddle_v1 import (
    AuthorizationProjection,
    DirectSourceBinding,
    ProjectionError,
    bindings_by_exact_key,
    canonical_json,
    project_direct_evidence_packet,
    project_direct_resource_ref,
)
from krail.provider.resources import PROVIDER_CONTRACT, resource_json
from krail.provider.v1 import EvidenceItem, EvidencePacket, ResourceRef


CONTENT = "exact direct evidence\n"
DIGEST = "sha256:" + hashlib.sha256(CONTENT.encode()).hexdigest()


def _ref(**changes: object) -> ResourceRef:
    values = {
        "authority": "https://knowledge.example.test/provider",
        "resource_id": "docs/evidence",
        "resource_type": "document",
        "version": "git:8e4a1f0",
        "digest": DIGEST,
    }
    values.update(changes)
    return ResourceRef.model_validate(values)


def _binding(ref: ResourceRef, **changes: object) -> DirectSourceBinding:
    values = {
        "source_id": "source/evidence",
        "origin": "git:https://example.test/acme/repository.git",
        "version": ref.version,
        "digest": ref.digest,
    }
    values.update(changes)
    return DirectSourceBinding(**values)


def _packet(ref: ResourceRef, **changes: object) -> EvidencePacket:
    values = {
        "packet_id": "packet/direct-0001",
        "query": "exact evidence",
        "generated_at": "2026-08-07T15:30:00Z",
        "items": [
            EvidenceItem(
                source=ref,
                locator="docs/evidence#utf8:0-22",
                excerpt=CONTENT,
            )
        ],
    }
    values.update(changes)
    return EvidencePacket(**values)


def test_direct_resource_ref_projection_preserves_exact_identity_and_provenance() -> None:
    ref = _ref()
    projected = project_direct_resource_ref(ref, _binding(ref))

    assert projected["issuer"] == ref.authority
    assert projected["version"] == ref.version
    assert projected["digest"] == {"algorithm": "sha-256", "value": DIGEST[7:]}
    assert projected["source"]["version"] == ref.version
    assert projected["source"]["digest"] == projected["digest"]
    assert "contract" not in projected


def test_direct_evidence_projection_validates_normative_schema_and_digest_rules() -> None:
    ref = _ref()
    projected = project_direct_evidence_packet(
        _packet(ref),
        source_bindings=bindings_by_exact_key(((ref, _binding(ref)),)),
        authorization=AuthorizationProjection(authorized=True),
    )
    protocol = resource_json(PROVIDER_CONTRACT, "schemas/protocol.schema.json")
    validator = Draft202012Validator(
        {"$ref": "#/$defs/EvidencePacket", "$defs": protocol["$defs"]},
        format_checker=FormatChecker(),
    )

    validator.validate(projected)
    citation = projected["results"][0]["citations"][0]
    assert citation["resource"] == projected["results"][0]["resource"]
    assert citation["excerpt_digest"]["value"] == hashlib.sha256(
        citation["content"].encode()
    ).hexdigest()
    record = {key: value for key, value in citation.items() if key != "record_digest"}
    from krail.provider.opensaddle_v1 import canonical_json

    assert citation["record_digest"]["value"] == hashlib.sha256(
        canonical_json(record)
    ).hexdigest()


@pytest.mark.parametrize(
    ("packet_changes", "bindings", "authorization", "message"),
    [
        ({}, {}, AuthorizationProjection(authorized=True), "explicit binding"),
        ({"truncated": True}, None, AuthorizationProjection(authorized=True), "truncated"),
        ({"packet_id": "invalid packet id"}, None, AuthorizationProjection(authorized=True), "packet_id"),
        ({}, None, AuthorizationProjection(authorized=False), "unauthorized"),
    ],
)
def test_evidence_projection_fails_closed(
    packet_changes: dict[str, object],
    bindings: dict | None,
    authorization: AuthorizationProjection,
    message: str,
) -> None:
    ref = _ref()
    selected = bindings if bindings is not None else bindings_by_exact_key(((ref, _binding(ref)),))
    with pytest.raises(ProjectionError, match=message):
        project_direct_evidence_packet(
            _packet(ref, **packet_changes),
            source_bindings=selected,
            authorization=authorization,
        )


def test_projection_rejects_fabricated_or_out_of_bounds_source_provenance() -> None:
    ref = _ref()
    with pytest.raises(ProjectionError, match="match the exact"):
        project_direct_resource_ref(ref, _binding(ref, version="git:different"))
    with pytest.raises(ProjectionError, match="origin"):
        _binding(ref, origin="https://example.test/" + "x" * 920)


def test_projection_rejects_locator_offsets_outside_ijson_safe_integer_range() -> None:
    ref = _ref()
    packet = _packet(
        ref,
        items=[
            _packet(ref).items[0].model_copy(
                update={
                    "locator": (
                        "docs/evidence#utf8:9007199254740991-9007199254740992"
                    )
                }
            )
        ],
    )

    with pytest.raises(ProjectionError, match="byte span"):
        project_direct_evidence_packet(
            packet,
            source_bindings=bindings_by_exact_key(((ref, _binding(ref)),)),
            authorization=AuthorizationProjection(authorized=True),
        )


def test_projection_digest_canonicalization_is_rfc8785_and_rejects_unsafe_integers() -> None:
    assert canonical_json({"z": 1.0, "é": "value", "a": True}) == (
        b'{"a":true,"z":1,"\xc3\xa9":"value"}'
    )
    with pytest.raises(ProjectionError, match="RFC 8785"):
        canonical_json({"unsafe": 9_007_199_254_740_992})


def test_projection_rejects_duplicate_evidence_records() -> None:
    ref = _ref()
    first = _packet(ref).items[0]
    packet = _packet(ref, items=[first, first])

    with pytest.raises(ProjectionError, match="duplicate evidence records"):
        project_direct_evidence_packet(
            packet,
            source_bindings=bindings_by_exact_key(((ref, _binding(ref)),)),
            authorization=AuthorizationProjection(authorized=True),
        )


def test_projection_accepts_supported_text_media_and_binds_unprojected_metadata() -> None:
    ref = _ref()
    plain = project_direct_evidence_packet(
        _packet(ref),
        source_bindings=bindings_by_exact_key(((ref, _binding(ref)),)),
        authorization=AuthorizationProjection(authorized=True),
    )["results"][0]["citations"][0]
    rich_item = _packet(ref).items[0].model_copy(
        update={"media_type": "text/markdown", "relevance": 0.5}
    )
    rich = project_direct_evidence_packet(
        _packet(ref, items=[rich_item]),
        source_bindings=bindings_by_exact_key(((ref, _binding(ref)),)),
        authorization=AuthorizationProjection(authorized=True),
    )["results"][0]["citations"][0]

    assert rich["content"] == plain["content"]
    assert "media_type" not in rich and "relevance" not in rich
    assert rich["evidence_id"] != plain["evidence_id"]
    assert rich["record_digest"] != plain["record_digest"]


def test_projection_rejects_untyped_or_binary_media() -> None:
    ref = _ref()
    item = _packet(ref).items[0].model_copy(update={"media_type": "application/json"})

    with pytest.raises(ProjectionError, match="supported UTF-8 text"):
        project_direct_evidence_packet(
            _packet(ref, items=[item]),
            source_bindings=bindings_by_exact_key(((ref, _binding(ref)),)),
            authorization=AuthorizationProjection(authorized=True),
        )


def test_projection_preserves_contiguous_citation_order_and_rejects_interleaving() -> None:
    first_ref = _ref()
    second_ref = _ref(resource_id="docs/second")
    first = _packet(first_ref).items[0]
    first_later = first.model_copy(update={"locator": "section-two"})
    second = EvidenceItem(
        source=second_ref,
        locator="docs/second#utf8:0-22",
        excerpt=CONTENT,
    )
    bindings = bindings_by_exact_key(
        ((first_ref, _binding(first_ref)), (second_ref, _binding(second_ref)))
    )
    contiguous = project_direct_evidence_packet(
        _packet(first_ref, items=[first, first_later, second]),
        source_bindings=bindings,
        authorization=AuthorizationProjection(authorized=True),
    )

    assert [citation["locator"] for result in contiguous["results"] for citation in result["citations"]] == [
        {"kind": "span", "unit": "bytes", "start": 0, "end": 22},
        {"kind": "fragment", "value": "section-two"},
        {"kind": "span", "unit": "bytes", "start": 0, "end": 22},
    ]
    with pytest.raises(ProjectionError, match="interleaved"):
        project_direct_evidence_packet(
            _packet(first_ref, items=[first, second, first_later]),
            source_bindings=bindings,
            authorization=AuthorizationProjection(authorized=True),
        )
