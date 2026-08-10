"""Pure projection to OpenSaddle's normative ``krail.provider.v1`` wire shape.

The OpenSaddle protocol is vendored as immutable package data.  These helpers
do not import OpenSaddle and do not authorize reads.  They only project values
that a caller has already authorized and whose direct-source provenance has
been supplied explicitly.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import rfc8785

from krail.provider.v1 import EvidenceItem, EvidencePacket, ResourceRef


WIRE_CONTRACT_ID = "krail.provider.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
_TYPE_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_UTF8_LOCATOR = re.compile(r"^.+#utf8:(\d+)-(\d+)$")
_TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown"})
_REASON_CODES = frozenset(
    {"policy_filtered", "scope_filtered", "classification_filtered"}
)


class ProjectionError(ValueError):
    """The source value cannot be projected under the bounded wire profile."""


def canonical_json(value: Any) -> bytes:
    """Return RFC 8785/JCS bytes used for cross-language projection digests."""

    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError) as exc:
        raise ProjectionError("value cannot be RFC 8785 canonicalized") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def project_digest(value: str) -> dict[str, str]:
    """Convert KRAIL's ``sha256:<hex>`` scalar to the normative Digest object."""

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ProjectionError("only a lower-case sha256 digest can cross the v1 boundary")
    return {"algorithm": "sha-256", "value": value.removeprefix("sha256:")}


def _absolute_uri(value: str, *, field: str) -> str:
    parsed = urlsplit(value)
    if (
        not parsed.scheme
        or not value.startswith(f"{parsed.scheme}:")
        or len(value.partition(":")[2]) < 1
        or any(char.isspace() for char in value)
    ):
        raise ProjectionError(f"{field} must be an absolute URI")
    return value


@dataclass(frozen=True, slots=True)
class DirectSourceBinding:
    """Caller-attested provenance for a resource that is itself a direct source.

    ``version`` and ``digest`` must exactly match the KRAIL ref.  Requiring this
    binding prevents the projection from inventing a source record for a
    derived KRAIL resource.
    """

    source_id: str
    origin: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.source_id):
            raise ProjectionError("source_id is outside OpenSaddle v1 identifier bounds")
        _absolute_uri(self.origin, field="source origin")
        if len(self.origin) > 932 or len(self.origin.partition(":")[2]) > 900:
            raise ProjectionError("source origin is outside OpenSaddle v1 bounds")
        if not self.version or len(self.version) > 200:
            raise ProjectionError("source version is outside OpenSaddle v1 bounds")
        project_digest(self.digest)


@dataclass(frozen=True, slots=True)
class AuthorizationProjection:
    """Explicit authorization decision and non-leaking collection summary."""

    authorized: bool
    omitted_count: int = 0
    count_precision: str = "exact"
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.authorized, bool):
            raise ProjectionError("authorization must be an explicit boolean decision")
        if not 0 <= self.omitted_count <= 1_000_000_000:
            raise ProjectionError("omitted_count is outside OpenSaddle v1 bounds")
        if self.count_precision not in {"exact", "lower_bound", "undisclosed"}:
            raise ProjectionError("unsupported authorization count precision")
        if len(self.reason_codes) > 3 or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ProjectionError("authorization reason codes must be unique and bounded")
        if not set(self.reason_codes).issubset(_REASON_CODES):
            raise ProjectionError("unsupported authorization reason code")

    def wire_summary(self) -> dict[str, Any]:
        if not self.authorized:
            raise ProjectionError("unauthorized evidence must not be projected")
        return {
            "shape": "authorized_projection",
            "omitted_count": self.omitted_count,
            "count_precision": self.count_precision,
            "reason_codes": list(self.reason_codes),
        }


