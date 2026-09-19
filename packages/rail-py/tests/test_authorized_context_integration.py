from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from krail.provider.v1 import ResourceRef
from rail.authorized_context_integration import bootstrap_authorized_context_integration
from rail.hosted.access import AccessContextAuthority, MemoryRevocationRegistry


NOW = datetime(2026, 9, 7, 15, tzinfo=UTC)
PURPOSE = "shared grounded project task"
POLICY_DIGEST = "sha256:" + "a" * 64


def _fixture(tmp_path):
    current = [NOW]
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"core": b"caller-owned-integration-key"},
        issuer="https://core.example.test",
        revocations=revocations,
    )
    fixture = bootstrap_authorized_context_integration(
        tmp_path,
        authority=authority,
        revocations=revocations,
        tenant_id="tenant-a",
        project_id="project-a",
        key_id="core",
        clock=lambda: current[0],
    )
    return fixture, current


def test_real_provider_fixture_exposes_two_users_and_exact_core_source_bindings(tmp_path):
    fixture, _current = _fixture(tmp_path)
    request = fixture.context_request()
    alice = fixture.grant(
        subject="user/alice",
        exact_refs=fixture.alice_refs,
        context_request=request,
        purpose=PURPOSE,
        nonce="alice-wide",
        classifications=("internal", "confidential"),
        policy_digest=POLICY_DIGEST,
    )
    bob = fixture.grant(
        subject="user/bob",
        exact_refs=fixture.bob_refs,
        context_request=request,
        purpose=PURPOSE,
        nonce="bob-narrow",
        classifications=("internal",),
        policy_digest=POLICY_DIGEST,
    )

    alice_packet = fixture.provider.create_authorized_context_packet(
        alice.create_request
    )
    bob_packet = fixture.provider.create_authorized_context_packet(bob.create_request)

    assert alice.create_request.access_context.claims.subject == "user/alice"
    assert bob.create_request.access_context.claims.subject == "user/bob"
    assert "Private-Zebra-42" in alice_packet.model_dump_json()
    assert "Private-Zebra-42" not in bob_packet.model_dump_json()
    assert len(fixture.source_bindings) == 3
    assert {
        binding.core_source_id for binding in fixture.source_bindings.values()
    } == {"core-source/repository", "core-source/issue", "core-source/private"}
    for ref in fixture.refs:
        binding = fixture.source_bindings[ref.exact_key]
        assert (binding.tenant_id, binding.project_id) == ("tenant-a", "project-a")
        assert (binding.resource_id, binding.version, binding.digest) == (
            ref.resource_id,
            ref.version,
            ref.digest,
        )


@pytest.mark.parametrize(
    "source_ids",
    [
        {},
        {
            "docs/repository.md": "same",
            "topics/issue.md": "same",
            "sources/private.md": "private",
        },
        {
            "docs/repository.md": "repository",
            "topics/issue.md": "issue",
            "sources/private.md": "",
        },
    ],
)
def test_invalid_core_source_mapping_is_rejected_before_fixture_writes(
    tmp_path, source_ids
):
    target = tmp_path / "fixture"
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"core": b"caller-owned-integration-key"},
        issuer="https://core.example.test",
        revocations=revocations,
    )
    with pytest.raises(ValueError, match="non-empty, unique"):
        bootstrap_authorized_context_integration(
            target,
            authority=authority,
            revocations=revocations,
            tenant_id="tenant-a",
            project_id="project-a",
            key_id="core",
            clock=lambda: NOW,
            core_source_ids=source_ids,
        )
    assert not target.exists()


def test_retained_source_head_advance_and_revocation_deny_packet_without_leak(tmp_path):
    fixture, current = _fixture(tmp_path)
    request = fixture.context_request()
    alice = fixture.grant(
        subject="user/alice",
        exact_refs=fixture.alice_refs,
        context_request=request,
        purpose=PURPOSE,
        nonce="alice-original",
        classifications=("internal", "confidential"),
        policy_digest=POLICY_DIGEST,
    )
    packet = fixture.provider.create_authorized_context_packet(alice.create_request)
    assert fixture.provider.read_authorized_context_packet(
        fixture.read_request(packet, alice)
    ).status == "available"

    private_ref = fixture.refs[2]
    newer = b"new private source revision"
    current[0] += timedelta(minutes=5)
    fixture.set_current_head(
        private_ref.resource_id,
        ResourceRef(
            authority=private_ref.authority,
            resource_type=private_ref.resource_type,
            resource_id=private_ref.resource_id,
            version="content:" + hashlib.sha256(newer).hexdigest(),
            digest="sha256:" + hashlib.sha256(newer).hexdigest(),
        ),
    )
    denied = fixture.provider.read_authorized_context_packet(
        fixture.read_request(packet, alice)
    )
    assert denied.status == "context_packet_unavailable"
    assert denied.packet is None and denied.reauthorization is None
    assert "Private-Zebra-42" in fixture.read_retained_source(private_ref)

    fixture.set_current_head(private_ref.resource_id, private_ref)
    fresh = fixture.reauthorize_packet(
        packet,
        subject="user/bob",
        exact_refs=fixture.alice_refs,
        purpose=PURPOSE,
        nonce="bob-fresh-wide",
        classifications=("internal", "confidential"),
        policy_digest=POLICY_DIGEST,
    )
    assert fixture.provider.read_authorized_context_packet(
        fixture.read_request(packet, fresh)
    ).status == "available"
    fixture.revoke_grant(fresh)
    revoked = fixture.provider.read_authorized_context_packet(
        fixture.read_request(packet, fresh)
    )
    assert revoked.status == "context_packet_unavailable"
    assert revoked.model_dump(exclude={"schema_version", "status"}) == {
        "packet": None,
        "reauthorization": None,
    }
