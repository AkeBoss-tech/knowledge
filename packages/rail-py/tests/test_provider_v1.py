from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from krail.provider.v1 import (
    CONTRACT_ID,
    MAX_EVIDENCE_CONTENT_BYTES,
    MAX_EVIDENCE_ITEM_BYTES,
    MAX_EVIDENCE_ITEMS,
    MAX_EVIDENCE_PACKET_BYTES,
    DescribeTypesRequest,
    DescribeTypesResult,
    EvidenceItem,
    EvidencePacket,
    ExplainRequest,
    ExplainResult,
    FindRequest,
    FindResult,
    GetResourceRequest,
    GetResourceResult,
    IntegrityRequest,
    IntegrityResult,
    LineageRequest,
    LineageResult,
    ProviderInfoRequest,
    ProviderInfoResult,
    ResourceRef,
    ResourcePayload,
    RetrieveEvidenceRequest,
    RetrieveEvidenceResult,
    SearchRequest,
    SearchResult,
)


FIXTURES = Path(__file__).parent / "fixtures" / "provider_v1"
DIGEST = "sha256:" + "a" * 64


def ref(**overrides: object) -> ResourceRef:
    values = {
        "authority": "git+file:///workspace/project",
        "resource_type": "topic",
        "resource_id": "topics/contract.md",
        "version": "git:5e79f760286edece91cf3f99c90f83e9d731d67f",
        "digest": DIGEST,
    }
    values.update(overrides)
    return ResourceRef.model_validate(values)


def item(excerpt: str = "bounded evidence") -> EvidenceItem:
    return EvidenceItem(source=ref(), locator="lines:1-2", excerpt=excerpt)


def packet(items: list[EvidenceItem] | None = None) -> EvidencePacket:
    return EvidencePacket(
        packet_id="packet:test",
        query="test query",
        generated_at="2026-08-07T12:00:00Z",
        items=items or [item()],
    )


def test_resource_ref_has_stable_identity_and_exact_version_key() -> None:
    resource = ref()

    assert resource.identity == (
        "git+file:///workspace/project",
        "topic",
        "topics/contract.md",
    )
    assert resource.exact_key == (*resource.identity, resource.version, DIGEST)
    assert resource.model_dump()["contract"] == CONTRACT_ID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authority", "workspace/project"),
        ("authority", "git+file:///project?branch=main"),
        ("authority", "git+file:///project#fragment"),
        ("resource_type", "Topic Page"),
        ("resource_id", "\n"),
        ("version", "\x00revision"),
        ("version", "latest"),
        ("digest", "a" * 64),
        ("digest", "sha256:" + "z" * 64),
    ],
)
def test_resource_ref_rejects_ambiguous_or_unverifiable_identity(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ref(**{field: value})


def test_fixtures_are_executable_contract_examples() -> None:
    valid = json.loads((FIXTURES / "evidence_packet.json").read_text(encoding="utf-8"))
    invalid = json.loads((FIXTURES / "invalid_unqualified_ref.json").read_text(encoding="utf-8"))
    provider_info = json.loads((FIXTURES / "provider_info.json").read_text(encoding="utf-8"))

    assert EvidencePacket.model_validate(valid).items[0].source.version.startswith("git:")
    assert ProviderInfoResult.model_validate(provider_info).capabilities[-1] == "integrity"
    with pytest.raises(ValidationError):
        ResourceRef.model_validate(invalid)


def test_complete_resource_payload_verifies_digest() -> None:
    content = "exact source bytes"
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    source = ref(digest=digest)

    assert source.verifies(content)
    assert ResourcePayload(ref=source, media_type="text/plain", content=content).content == content
    with pytest.raises(ValidationError, match="does not match"):
        ResourcePayload(ref=source, media_type="text/plain", content="changed")


def test_evidence_item_limit_counts_utf8_bytes() -> None:
    item("é" * (MAX_EVIDENCE_ITEM_BYTES // 2))

    with pytest.raises(ValidationError, match="UTF-8 bytes"):
        item("é" * (MAX_EVIDENCE_ITEM_BYTES // 2 + 1))


def test_evidence_packet_requires_unambiguous_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        EvidencePacket(
            packet_id="packet:test",
            query="test query",
            generated_at="2026-08-07T12:00:00",
            items=[item()],
        )


def test_evidence_packet_enforces_cardinality_and_total_payload() -> None:
    with pytest.raises(ValidationError):
        packet([item(str(index)) for index in range(MAX_EVIDENCE_ITEMS + 1)])

    excerpt = "x" * MAX_EVIDENCE_ITEM_BYTES
    with pytest.raises(ValidationError, match="combined evidence excerpts"):
        packet([item(excerpt) for _ in range(MAX_EVIDENCE_CONTENT_BYTES // MAX_EVIDENCE_ITEM_BYTES + 1)])


def test_evidence_packet_bounds_serialized_reference_overhead() -> None:
    large_ref = ref(
        authority="git+file:///" + "a" * 500,
        resource_id="r" * 2048,
        version="v" * 512,
    )
    large_item = EvidenceItem(
        source=large_ref,
        locator="l" * 2048,
        excerpt="x" * (MAX_EVIDENCE_CONTENT_BYTES // MAX_EVIDENCE_ITEMS),
    )

    with pytest.raises(ValidationError, match="serialized evidence packet"):
        packet([large_item for _ in range(MAX_EVIDENCE_ITEMS)])

    assert MAX_EVIDENCE_PACKET_BYTES > MAX_EVIDENCE_CONTENT_BYTES


def test_every_evidence_item_requires_an_exact_resource_ref() -> None:
    payload = item().model_dump(mode="json")
    del payload["source"]["version"]

    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(payload)


@pytest.mark.parametrize(
    "model",
    [
        DescribeTypesRequest,
        DescribeTypesResult,
        SearchRequest,
        SearchResult,
        FindRequest,
        FindResult,
        GetResourceRequest,
        GetResourceResult,
        RetrieveEvidenceRequest,
        RetrieveEvidenceResult,
        ExplainRequest,
        ExplainResult,
        LineageRequest,
        LineageResult,
        IntegrityRequest,
        IntegrityResult,
        ProviderInfoRequest,
        ProviderInfoResult,
    ],
)
def test_every_operation_shape_exposes_versioned_json_schema(model: type) -> None:
    schema = model.model_json_schema()

    assert schema["properties"]["contract"]["const"] == CONTRACT_ID
    assert schema["additionalProperties"] is False


def test_request_limits_are_enforced() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="query", limit=101)
    with pytest.raises(ValidationError):
        RetrieveEvidenceRequest(query="query", max_items=MAX_EVIDENCE_ITEMS + 1)
    with pytest.raises(ValidationError):
        LineageRequest(ref=ref(), max_nodes=201)
    with pytest.raises(ValidationError):
        IntegrityRequest(refs=[ref()], max_findings=201)


def test_contract_import_has_no_runtime_or_hosted_dependency() -> None:
    import krail.provider.v1 as provider_v1

    source = Path(provider_v1.__file__).read_text(encoding="utf-8")
    assert "from rail" not in source
    assert "import rail" not in source
    assert "opensaddle" not in source.lower()