def project_direct_resource_ref(
    ref: ResourceRef,
    binding: DirectSourceBinding,
) -> dict[str, Any]:
    """Project an exact, explicitly direct KRAIL ref to OpenSaddle v1."""

    if ref.contract != "krail.provider.model.v1":
        raise ProjectionError("unsupported KRAIL provider model discriminator")
    if not _IDENTIFIER.fullmatch(ref.resource_id):
        raise ProjectionError("resource_id is outside OpenSaddle v1 identifier bounds")
    if not _TYPE_NAME.fullmatch(ref.resource_type):
        raise ProjectionError("resource_type is outside OpenSaddle v1 type-name bounds")
    if len(ref.version) > 256:
        raise ProjectionError("resource version is outside OpenSaddle v1 bounds")
    _absolute_uri(ref.authority, field="resource issuer")
    if binding.version != ref.version or binding.digest != ref.digest:
        raise ProjectionError(
            "direct-source provenance must match the exact resource version and digest"
        )
    return {
        "issuer": ref.authority,
        "resource_id": ref.resource_id,
        "resource_type": ref.resource_type,
        "version": ref.version,
        "digest": project_digest(ref.digest),
        "source": {
            "source_id": binding.source_id,
            "origin": binding.origin,
            "version": binding.version,
            "digest": project_digest(binding.digest),
        },
    }


def _locator(value: str) -> dict[str, Any]:
    match = _UTF8_LOCATOR.fullmatch(value)
    if match:
        start, end = (int(item) for item in match.groups())
        # OpenSaddle's canonical JSON boundary follows I-JSON/RFC 8785 and
        # therefore limits integer values to the exactly representable range.
        if (
            start < 0
            or start > 9_007_199_254_740_990
            or end <= start
            or end > 9_007_199_254_740_991
        ):
            raise ProjectionError("evidence byte span is invalid")
        return {"kind": "span", "unit": "bytes", "start": start, "end": end}
    if not value or len(value) > 1024:
        raise ProjectionError("evidence locator cannot be represented by OpenSaddle v1")
    return {"kind": "fragment", "value": value}


def _citation(
    item: EvidenceItem,
    resource: dict[str, Any],
) -> dict[str, Any]:
    if item.media_type not in _TEXT_MEDIA_TYPES:
        raise ProjectionError(
            "evidence media_type is not an explicitly supported UTF-8 text type"
        )
    excerpt_digest = {"algorithm": "sha-256", "value": _sha256(item.excerpt.encode("utf-8"))}
    locator = _locator(item.locator)
    identity = {
        "resource": resource,
        "locator": locator,
        "excerpt_digest": excerpt_digest,
        "internal_media_type": item.media_type,
        "internal_relevance": item.relevance,
    }
    identity_digest = _sha256(canonical_json(identity))
    evidence_id = f"evidence/{identity_digest[:32]}"
    wire_body = {
        "evidence_id": evidence_id,
        "relation": "direct",
        "resource": resource,
        "locator": locator,
        "excerpt_digest": excerpt_digest,
        "content": item.excerpt,
    }
    return {
        **wire_body,
        "record_digest": {
            "algorithm": "sha-256",
            "value": _sha256(canonical_json(wire_body)),
        },
    }


