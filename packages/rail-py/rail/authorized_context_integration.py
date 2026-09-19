"""Runnable local composition fixture for shared authorized context packets.

The caller supplies the authority, revocation control, tenant, project and live
clock.  The fixture only creates deterministic source material and routes calls
through the real ``Project.provider`` packet operations.  It is an integration
bootstrap, not an identity service or a parallel summary API.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from krail.provider.v1 import FindRequest, GetResourceRequest, ResourceRef
from rail.authorized_context import (
    AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
    AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
    AuthorizedContextPacketCreateRequest,
    AuthorizedContextPacketReadRequest,
    SharedAuthorizedContextPacket,
    authorized_context_packet_request_digest,
)
from rail.bootstrap import bootstrap_future_project
from rail.capability_publication import authorized_context_packet_descriptor
from rail.context_brief import ContextBriefRequest
from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    PacketRequestBinding,
    SignedAccessContext,
    SignedPacketRequestBinding,
)
from rail.local import LocalEngine
from rail.project import Project
from rail.hosted.models import DataClassification


class RevocationControl(Protocol):
    """Caller-owned mutation side of the external revocation registry."""

    def revoke_context(self, context_digest: str, *, revoked_at: datetime) -> None: ...

    def revoke_delegation(self, delegation_id: str, *, revoked_at: datetime) -> None: ...


@dataclass(frozen=True)
class FixtureGrant:
    subject: str
    exact_refs: tuple[ResourceRef, ...]
    create_request: AuthorizedContextPacketCreateRequest


@dataclass(frozen=True)
class FixtureReadGrant:
    subject: str
    exact_refs: tuple[ResourceRef, ...]
    access_context: SignedAccessContext
    request_binding: SignedPacketRequestBinding


@dataclass(frozen=True)
class CoreSourceBinding:
    """Explicit bridge to a caller-owned Core source grant and revision."""

    tenant_id: str
    project_id: str
    core_source_id: str
    resource_id: str
    version: str
    digest: str
    classification: str


@dataclass
class AuthorizedContextIntegrationFixture:
    """Core/Inspector acceptance fixture backed by one real local provider."""

    root: Path
    project: Project
    authority: AccessContextAuthority
    revocations: RevocationControl
    tenant_id: str
    project_id: str
    key_id: str
    clock: Callable[[], datetime]
    refs: tuple[ResourceRef, ResourceRef, ResourceRef]
    current_heads: dict[str, ResourceRef]
    source_bindings: dict[tuple[str, str, str, str, str], CoreSourceBinding]

    @property
    def provider(self):
        return self.project.provider

    @property
    def alice_refs(self) -> tuple[ResourceRef, ...]:
        return self.refs

    @property
    def bob_refs(self) -> tuple[ResourceRef, ...]:
        return self.refs[:2]

    def context_request(
        self,
        *,
        query: str = "NeedleWide",
        max_items: int = 8,
        max_total_bytes: int = 4096,
    ) -> ContextBriefRequest:
        return ContextBriefRequest(
            repository=self.refs[0],
            issue=self.refs[1],
            evaluated_at=self.clock(),
            query=query,
            max_items=max_items,
            max_total_bytes=max_total_bytes,
            authorization_omission=True,
        )

    def grant(
        self,
        *,
        subject: str,
        exact_refs: tuple[ResourceRef, ...],
        context_request: ContextBriefRequest,
        purpose: str,
        nonce: str,
        classifications: tuple[DataClassification, ...],
        policy_digest: str,
        expires_at: datetime | None = None,
        max_context_tokens: int = 32_768,
    ) -> FixtureGrant:
        """Ask the injected reference authority to issue one bounded fixture grant."""
        now = self.clock()
        descriptor = authorized_context_packet_descriptor()
        expiry = expires_at or now + timedelta(hours=1)
        claims = AccessClaims(
            issuer=self.authority.issuer,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            subject=subject,
            delegator=subject,
            delegation_id=f"delegation/{nonce}",
            capability_id=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
            capability_version=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
            capability_digest=descriptor.descriptor_digest,
            actions=("context.read",),
            source_ids=tuple(ref.resource_id for ref in exact_refs),
            classifications=classifications,
            policy_digest=policy_digest,
            issued_at=now - timedelta(minutes=2),
            not_before=now - timedelta(minutes=1),
            expires_at=expiry,
            nonce=f"context/{nonce}",
        )
        context = self.authority.issue(claims, key_id=self.key_id)
        request_digest = authorized_context_packet_request_digest(
            exact_refs=exact_refs,
            context_request=context_request,
            purpose=purpose,
            scope=self.project_id,
            max_context_tokens=max_context_tokens,
        )
        binding = self.authority.issue_packet_request_binding(
            PacketRequestBinding(
                access_context_digest=context.context_digest,
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                capability_id=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
                capability_version=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
                capability_digest=descriptor.descriptor_digest,
                request_digest=request_digest,
                purpose=purpose,
                scope=self.project_id,
                issued_at=now - timedelta(minutes=2),
                not_before=now - timedelta(minutes=1),
                expires_at=expiry,
                nonce=f"binding/{nonce}",
            ),
            key_id=self.key_id,
        )
        create_request = AuthorizedContextPacketCreateRequest(
            access_context=context,
            request_binding=binding,
            exact_refs=exact_refs,
            context_request=context_request,
            purpose=purpose,
            scope=self.project_id,
            max_context_tokens=max_context_tokens,
        )
        return FixtureGrant(
            subject=subject, exact_refs=exact_refs, create_request=create_request
        )

    def reauthorize_packet(
        self,
        packet: SharedAuthorizedContextPacket,
        *,
        subject: str,
        exact_refs: tuple[ResourceRef, ...],
        purpose: str,
        nonce: str,
        classifications: tuple[DataClassification, ...],
        policy_digest: str,
        expires_at: datetime | None = None,
    ) -> FixtureReadGrant:
        """Issue a fresh grant bound to the immutable packet request digest."""
        now = self.clock()
        descriptor = authorized_context_packet_descriptor()
        expiry = expires_at or now + timedelta(hours=1)
        claims = AccessClaims(
            issuer=self.authority.issuer,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            subject=subject,
            delegator=subject,
            delegation_id=f"delegation/{nonce}",
            capability_id=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
            capability_version=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
            capability_digest=descriptor.descriptor_digest,
            actions=("context.read",),
            source_ids=tuple(ref.resource_id for ref in exact_refs),
            classifications=classifications,
            policy_digest=policy_digest,
            issued_at=now - timedelta(minutes=2),
            not_before=now - timedelta(minutes=1),
            expires_at=expiry,
            nonce=f"context/{nonce}",
        )
        context = self.authority.issue(claims, key_id=self.key_id)
        binding = self.authority.issue_packet_request_binding(
            PacketRequestBinding(
                access_context_digest=context.context_digest,
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                capability_id=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
                capability_version=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
                capability_digest=descriptor.descriptor_digest,
                request_digest=packet.request_digest,
                purpose=purpose,
                scope=self.project_id,
                issued_at=now - timedelta(minutes=2),
                not_before=now - timedelta(minutes=1),
                expires_at=expiry,
                nonce=f"binding/{nonce}",
            ),
            key_id=self.key_id,
        )
        return FixtureReadGrant(
            subject=subject,
            exact_refs=exact_refs,
            access_context=context,
            request_binding=binding,
        )

    def read_request(
        self,
        packet: SharedAuthorizedContextPacket,
        grant: FixtureGrant | FixtureReadGrant,
    ) -> AuthorizedContextPacketReadRequest:
        if isinstance(grant, FixtureGrant):
            access_context = grant.create_request.access_context
            request_binding = grant.create_request.request_binding
        else:
            access_context = grant.access_context
            request_binding = grant.request_binding
        return AuthorizedContextPacketReadRequest(
            packet_digest=packet.packet_digest,
            access_context=access_context,
            request_binding=request_binding,
            exact_refs=grant.exact_refs,
            query=packet.context.evidence.query,
            purpose=packet.purpose,
            scope=self.project_id,
        )

    def set_current_head(self, resource_id: str, current_ref: ResourceRef) -> None:
        """Apply the caller/provider freshness decision without deleting old bytes."""
        if current_ref.resource_id != resource_id:
            raise ValueError("current source head must preserve resource identity")
        self.current_heads[resource_id] = current_ref

    def read_retained_source(self, ref: ResourceRef) -> str:
        """Read the exact retained source through provider-v1 for provenance proof."""
        return self.provider.get_resource(
            GetResourceRequest(ref=ref, max_bytes=1_048_576)
        ).resource.content

    def revoke_grant(self, grant: FixtureGrant | FixtureReadGrant) -> None:
        context = (
            grant.create_request.access_context
            if isinstance(grant, FixtureGrant)
            else grant.access_context
        )
        self.revocations.revoke_context(
            context.context_digest,
            revoked_at=self.clock(),
        )


def bootstrap_authorized_context_integration(
    target_dir: str | Path,
    *,
    authority: AccessContextAuthority,
    revocations: RevocationControl,
    tenant_id: str,
    project_id: str,
    key_id: str,
    clock: Callable[[], datetime],
    core_source_ids: Mapping[str, str] | None = None,
) -> AuthorizedContextIntegrationFixture:
    """Build a real provider fixture with Alice-wide and Bob-narrow source sets."""
    content = {
        "docs/repository.md": "# Repository\n\nShared packet architecture and public evidence.\n",
        "topics/issue.md": "# Issue\n\nBuild the shared packet from cited evidence.\n",
        "sources/private.md": "# Private\n\nNeedleWide evidence contains Private-Zebra-42.\n",
    }
    supplied_source_ids = dict(
        {
            "docs/repository.md": "core-source/repository",
            "topics/issue.md": "core-source/issue",
            "sources/private.md": "core-source/private",
        }
        if core_source_ids is None
        else core_source_ids
    )
    if (
        set(supplied_source_ids) != set(content)
        or any(
            not isinstance(source_id, str) or not source_id.strip()
            for source_id in supplied_source_ids.values()
        )
        or len(set(supplied_source_ids.values())) != len(content)
    ):
        raise ValueError(
            "Core source identities must be non-empty, unique, and cover every fixture resource exactly"
        )
    target = Path(target_dir).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("integration fixture target must be new or empty")
    root = bootstrap_future_project(
        target, name="Shared Packet Integration", slug="shared-packet-integration"
    )
    for relative, value in content.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    project = Project(
        slug="shared-packet-integration", backend=LocalEngine(project_path=root)
    )
    resource_types = {
        "docs/repository.md": "document",
        "topics/issue.md": "topic",
        "sources/private.md": "source",
    }
    refs: list[ResourceRef] = []
    for relative in content:
        result = project.provider.find(
            FindRequest(resource_type=resource_types[relative], identifiers=[relative])
        )
        if len(result.hits) != 1 or result.missing_identifiers:
            raise RuntimeError("integration fixture source discovery failed")
        refs.append(result.hits[0].ref)
    exact_refs = (refs[0], refs[1], refs[2])
    current_heads = {ref.resource_id: ref for ref in exact_refs}
    source_bindings = {
        ref.exact_key: CoreSourceBinding(
            tenant_id=tenant_id,
            project_id=project_id,
            core_source_id=supplied_source_ids[ref.resource_id],
            resource_id=ref.resource_id,
            version=ref.version,
            digest=ref.digest,
            classification="confidential"
            if ref.resource_id == "sources/private.md"
            else "internal",
        )
        for ref in exact_refs
    }
    project.configure_authorized_context_packets(
        authority,
        tenant_id=tenant_id,
        project_id=project_id,
        clock=clock,
        current_ref_resolver=lambda resource_id: current_heads[resource_id],
    )
    return AuthorizedContextIntegrationFixture(
        root=root,
        project=project,
        authority=authority,
        revocations=revocations,
        tenant_id=tenant_id,
        project_id=project_id,
        key_id=key_id,
        clock=clock,
        refs=exact_refs,
        current_heads=current_heads,
        source_bindings=source_bindings,
    )


__all__ = [
    "AuthorizedContextIntegrationFixture",
    "CoreSourceBinding",
    "FixtureGrant",
    "FixtureReadGrant",
    "RevocationControl",
    "bootstrap_authorized_context_integration",
]
