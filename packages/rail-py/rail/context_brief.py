"""Deterministic, bounded Context Brief assembly over ``provider.v1`` reads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from krail.epistemic_history import DomainEventRef, EpistemicHistory, OperationContext
from krail.provider.v1 import (
    EvidenceItem,
    EvidencePacket,
    GetResourceRequest,
    MAX_EVIDENCE_ITEM_BYTES,
    GetResourceResult,
    SearchResult,
    ResourceRef,
    SearchRequest,
)


CONTEXT_BRIEF_VERSION = "krail.context-brief.v1"
RANKING_VERSION = "krail.context-ranking.v1"
FRESHNESS_VERSION = "krail.context-freshness.v1"
CONFLICT_VERSION = "krail.context-conflict.v1"
MAX_CONTEXT_ITEMS = 32

Version = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _utf8_prefix(value: str, limit: int) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessingVersion(StrictModel):
    component: Version
    version: Version


def _default_versions() -> tuple[ProcessingVersion, ...]:
    return tuple(sorted((
        ProcessingVersion(component="context-brief", version=CONTEXT_BRIEF_VERSION),
        ProcessingVersion(component="ranking", version=RANKING_VERSION),
        ProcessingVersion(component="freshness", version=FRESHNESS_VERSION),
        ProcessingVersion(component="conflict", version=CONFLICT_VERSION),
    ), key=lambda item: item.component))


class ContextBriefRequest(StrictModel):
    repository: ResourceRef
    issue: ResourceRef
    evaluated_at: datetime
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)] | None = None
    max_items: int = Field(default=12, ge=2, le=MAX_CONTEXT_ITEMS)
    max_total_bytes: int = Field(default=65_536, ge=2, le=131_072)
    authorization_omission: bool = False
    operation_context: OperationContext | None = None
    processing_versions: tuple[ProcessingVersion, ...] = Field(default_factory=_default_versions, min_length=1, max_length=16)

    @field_validator("evaluated_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        return value

    @field_validator("processing_versions")
    @classmethod
    def _unique_components(cls, value: tuple[ProcessingVersion, ...]) -> tuple[ProcessingVersion, ...]:
        components = [item.component for item in value]
        if len(set(components)) != len(components):
            raise ValueError("processing version components must be unique")
        return tuple(sorted(value, key=lambda item: item.component))

    @model_validator(mode="after")
    def _distinct_inputs(self) -> "ContextBriefRequest":
        if self.repository.exact_key == self.issue.exact_key:
            raise ValueError("repository and issue must be distinct exact resources")
        return self


class RankingTraceEntry(StrictModel):
    rank: int = Field(ge=1, le=100)
    source: ResourceRef
    score: float | None = Field(default=None, ge=0.0)
    ranking_version: Literal["krail.context-ranking.v1"] = RANKING_VERSION


class ContextAssertion(StrictModel):
    assertion_id: Digest
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16_384)]
    source: ResourceRef
    locator: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    processing_versions: tuple[ProcessingVersion, ...] = Field(min_length=1, max_length=16)


class FreshnessAssessment(StrictModel):
    source: ResourceRef
    status: Literal["fresh", "stale", "unknown"]
    basis: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
    processing_version: Literal["krail.context-freshness.v1"] = FRESHNESS_VERSION


class EvidenceConflict(StrictModel):
    sources: tuple[ResourceRef, ResourceRef]
    basis: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    processing_version: Literal["krail.context-conflict.v1"] = CONFLICT_VERSION


class EvidenceGap(StrictModel):
    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$", max_length=128)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class AuthorizationOmission(StrictModel):
    reason: Literal["authorization-policy"] = "authorization-policy"
    disclosure: Literal["Additional evidence may exist but is not visible in this authorization context."] = (
        "Additional evidence may exist but is not visible in this authorization context."
    )


class ContextBrief(StrictModel):
    schema_version: Literal["krail.context-brief.v1"] = CONTEXT_BRIEF_VERSION
    brief_digest: Digest
    repository: ResourceRef
    issue: ResourceRef
    evaluated_at: datetime
    evidence: EvidencePacket
    assertions: tuple[ContextAssertion, ...] = Field(max_length=MAX_CONTEXT_ITEMS)
    freshness: tuple[FreshnessAssessment, ...] = Field(max_length=MAX_CONTEXT_ITEMS)
    conflicts: tuple[EvidenceConflict, ...] = Field(max_length=MAX_CONTEXT_ITEMS)
    gaps: tuple[EvidenceGap, ...] = Field(max_length=MAX_CONTEXT_ITEMS)
    omissions: tuple[AuthorizationOmission, ...] = Field(max_length=1)
    ranking_trace: tuple[RankingTraceEntry, ...] = Field(max_length=MAX_CONTEXT_ITEMS)
    processing_versions: tuple[ProcessingVersion, ...] = Field(min_length=1, max_length=16)
    domain_event_ref: DomainEventRef
    truncated: bool = False


def context_brief_digest(
    *,
    repository: ResourceRef,
    issue: ResourceRef,
    evaluated_at: datetime,
    evidence: EvidencePacket,
    assertions: tuple[ContextAssertion, ...],
    freshness: tuple[FreshnessAssessment, ...],
    conflicts: tuple[EvidenceConflict, ...],
    gaps: tuple[EvidenceGap, ...],
    omissions: tuple[AuthorizationOmission, ...],
    ranking_trace: tuple[RankingTraceEntry, ...],
    processing_versions: tuple[ProcessingVersion, ...],
    operation_context: OperationContext | None,
    truncated: bool,
) -> str:
    """Digest the complete production Context Brief semantic body."""

    return _digest(
        {
            "schema_version": CONTEXT_BRIEF_VERSION,
            "repository": repository.model_dump(mode="json"),
            "issue": issue.model_dump(mode="json"),
            "evaluated_at": evaluated_at.isoformat(),
            "evidence": evidence.model_dump(mode="json"),
            "assertions": [item.model_dump(mode="json") for item in assertions],
            "freshness": [item.model_dump(mode="json") for item in freshness],
            "conflicts": [item.model_dump(mode="json") for item in conflicts],
            "gaps": [item.model_dump(mode="json") for item in gaps],
            "omissions": [item.model_dump(mode="json") for item in omissions],
            "ranking_trace": [item.model_dump(mode="json") for item in ranking_trace],
            "processing_versions": [
                item.model_dump(mode="json") for item in processing_versions
            ],
            "operation_context": (
                operation_context.model_dump(mode="json") if operation_context else None
            ),
            "truncated": truncated,
        }
    )


class ContextAuthorizer(Protocol):
    """Live, caller-owned authorization boundary for exact KRAIL reads."""

    def authorize(self, ref: ResourceRef, *, at: datetime | None = None) -> None:
        """Raise ``PermissionError`` when the exact ref is not currently allowed."""


class ContextReader(Protocol):
    """Minimum structural read boundary needed for context and packet assembly."""

    def get_resource(self, request: GetResourceRequest) -> GetResourceResult: ...
    def search(self, request: SearchRequest) -> SearchResult: ...


class ContextBriefService:
    """Read-only context assembly; history recording is an explicit local step."""

    def __init__(self, provider: ContextReader, history: EpistemicHistory | None = None) -> None:
        self.provider = provider
        self.history = history

    def assemble(
        self,
        request: ContextBriefRequest,
        *,
        authorizer: "ContextAuthorizer | None" = None,
    ) -> ContextBrief:
        """Assemble a bounded brief, optionally through a live read authorizer.

        Authorization is deliberately an injected boundary.  KRAIL does not
        mint identities or permissions; callers provide the current authority
        decision.  When present, the decision is checked before candidate
        shaping, before every exact read, and once more before returning so a
        revoked context cannot use a previously allowed packet.
        """
        if authorizer is not None:
            self._authorize(authorizer, request.repository)
            self._authorize(authorizer, request.issue)
        if authorizer is not None:
            self._authorize(authorizer, request.repository)
        repository_payload = self.provider.get_resource(
            GetResourceRequest(ref=request.repository, max_bytes=MAX_EVIDENCE_ITEM_BYTES)
        ).resource
        if authorizer is not None:
            self._authorize(authorizer, request.issue)
        issue_payload = self.provider.get_resource(
            GetResourceRequest(ref=request.issue, max_bytes=MAX_EVIDENCE_ITEM_BYTES)
        ).resource
        query = request.query or self._query(issue_payload.content, request.issue.resource_id)
        searched = self.provider.search(SearchRequest(query=query, limit=request.max_items))
        visible_hits = tuple(
            hit for hit in searched.hits
            if authorizer is None or self._is_authorized(authorizer, hit.ref)
        )
        ranking_trace = tuple(
            RankingTraceEntry(rank=index, source=hit.ref, score=hit.score)
            for index, hit in enumerate(visible_hits, start=1)
        )

        ordered_refs: list[ResourceRef] = []
        for ref in (request.repository, request.issue, *(hit.ref for hit in visible_hits)):
            if ref.exact_key not in {item.exact_key for item in ordered_refs}:
                ordered_refs.append(ref)
            if len(ordered_refs) >= request.max_items:
                break

        evidence_items: list[EvidenceItem] = []
        metadata: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
        remaining = request.max_total_bytes
        truncated = searched.truncated or len(ordered_refs) < 2 + len(visible_hits)
        mandatory_keys = {request.repository.exact_key, request.issue.exact_key}
        for index, ref in enumerate(ordered_refs):
            if remaining <= 0:
                truncated = True
                break
            mandatory_remaining = sum(
                1 for later in ordered_refs[index + 1 :] if later.exact_key in mandatory_keys
            )
            allocation = min(MAX_EVIDENCE_ITEM_BYTES, remaining - mandatory_remaining)
            if allocation <= 0:
                truncated = True
                break
            if authorizer is not None:
                self._authorize(authorizer, ref)
            payload = self.provider.get_resource(
                GetResourceRequest(ref=ref, max_bytes=allocation)
            ).resource
            excerpt, clipped = _utf8_prefix(payload.content.strip(), allocation)
            metadata[ref.exact_key] = self._metadata(payload.content)
            if not excerpt:
                continue
            locator = f"{ref.resource_id}#utf8:0-{len(excerpt.encode('utf-8'))}"
            evidence_items.append(
                EvidenceItem(
                    source=ref,
                    locator=locator,
                    excerpt=excerpt,
                    media_type=payload.media_type,
                    relevance=self._score(ref, visible_hits),
                )
            )
            remaining -= len(excerpt.encode("utf-8"))
            truncated = truncated or clipped or payload.truncated
        if not evidence_items:
            raise LookupError("repository and issue yielded no readable exact evidence")

        packet_fingerprint = {
            "query": query,
            "items": [item.model_dump(mode="json") for item in evidence_items],
            "versions": [item.model_dump(mode="json") for item in request.processing_versions],
        }
        evidence = EvidencePacket(
            packet_id="packet:" + _digest(packet_fingerprint).removeprefix("sha256:")[:24],
            query=query,
            generated_at=request.evaluated_at,
            items=evidence_items,
            truncated=truncated,
        )
        assertions = tuple(self._assertion(item, request.processing_versions) for item in evidence.items)
        freshness = tuple(self._freshness(item.source, metadata.get(item.source.exact_key, {})) for item in evidence.items)
        conflicts, conflict_gaps, conflict_truncated = self._conflicts(evidence.items, metadata)
        truncated = truncated or conflict_truncated
        gaps: list[EvidenceGap] = list(conflict_gaps)
        if not any(item.source.exact_key not in {request.repository.exact_key, request.issue.exact_key} for item in evidence.items):
            gaps.append(EvidenceGap(code="supplementary-evidence-missing", message="No supplementary evidence matched the bounded issue query."))
        if any(item.status == "unknown" for item in freshness):
            gaps.append(EvidenceGap(code="freshness-unknown", message="At least one cited source has no explicit freshness metadata."))
        if len(gaps) > MAX_CONTEXT_ITEMS:
            gaps = gaps[:MAX_CONTEXT_ITEMS]
            truncated = True
        if truncated and not evidence.truncated:
            evidence = evidence.model_copy(update={"truncated": True})
        omissions = (AuthorizationOmission(),) if request.authorization_omission else ()

        if authorizer is not None:
            for ref in ordered_refs:
                self._authorize(authorizer, ref)

        brief_digest = context_brief_digest(
            repository=request.repository,
            issue=request.issue,
            evaluated_at=request.evaluated_at,
            evidence=evidence,
            assertions=assertions,
            freshness=freshness,
            conflicts=conflicts,
            gaps=tuple(gaps),
            omissions=omissions,
            ranking_trace=ranking_trace,
            processing_versions=request.processing_versions,
            operation_context=request.operation_context,
            truncated=truncated,
        )
        event_ref = DomainEventRef.for_digest(
            event_type="krail.context-brief-assembled.v1",
            digest=brief_digest,
            context=request.operation_context,
        )
        return ContextBrief(
            brief_digest=brief_digest,
            repository=request.repository,
            issue=request.issue,
            evaluated_at=request.evaluated_at,
            evidence=evidence,
            assertions=assertions,
            freshness=freshness,
            conflicts=conflicts,
            gaps=tuple(gaps),
            omissions=omissions,
            ranking_trace=ranking_trace,
            processing_versions=request.processing_versions,
            domain_event_ref=event_ref,
            truncated=truncated,
        )

    @staticmethod
    def _authorize(authorizer: "ContextAuthorizer", ref: ResourceRef) -> None:
        try:
            authorizer.authorize(ref)
        except PermissionError as exc:
            # Keep denials opaque: callers must not learn whether a hidden
            # resource existed or why it was filtered.
            raise PermissionError("context access denied") from exc

    @classmethod
    def _is_authorized(cls, authorizer: "ContextAuthorizer", ref: ResourceRef) -> bool:
        try:
            cls._authorize(authorizer, ref)
        except PermissionError:
            return False
        return True
    def record(self, brief: ContextBrief, *, retention_until: datetime | None = None):
        if self.history is None:
            raise RuntimeError("no KRAIL epistemic history store is configured")
        context = OperationContext(
            operation_id=brief.domain_event_ref.operation_id,
            correlation_id=brief.domain_event_ref.correlation_id,
            causation_id=brief.domain_event_ref.causation_id,
        )
        return self.history.append(
            kind="context-brief-assembled",
            subject_digest=brief.brief_digest,
            details={
                "repository": brief.repository.model_dump(mode="json"),
                "issue": brief.issue.model_dump(mode="json"),
                "evidence_packet_id": brief.evidence.packet_id,
                "evidence_digests": [item.source.digest for item in brief.evidence.items],
                "processing_versions": [item.model_dump(mode="json") for item in brief.processing_versions],
            },
            created_at=brief.evaluated_at,
            retention_until=retention_until,
            operation_context=context if any(context.model_dump().values()) else None,
        )

    @staticmethod
    def _query(content: str, fallback: str) -> str:
        body = content.strip() or fallback
        return _utf8_prefix(" ".join(body.split()), 8192)[0]

    @staticmethod
    def _score(ref: ResourceRef, hits) -> float | None:
        return next((hit.score for hit in hits if hit.ref.exact_key == ref.exact_key), None)

    @staticmethod
    def _metadata(content: str) -> dict[str, object]:
        if not content.startswith("---\n"):
            return {}
        closing = content.find("\n---", 4)
        if closing < 0:
            return {}
        try:
            parsed = yaml.safe_load(content[4:closing]) or {}
        except yaml.YAMLError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _assertion(item: EvidenceItem, versions: tuple[ProcessingVersion, ...]) -> ContextAssertion:
        identity = {"source": item.source.model_dump(mode="json"), "locator": item.locator, "text": item.excerpt, "versions": [v.model_dump() for v in versions]}
        return ContextAssertion(assertion_id=_digest(identity), text=item.excerpt, source=item.source, locator=item.locator, processing_versions=versions)

    @staticmethod
    def _freshness(ref: ResourceRef, metadata: dict[str, object]) -> FreshnessAssessment:
        value = str(metadata.get("freshness") or "").strip().lower()
        if value in {"fresh", "current"}:
            return FreshnessAssessment(source=ref, status="fresh", basis=f"source metadata declares freshness={value}")
        if value in {"stale", "expired", "outdated"}:
            return FreshnessAssessment(source=ref, status="stale", basis=f"source metadata declares freshness={value}")
        return FreshnessAssessment(source=ref, status="unknown", basis="source carries no recognized explicit freshness declaration")

    @staticmethod
    def _conflicts(
        items: list[EvidenceItem],
        metadata: dict[tuple[str, str, str, str, str], dict[str, object]],
    ) -> tuple[tuple[EvidenceConflict, ...], tuple[EvidenceGap, ...], bool]:
        refs = {item.source.resource_id: item.source for item in items}
        conflicts: list[EvidenceConflict] = []
        gaps: list[EvidenceGap] = []
        seen: set[tuple[str, str]] = set()
        truncated = False
        for item in items:
            declared = metadata.get(item.source.exact_key, {}).get("conflicts_with", [])
            values = [declared] if isinstance(declared, str) else declared if isinstance(declared, list) else []
            for target_id in sorted(str(value) for value in values):
                if len(conflicts) + len(gaps) >= MAX_CONTEXT_ITEMS:
                    truncated = True
                    break
                target = refs.get(target_id)
                if target is None:
                    gaps.append(EvidenceGap(code="conflict-source-missing", message="A cited source declares a conflict whose counterpart is outside the bounded visible evidence."))
                    continue
                key = tuple(sorted((item.source.digest, target.digest)))
                if key in seen:
                    continue
                seen.add(key)
                conflicts.append(EvidenceConflict(sources=(item.source, target), basis="source metadata explicitly declares conflicts_with"))
        return tuple(conflicts), tuple(gaps), truncated


__all__ = [
    "CONFLICT_VERSION",
    "CONTEXT_BRIEF_VERSION",
    "FRESHNESS_VERSION",
    "RANKING_VERSION",
    "AuthorizationOmission",
    "ContextAssertion",
    "ContextBrief",
    "ContextBriefRequest",
    "ContextBriefService",
    "context_brief_digest",
    "EvidenceConflict",
    "EvidenceGap",
    "FreshnessAssessment",
    "ProcessingVersion",
    "RankingTraceEntry",
]
