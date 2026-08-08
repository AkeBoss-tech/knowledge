"""Implementation-independent ``krail.provider.v1`` read contract.

Providers implement these request and result shapes. Consumers must not infer a
filesystem, database, API, or KRAIL runtime implementation from them.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


CONTRACT_ID = "krail.provider.v1"
MAX_SEARCH_RESULTS = 100
MAX_FIND_RESULTS = 100
MAX_RESOURCE_BYTES = 1_048_576
MAX_PREVIEW_BYTES = 4_096
MAX_EVIDENCE_ITEMS = 32
MAX_EVIDENCE_ITEM_BYTES = 16_384
MAX_EVIDENCE_CONTENT_BYTES = 131_072
MAX_EVIDENCE_PACKET_BYTES = 262_144
MAX_LINEAGE_NODES = 200
MAX_LINEAGE_EDGES = 400
MAX_INTEGRITY_FINDINGS = 200

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ResourceType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9._-]*$",
    ),
]


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


class ContractModel(BaseModel):
    """Strict base for every provider-v1 wire model."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    contract: Literal["krail.provider.v1"] = CONTRACT_ID


ProviderCapability = Literal[
    "describe_types",
    "search",
    "find",
    "get_resource",
    "retrieve_evidence",
    "explain",
    "lineage",
    "integrity",
]


class ProviderInfoRequest(ContractModel):
    """Negotiate the provider contract without depending on its implementation."""

    consumer_version: Annotated[str, StringConstraints(max_length=128)] | None = None


class ProviderInfoResult(ContractModel):
    provider: NonEmpty
    provider_version: NonEmpty
    installed_distribution_version: NonEmpty
    capabilities: list[ProviderCapability] = Field(max_length=8)
    compatible: bool = True
    diagnostic: Annotated[str, StringConstraints(max_length=4096)] = ""


class ResourceRef(ContractModel):
    """Stable reference to one exact version of one authority-owned resource.

    ``authority`` and ``resource_id`` form the authority-qualified identity;
    ``version`` selects an immutable source revision and ``digest`` verifies the
    bytes (or canonical serialized value) returned for that revision.
    """

    authority: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=3, max_length=512),
    ]
    resource_type: ResourceType
    resource_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    ]
    version: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
    ]
    digest: Annotated[
        str,
        StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$"),
    ]

    @field_validator("authority")
    @classmethod
    def _authority_is_qualified(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9+.-]*:[^\s?#]+", value):
            raise ValueError("authority must be an absolute, query-free and fragment-free URI")
        return value

    @field_validator("resource_id", "version")
    @classmethod
    def _opaque_values_are_stable(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("value may not contain control characters")
        return value

    @field_validator("version")
    @classmethod
    def _version_is_immutable(cls, value: str) -> str:
        if value.lower() in {"latest", "current", "head", "working-tree"}:
            raise ValueError("version must be an immutable revision, not a moving label")
        return value

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.authority, self.resource_type, self.resource_id)

    @property
    def exact_key(self) -> tuple[str, str, str, str, str]:
        return (*self.identity, self.version, self.digest)

    def verifies(self, content: str | bytes) -> bool:
        payload = content.encode("utf-8") if isinstance(content, str) else content
        return self.digest == f"sha256:{hashlib.sha256(payload).hexdigest()}"


class EvidenceItem(ContractModel):
    source: ResourceRef
    locator: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    ]
    excerpt: str
    media_type: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ] = "text/plain"
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("excerpt")
    @classmethod
    def _bounded_excerpt(cls, value: str) -> str:
        size = _utf8_size(value)
        if not value or size > MAX_EVIDENCE_ITEM_BYTES:
            raise ValueError(f"excerpt must contain 1..{MAX_EVIDENCE_ITEM_BYTES} UTF-8 bytes")
        return value


class EvidencePacket(ContractModel):
    packet_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
    ]
    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=8192),
    ]
    generated_at: datetime
    items: list[EvidenceItem] = Field(min_length=1, max_length=MAX_EVIDENCE_ITEMS)
    truncated: bool = False

    @field_validator("generated_at")
    @classmethod
    def _timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def _bounded_payload(self) -> "EvidencePacket":
        total = sum(_utf8_size(item.excerpt) for item in self.items)
        if total > MAX_EVIDENCE_CONTENT_BYTES:
            raise ValueError(
                f"combined evidence excerpts exceed {MAX_EVIDENCE_CONTENT_BYTES} UTF-8 bytes"
            )
        serialized_size = _utf8_size(self.model_dump_json())
        if serialized_size > MAX_EVIDENCE_PACKET_BYTES:
            raise ValueError(
                f"serialized evidence packet exceeds {MAX_EVIDENCE_PACKET_BYTES} UTF-8 bytes"
            )
        return self


class ResourceTypeDescriptor(ContractModel):
    resource_type: ResourceType
    title: NonEmpty
    description: Annotated[str, StringConstraints(max_length=4096)] = ""
    media_types: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=32
    )
    searchable: bool = True


class DescribeTypesRequest(ContractModel):
    pass


class DescribeTypesResult(ContractModel):
    types: list[ResourceTypeDescriptor] = Field(max_length=256)


class SearchRequest(ContractModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
    resource_types: list[ResourceType] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=20, ge=1, le=MAX_SEARCH_RESULTS)
    cursor: Annotated[str, StringConstraints(max_length=2048)] | None = None


class FindRequest(ContractModel):
    resource_type: ResourceType
    identifiers: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]] = Field(
        min_length=1, max_length=MAX_FIND_RESULTS
    )