def project_direct_evidence_packet(
    packet: EvidencePacket,
    *,
    source_bindings: Mapping[tuple[str, str, str, str, str], DirectSourceBinding],
    authorization: AuthorizationProjection,
) -> dict[str, Any]:
    """Project a complete direct-evidence packet without weakening policy.

    Derived inputs are unsupported because the KRAIL Python packet does not
    carry the complete lineage bindings required by OpenSaddle.  Truncated
    packets are rejected because OpenSaddle v1 has no equivalent packet flag.
    """

    authorization_summary = authorization.wire_summary()
    if packet.contract != "krail.provider.model.v1":
        raise ProjectionError("unsupported KRAIL evidence model discriminator")
    if packet.truncated:
        raise ProjectionError("truncated KRAIL packets cannot be projected losslessly")
    if not _IDENTIFIER.fullmatch(packet.packet_id):
        raise ProjectionError("packet_id is outside OpenSaddle v1 identifier bounds")

    results: list[dict[str, Any]] = []
    completed: set[tuple[str, str, str, str, str]] = set()
    current_key: tuple[str, str, str, str, str] | None = None
    evidence_ids: set[str] = set()
    for item in packet.items:
        key = item.source.exact_key
        if current_key is not None and key != current_key:
            completed.add(current_key)
        if key in completed:
            raise ProjectionError(
                "interleaved resource citations cannot preserve global KRAIL item order"
            )
        binding = source_bindings.get(key)
        if binding is None:
            raise ProjectionError("every direct evidence source requires an explicit binding")
        resource = project_direct_resource_ref(item.source, binding)
        citation = _citation(item, resource)
        if citation["evidence_id"] in evidence_ids:
            raise ProjectionError("duplicate evidence records are not projectable")
        evidence_ids.add(citation["evidence_id"])
        if key == current_key:
            citations = results[-1]["citations"]
            if len(citations) >= 50:
                raise ProjectionError("direct citation count exceeds OpenSaddle v1 bounds")
            citations.append(citation)
        else:
            results.append(
                {
                    "resource": resource,
                    "inputs": [],
                    "citations": [citation],
                }
            )
            current_key = key
    if not results or len(results) > 100:
        raise ProjectionError("projected evidence result count is outside OpenSaddle v1 bounds")
    return {
        "packet_id": packet.packet_id,
        "created_at": packet.generated_at.isoformat().replace("+00:00", "Z"),
        "results": results,
        "authorization": authorization_summary,
    }


def project_refs_in_value(
    value: Any,
    *,
    source_bindings: Mapping[tuple[str, str, str, str, str], DirectSourceBinding],
    authorization: AuthorizationProjection,
) -> Any:
    """Recursively project refs/packets in a Pydantic JSON value.

    This is used only for the language-neutral Context Brief bundle. Unknown
    values remain byte-for-byte equivalent at the JSON data-model level.
    """

    if isinstance(value, ResourceRef):
        binding = source_bindings.get(value.exact_key)
        if binding is None:
            raise ProjectionError("every projected ResourceRef requires an explicit binding")
        return project_direct_resource_ref(value, binding)
    if isinstance(value, EvidencePacket):
        return project_direct_evidence_packet(
            value,
            source_bindings=source_bindings,
            authorization=authorization,
        )
    if hasattr(value, "model_dump"):
        return {
            key: project_refs_in_value(
                item,
                source_bindings=source_bindings,
                authorization=authorization,
            )
            for key, item in value.__dict__.items()
        }
    if isinstance(value, Mapping):
        return {
            key: project_refs_in_value(
                item,
                source_bindings=source_bindings,
                authorization=authorization,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            project_refs_in_value(
                item,
                source_bindings=source_bindings,
                authorization=authorization,
            )
            for item in value
        ]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProjectionError("projected timestamps must include a timezone")
        return value.isoformat().replace("+00:00", "Z")
    return value


def bindings_by_exact_key(
    values: Iterable[tuple[ResourceRef, DirectSourceBinding]],
) -> dict[tuple[str, str, str, str, str], DirectSourceBinding]:
    """Build a duplicate-rejecting exact binding map."""

    result: dict[tuple[str, str, str, str, str], DirectSourceBinding] = {}
    for ref, binding in values:
        if ref.exact_key in result:
            raise ProjectionError("duplicate direct-source binding")
        result[ref.exact_key] = binding
    return result


__all__ = [
    "AuthorizationProjection",
    "DirectSourceBinding",
    "ProjectionError",
    "WIRE_CONTRACT_ID",
    "bindings_by_exact_key",
    "canonical_json",
    "project_digest",
    "project_direct_evidence_packet",
    "project_direct_resource_ref",
    "project_refs_in_value",
]
