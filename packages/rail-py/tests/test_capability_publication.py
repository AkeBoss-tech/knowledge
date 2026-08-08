from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from krail.provider.capabilities import CapabilityDescriptor, CapabilityNegotiationRequest
from rail.bootstrap import bootstrap_future_project
from rail.capability_publication import CAPABILITY_ID, context_brief_descriptor
from rail.local import LocalEngine
from rail.project import Project


def _project(tmp_path: Path) -> Project:
    root = bootstrap_future_project(tmp_path, name="Capability Project", slug="capability-project")
    return Project(slug="capability-project", backend=LocalEngine(project_path=root))


def test_descriptor_is_stable_digest_addressed_read_only_and_bounded() -> None:
    first = context_brief_descriptor()
    second = context_brief_descriptor()

    assert first == second
    assert first.capability_id == CAPABILITY_ID
    assert first.semantic_version == "1.0.0"
    assert first.descriptor_digest.startswith("sha256:")
    assert {item.operation_id for item in first.operations} == {"retrieve_evidence", "context_brief"}
    assert all(item.input_schema["additionalProperties"] is False for item in first.operations)
    assert all(item.output_schema["additionalProperties"] is False for item in first.operations)
    assert first.effects.classification == "read-only"
    assert first.effects.external_effects is False
    assert first.authorization.descriptor_grants_authorization is False
    assert {item.component for item in first.semantic_processing_versions} >= {
        "provider-contract", "context-brief", "ranking", "freshness", "conflict"
    }
    assert {item.name for item in first.limits} >= {
        "max_context_items", "max_evidence_items", "max_evidence_content_bytes", "max_evidence_packet_bytes"
    }


def test_descriptor_rejects_content_mutation_under_an_old_digest() -> None:
    descriptor = context_brief_descriptor()
    changed = descriptor.model_dump(mode="json")
    changed["semantic_version"] = "1.0.1"

    with pytest.raises(ValidationError, match="digest does not match"):
        CapabilityDescriptor.model_validate(changed)


def test_provider_publication_negotiates_version_and_digest_without_authorizing(tmp_path: Path) -> None:
    provider = _project(tmp_path).provider
    descriptor = provider.capability_descriptor()
    accepted = provider.negotiate_capability(
        CapabilityNegotiationRequest(
            capability_id=CAPABILITY_ID,
            consumer_version="1.8.0",
            descriptor_digest=descriptor.descriptor_digest,
        )
    )
    wrong_major = provider.negotiate_capability(
        CapabilityNegotiationRequest(capability_id=CAPABILITY_ID, consumer_version="2.0.0")
    )
    wrong_digest = provider.negotiate_capability(
        CapabilityNegotiationRequest(
            capability_id=CAPABILITY_ID,
            consumer_version="1.0.0",
            descriptor_digest="sha256:" + "0" * 64,
        )
    )

    assert accepted.compatible is True
    assert accepted.descriptor == descriptor
    assert accepted.descriptor.authorization.descriptor_grants_authorization is False
    assert wrong_major.compatible is False
    assert wrong_digest.compatible is False


def test_public_capability_contract_contains_no_host_or_execution_dependency() -> None:
    import krail.provider.capabilities as capability_contract

    source = Path(capability_contract.__file__).read_text(encoding="utf-8").lower()
    assert "from rail" not in source
    assert "import rail" not in source
    assert "opensaddle" not in source