class SearchHit(ContractModel):
    ref: ResourceRef
    title: Annotated[str, StringConstraints(max_length=1024)] = ""
    preview: str = ""
    score: float | None = Field(default=None, ge=0.0)

    @field_validator("preview")
    @classmethod
    def _bounded_preview(cls, value: str) -> str:
        if _utf8_size(value) > MAX_PREVIEW_BYTES:
            raise ValueError(f"preview exceeds {MAX_PREVIEW_BYTES} UTF-8 bytes")
        return value


class SearchResult(ContractModel):
    hits: list[SearchHit] = Field(max_length=MAX_SEARCH_RESULTS)
    next_cursor: Annotated[str, StringConstraints(max_length=2048)] | None = None
    truncated: bool = False


class FindResult(ContractModel):
    hits: list[SearchHit] = Field(max_length=MAX_FIND_RESULTS)
    missing_identifiers: list[Annotated[str, StringConstraints(max_length=2048)]] = Field(
        default_factory=list, max_length=MAX_FIND_RESULTS
    )


class GetResourceRequest(ContractModel):
    ref: ResourceRef
    max_bytes: int = Field(default=MAX_RESOURCE_BYTES, ge=1, le=MAX_RESOURCE_BYTES)


class ResourcePayload(ContractModel):
    ref: ResourceRef
    media_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    content: str
    truncated: bool = False

    @field_validator("content")
    @classmethod
    def _bounded_content(cls, value: str) -> str:
        if _utf8_size(value) > MAX_RESOURCE_BYTES:
            raise ValueError(f"content exceeds {MAX_RESOURCE_BYTES} UTF-8 bytes")
        return value

    @model_validator(mode="after")
    def _verify_complete_content(self) -> "ResourcePayload":
        if not self.truncated and not self.ref.verifies(self.content):
            raise ValueError("complete resource content does not match the ResourceRef digest")
        return self


class GetResourceResult(ContractModel):
    resource: ResourcePayload


class RetrieveEvidenceRequest(ContractModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
    resource_types: list[ResourceType] = Field(default_factory=list, max_length=32)
    max_items: int = Field(default=12, ge=1, le=MAX_EVIDENCE_ITEMS)
    max_total_bytes: int = Field(default=MAX_EVIDENCE_CONTENT_BYTES, ge=1, le=MAX_EVIDENCE_CONTENT_BYTES)


class RetrieveEvidenceResult(ContractModel):
    evidence: EvidencePacket


class ExplainRequest(ContractModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
    refs: list[ResourceRef] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)
    max_evidence_items: int = Field(default=12, ge=1, le=MAX_EVIDENCE_ITEMS)


class ExplainResult(ContractModel):
    explanation: Annotated[str, StringConstraints(max_length=65_536)]
    evidence: EvidencePacket


class LineageRequest(ContractModel):
    ref: ResourceRef
    max_depth: int = Field(default=3, ge=0, le=16)
    max_nodes: int = Field(default=100, ge=1, le=MAX_LINEAGE_NODES)


class LineageEdge(ContractModel):
    source: ResourceRef
    target: ResourceRef
    relation: ResourceType


class LineageResult(ContractModel):
    root: ResourceRef
    nodes: list[ResourceRef] = Field(max_length=MAX_LINEAGE_NODES)
    edges: list[LineageEdge] = Field(max_length=MAX_LINEAGE_EDGES)
    truncated: bool = False


IntegritySeverity = Literal["info", "warning", "error"]


class IntegrityRequest(ContractModel):
    refs: list[ResourceRef] = Field(min_length=1, max_length=100)
    max_findings: int = Field(default=100, ge=1, le=MAX_INTEGRITY_FINDINGS)


class IntegrityFinding(ContractModel):
    code: ResourceType
    severity: IntegritySeverity
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
    ref: ResourceRef


class IntegrityResult(ContractModel):
    status: Literal["pass", "warn", "fail"]
    findings: list[IntegrityFinding] = Field(max_length=MAX_INTEGRITY_FINDINGS)
    truncated: bool = False


@runtime_checkable
class Provider(Protocol):
    """Structural interface implemented by storage-independent v1 providers."""

    def provider_info(self, request: ProviderInfoRequest) -> ProviderInfoResult: ...
    def describe_types(self, request: DescribeTypesRequest) -> DescribeTypesResult: ...
    def search(self, request: SearchRequest) -> SearchResult: ...
    def find(self, request: FindRequest) -> FindResult: ...
    def get_resource(self, request: GetResourceRequest) -> GetResourceResult: ...
    def retrieve_evidence(self, request: RetrieveEvidenceRequest) -> RetrieveEvidenceResult: ...
    def explain(self, request: ExplainRequest) -> ExplainResult: ...
    def lineage(self, request: LineageRequest) -> LineageResult: ...
    def integrity(self, request: IntegrityRequest) -> IntegrityResult: ...


__all__ = [name for name in globals() if name.endswith(("Request", "Result"))] + [
    "CONTRACT_ID",
    "MAX_SEARCH_RESULTS",
    "MAX_FIND_RESULTS",
    "MAX_RESOURCE_BYTES",
    "MAX_PREVIEW_BYTES",
    "MAX_EVIDENCE_ITEMS",
    "MAX_EVIDENCE_ITEM_BYTES",
    "MAX_EVIDENCE_CONTENT_BYTES",
    "MAX_EVIDENCE_PACKET_BYTES",
    "MAX_LINEAGE_NODES",
    "MAX_LINEAGE_EDGES",
    "MAX_INTEGRITY_FINDINGS",
    "ResourceRef",
    "ResourceTypeDescriptor",
    "SearchHit",
    "ResourcePayload",
    "EvidenceItem",
    "EvidencePacket",
    "LineageEdge",
    "IntegrityFinding",
    "Provider",
    "ProviderCapability",
]
