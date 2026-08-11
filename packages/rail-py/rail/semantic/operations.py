"""Bounded, policy-shaped semantic graph reads over the governed kernel."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from krail.provider.semantic import (
    AliasView,
    AssembleCrossSourceEvidenceRequest,
    AssembleCrossSourceEvidenceResult,
    CompareObservationsRequest,
    CompareObservationsResult,
    EntityView,
    EvidenceStatement,
    ExplainConflictRequest,
    ExplainConflictResult,
    FactObjectView,
    FactView,
    GetEntityRequest,
    GetEntityResult,
    ListOntologyPackagesRequest,
    ListOntologyPackagesResult,
    ListOntologyProposalHistoryRequest,
    ListOntologyProposalHistoryResult,
    ObservationDifference,
    OntologyPackageView,
    OntologyProposalHistoryView,
    OperationGap,
    OperationOmissions,
    OperationTrace,
    ProvenanceView,
    RelationshipHop,
    ResolveCandidate,
    ResolveEntityRequest,
    ResolveEntityResult,
    SemanticPackView,
    SemanticLineage,
    SemanticReadScope,
    TraverseRelationshipsRequest,
    TraverseRelationshipsResult,
)
from rail.semantic.models import (
    Alias,
    Conflict,
    Entity,
    Fact,
    OntologyChangeSet,
    OntologyPackage,
    OntologyPackageVersion,
    SemanticType,
    canonical_digest,
)
from rail.semantic.packs import SemanticPack
from rail.semantic.repository import SemanticRepository, SemanticRow


SEMANTIC_PROCESSING_VERSION = "semantic-operations/1.0.0"
SEMANTIC_PROCESSING_DIGEST = canonical_digest(
    {"component": "semantic-operations", "version": SEMANTIC_PROCESSING_VERSION}
)


@dataclass(frozen=True)
class AuthorizedSemanticScope:
    """Live-policy result consumed by graph reads, never caller input."""

    tenant_id: str
    project_id: str
    subject_id: str
    policy_digest: str
    authorization_context_digest: str
    request_scope_digest: str
    evidence_keys: frozenset[tuple[str, str, str, str, str]]


@dataclass(frozen=True)
class _PageState:
    operation: str
    snapshot: str
    shape: object
    offset: int
    total: int


def authorize_semantic_scope_from_claims(
    scope: SemanticReadScope,
    claims: Any,
    *,
    authorization_context_digest: str,
    resolve_source: Callable[[Any], tuple[str, str] | None],
) -> AuthorizedSemanticScope:
    """Bind requested refs to freshly verified Phase 4 claims and source metadata.

    ``claims`` must be the result of the live signed-context verifier for this
    operation.  ``resolve_source`` is authoritative metadata lookup; requested
    ``source_id`` and ``classification`` values are cross-checks only.
    """

    if (
        claims.tenant_id != scope.tenant_id
        or claims.project_id != scope.project_id
        or claims.subject != scope.subject_id
        or claims.policy_digest != scope.policy_digest
        or "capture.read" not in claims.actions
    ):
        raise PermissionError("semantic scope is unavailable")
    evidence: set[tuple[str, str, str, str, str]] = set()
    wildcard = "*" in claims.source_ids
    for requested in scope.allowed_sources:
        authoritative = resolve_source(requested.source)
        if authoritative is None:
            continue
        source_id, classification = authoritative
        if (source_id, classification) != (
            requested.source_id,
            requested.classification,
        ):
            continue
        if (
            wildcard or source_id in claims.source_ids
        ) and classification in claims.classifications:
            evidence.add(requested.source.exact_key)
    return AuthorizedSemanticScope(
        tenant_id=claims.tenant_id,
        project_id=claims.project_id,
        subject_id=claims.subject,
        policy_digest=claims.policy_digest,
        authorization_context_digest=authorization_context_digest,
        request_scope_digest=scope.scope_digest,
        evidence_keys=frozenset(evidence),
    )


def semantic_scope_authorizer_from_context(
    authority: Any,
    context: Any,
    *,
    resolve_source: Callable[[Any], tuple[str, str] | None],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Callable[[SemanticReadScope], AuthorizedSemanticScope]:
    """Compose graph reads with live Phase 4 signature/revocation checks."""

    def authorize(scope: SemanticReadScope) -> AuthorizedSemanticScope:
        claims = authority.verify(context, as_of=clock())
        return authorize_semantic_scope_from_claims(
            scope,
            claims,
            authorization_context_digest=context.context_digest,
            resolve_source=resolve_source,
        )

    return authorize


class SemanticOperationsService:
    """Six fixed graph reads plus governed package projections; never a query DSL."""

    def __init__(
        self,
        repository: SemanticRepository,
        *,
        cursor_key: bytes,
        authorize_scope: Callable[[SemanticReadScope], AuthorizedSemanticScope],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if len(cursor_key) < 32:
            raise ValueError("semantic cursor key must contain at least 32 bytes")
        self.repository = repository
        self._cursor_key = bytes(cursor_key)
        self._authorize_scope = authorize_scope
        self._clock = clock

    def _finish(
        self,
        result: Any,
        request: Any,
        *,
        started_at: float,
        collection_fields: tuple[str, ...],
        singular_fields: tuple[str, ...] = (),
        page_state: _PageState | None = None,
    ) -> Any:
        elapsed_ms = max(0.0, (self._clock() - started_at) * 1000)
        timed_out = elapsed_ms > request.budget.max_time_ms
        body = result.model_dump(mode="python")
        gaps = list(result.gaps)
        if timed_out:
            for field in collection_fields:
                body[field] = ()
            for field in singular_fields:
                body[field] = None
            if "explanation" in body:
                body["explanation"] = ""
            if "next_cursor" in body:
                body["next_cursor"] = None
            gaps.append(
                OperationGap(
                    code="time-budget",
                    message="The response exceeded its wall-clock budget.",
                )
            )
            body["truncated"] = True
        body["gaps"] = tuple(dict.fromkeys(gaps))
        candidate = type(result).model_validate(body)
        if len(candidate.model_dump_json().encode()) <= request.budget.max_bytes:
            return candidate
        for field in collection_fields:
            body[field] = ()
        for field in singular_fields:
            body[field] = None
        if "explanation" in body:
            body["explanation"] = ""
        if "next_cursor" in body:
            if (
                not timed_out
                and page_state is not None
                and page_state.offset + 1 < page_state.total
            ):
                body["next_cursor"] = self._cursor(
                    operation=page_state.operation,
                    scope=request.scope,
                    snapshot=page_state.snapshot,
                    shape=page_state.shape,
                    offset=page_state.offset + 1,
                )
            else:
                body["next_cursor"] = None
        body["gaps"] = tuple(gap for gap in gaps if gap.code == "time-budget") + (
            OperationGap(
                code="byte-budget",
                message="The serialized response reached its byte budget.",
            ),
        )
        body["truncated"] = True
        candidate = type(result).model_validate(body)
        if (
            len(candidate.model_dump_json().encode()) > request.budget.max_bytes
            and body.get("next_cursor") is not None
        ):
            # A continuation is useful only when the complete signed envelope
            # fits the advertised serialized-byte ceiling.
            body["next_cursor"] = None
            candidate = type(result).model_validate(body)
        if len(candidate.model_dump_json().encode()) > request.budget.max_bytes:
            raise ValueError(
                "semantic response metadata exceeds the requested byte budget"
            )
        return candidate

    def _scope(self, scope: SemanticReadScope) -> AuthorizedSemanticScope:
        verified = self._authorize_scope(scope)
        if (
            not isinstance(verified, AuthorizedSemanticScope)
            or verified.request_scope_digest != scope.scope_digest
            or verified.tenant_id != scope.tenant_id
            or verified.project_id != scope.project_id
            or verified.subject_id != scope.subject_id
            or verified.policy_digest != scope.policy_digest
            or verified.tenant_id != self.repository.tenant_id
            or verified.project_id != self.repository.project_id
        ):
            raise PermissionError("semantic scope is unavailable")
        return verified

    def _snapshot(self, kinds: Sequence[str]) -> tuple[list[SemanticRow], str]:
        with self.repository.store.transaction():
            rows = [
                row
                for row in self.repository.store.list(
                    self.repository.tenant_id, self.repository.project_id
                )
                if row.record_kind in kinds
            ]
        rows.sort(key=lambda row: (row.record_kind, row.record_id))
        digest = canonical_digest(
            [
                {
                    "kind": row.record_kind,
                    "id": row.record_id,
                    "revision": row.revision,
                    "payload": row.payload,
                }
                for row in rows
            ]
        )
        return rows, digest

    @staticmethod
    def _permission_snapshot(values: Iterable[Any]) -> str:
        payloads = [
            value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            for value in values
        ]
        payloads.sort(
            key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
        )
        return canonical_digest(payloads)

    @staticmethod
    def _conflict_snapshot(
        conflicts: Iterable[Conflict], visible_fact_ids: set[str]
    ) -> tuple[dict[str, Any], ...]:
        """Permission-shape conflict identity without retaining hidden fact IDs."""
        return tuple(
            {
                "conflict_id": item.conflict_id,
                "fact_ids": tuple(
                    value for value in item.fact_ids if value in visible_fact_ids
                ),
                "reason": item.reason,
                "state": item.state,
                "resolution_fact_id": (
                    item.resolution_fact_id
                    if item.resolution_fact_id in visible_fact_ids
                    else None
                ),
                "revision": item.revision,
                "provenance": item.provenance.model_dump(mode="json"),
            }
            for item in conflicts
        )

    @staticmethod
    def _visible(value: Any, authorization: AuthorizedSemanticScope) -> bool:
        provenance = getattr(value, "provenance", None)
        if provenance is None:
            return False
        return all(
            ref.exact_key in authorization.evidence_keys for ref in provenance.evidence
        )

    def _partition(
        self, values: Iterable[Any], authorization: AuthorizedSemanticScope
    ) -> tuple[list[Any], OperationOmissions]:
        visible: list[Any] = []
        for value in values:
            if self._visible(value, authorization):
                visible.append(value)
        # Background hidden rows never influence result metadata.
        return visible, OperationOmissions()

    def _facts(
        self,
        values: Iterable[Fact],
        authorization: AuthorizedSemanticScope,
        visible_entity_ids: set[str],
        lineages: dict[str, SemanticLineage],
    ) -> tuple[list[Fact], OperationOmissions]:
        authorized, omissions = self._partition(values, authorization)
        visible = [
            fact
            for fact in authorized
            if fact.subject_entity_id in visible_entity_ids
            and (
                fact.object.entity_id is None
                or fact.object.entity_id in visible_entity_ids
            )
            and fact.relationship_type_id in lineages
        ]
        # Facts whose endpoints or type definitions are hidden disappear as a
        # unit; do not disclose that an edge was removed.
        return visible, OperationOmissions()

    def _lineages(
        self, rows: Sequence[SemanticRow], authorization: AuthorizedSemanticScope
    ) -> tuple[
        dict[str, SemanticLineage], list[SemanticType], list[SemanticPack], bool
    ]:
        types, _ = self._partition(
            (
                SemanticType.model_validate(row.payload)
                for row in rows
                if row.record_kind == "type"
            ),
            authorization,
        )
        packs, _ = self._partition(
            (
                SemanticPack.model_validate(row.payload)
                for row in rows
                if row.record_kind == "semantic_pack"
            ),
            authorization,
        )
        trusted = {
            (pack.pack_id, pack.version): pack
            for pack in packs
            if pack.signature_verification is not None
            and pack.signature_verification.status == "trusted"
        }
        lineages: dict[str, SemanticLineage] = {}
        for semantic_type in types:
            pack = trusted.get((semantic_type.pack_id, semantic_type.pack_version))
            if pack is None or semantic_type.type_id not in pack.type_ids:
                continue
            verification = pack.signature_verification
            assert verification is not None
            lineages[semantic_type.type_id] = SemanticLineage(
                semantic_type_id=semantic_type.type_id,
                semantic_type_revision=semantic_type.revision,
                semantic_type_digest=canonical_digest(
                    semantic_type.model_dump(mode="json")
                ),
                semantic_type_provenance=self._provenance(semantic_type),
                pack_id=pack.pack_id,
                pack_version=pack.version,
                pack_digest=pack.content_digest,
                pack_revision=pack.revision,
                pack_provenance=self._provenance(pack),
                signature_verification_digest=verification.request_digest,
                signature_verifier_digest=verification.verifier_digest,
                trust_policy_digest=verification.trust_policy_digest,
            )
        visible_types = [item for item in types if item.type_id in lineages]
        visible_pack_keys = {
            (item.pack_id, item.pack_version) for item in visible_types
        }
        visible_packs = [
            item for key, item in trusted.items() if key in visible_pack_keys
        ]
        # An authorized type without a trusted exact pack is a visible quality
        # gap. Unrelated hidden types/packs do not affect this bit.
        omitted = len(lineages) != len(types)
        return lineages, visible_types, visible_packs, omitted

    def _entities(
        self,
        values: Iterable[Entity],
        authorization: AuthorizedSemanticScope,
        lineages: dict[str, SemanticLineage],
    ) -> tuple[list[Entity], OperationOmissions]:
        authorized, omissions = self._partition(values, authorization)
        typed = [item for item in authorized if item.type_id in lineages]
        identifiers = {item.entity_id for item in typed}
        visible = [
            item
            for item in typed
            if item.merged_into is None or item.merged_into in identifiers
        ]
        return visible, OperationOmissions()

    def _cursor(
        self,
        *,
        operation: str,
        scope: SemanticReadScope,
        snapshot: str,
        shape: object,
        offset: int,
    ) -> str:
        if offset < 0 or offset >= 2**32:
            raise ValueError("semantic cursor offset is out of bounds")
        context = canonical_digest(
            {
                "operation": operation,
                "scope": scope.scope_digest,
                "shape": canonical_digest(shape),
            }
        )
        body = (
            offset.to_bytes(4, "big")
            + bytes.fromhex(context.removeprefix("sha256:"))[:16]
            + bytes.fromhex(snapshot.removeprefix("sha256:"))[:16]
        )
        # A 128-bit truncated HMAC keeps cursors compact enough for the minimum
        # response budget while retaining a strong opaque integrity boundary.
        signature = hmac.new(self._cursor_key, body, hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")

    def _offset(
        self,
        cursor: str | None,
        *,
        operation: str,
        scope: SemanticReadScope,
        snapshot: str,
        shape: object,
    ) -> tuple[int, bool]:
        if cursor is None:
            return 0, False
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            if len(raw) != 52:
                raise ValueError
            body_raw, supplied = raw[:36], raw[36:]
            expected = hmac.new(self._cursor_key, body_raw, hashlib.sha256).digest()[
                :16
            ]
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            context = canonical_digest(
                {
                    "operation": operation,
                    "scope": scope.scope_digest,
                    "shape": canonical_digest(shape),
                }
            )
            expected_context = bytes.fromhex(context.removeprefix("sha256:"))[:16]
            if not hmac.compare_digest(body_raw[4:20], expected_context):
                raise ValueError
            expected_snapshot = bytes.fromhex(snapshot.removeprefix("sha256:"))[:16]
            if not hmac.compare_digest(body_raw[20:36], expected_snapshot):
                return 0, True
            offset = int.from_bytes(body_raw[:4], "big")
            return offset, False
        except Exception as exc:
            raise ValueError(
                "semantic cursor is invalid for this operation and scope"
            ) from exc

    @staticmethod
    def _trace(
        operation: str,
        request: Any,
        snapshot: str,
        lineages: Mapping[str, SemanticLineage] | None = None,
    ) -> OperationTrace:
        packs = {
            (item.pack_id, item.pack_version, item.pack_digest)
            for item in (lineages or {}).values()
        }
        pack_id = pack_version = pack_digest = None
        if len(packs) == 1:
            pack_id, pack_version, pack_digest = next(iter(packs))
        return OperationTrace(
            operation=operation,
            request_digest=canonical_digest(request.model_dump(mode="json")),
            snapshot_digest=snapshot,
            processing_version=SEMANTIC_PROCESSING_VERSION,
            processing_digest=SEMANTIC_PROCESSING_DIGEST,
            pack_id=pack_id,
            pack_version=pack_version,
            pack_digest=pack_digest,
        )

    def _page(
        self,
        values: Sequence[Any],
        *,
        request: Any,
        operation: str,
        snapshot: str,
        shape: object,
    ) -> tuple[list[Any], str | None, bool, tuple[OperationGap, ...], _PageState]:
        offset, stale = self._offset(
            request.cursor,
            operation=operation,
            scope=request.scope,
            snapshot=snapshot,
            shape=shape,
        )
        state = _PageState(
            operation=operation,
            snapshot=snapshot,
            shape=shape,
            offset=offset,
            total=len(values),
        )
        if stale:
            return (
                [],
                None,
                True,
                (
                    OperationGap(
                        code="cursor-stale",
                        message="The graph snapshot changed; restart the read.",
                    ),
                ),
                state,
            )
        limit = min(request.budget.max_items, request.budget.max_nodes)
        selected: list[Any] = []
        byte_total = 0
        byte_limited = False
        skipped_oversized = False
        # Reserve room for root/trace/gaps and the signed continuation cursor.
        item_byte_budget = max(
            0, request.budget.max_bytes - min(4096, request.budget.max_bytes // 2)
        )
        for value in values[offset : offset + limit]:
            size = len(
                json.dumps(
                    value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ).encode()
            )
            if byte_total + size > item_byte_budget:
                byte_limited = True
                if not selected:
                    # Advance past an item that can never fit this envelope so
                    # the same cursor cannot dead-loop forever.
                    skipped_oversized = True
                break
            selected.append(value)
            byte_total += size
        consumed = len(selected)
        advanced = consumed + (1 if skipped_oversized else 0)
        more = offset + advanced < len(values)
        truncated = more or byte_limited
        next_cursor = (
            self._cursor(
                operation=operation,
                scope=request.scope,
                snapshot=snapshot,
                shape=shape,
                offset=offset + advanced,
            )
            if more and advanced
            else None
        )
        gaps: list[OperationGap] = []
        if byte_limited:
            gaps.append(
                OperationGap(
                    code="byte-budget", message="The response reached its byte budget."
                )
            )
        elif more:
            gaps.append(
                OperationGap(
                    code="item-budget", message="The response reached its item budget."
                )
            )
        return selected, next_cursor, truncated, tuple(gaps), state

    @staticmethod
    def _provenance(value: Any) -> ProvenanceView:
        return ProvenanceView(
            evidence=value.provenance.evidence,
            observed_at=value.provenance.observed_at,
            processing_version=value.provenance.processing_version,
            processing_digest=value.provenance.processing_digest,
        )

    @classmethod
    def _entity(cls, value: Entity, lineage: SemanticLineage) -> EntityView:
        return EntityView(
            entity_id=value.entity_id,
            type_id=value.type_id,
            canonical_name=value.canonical_name,
            state=value.state,
            merged_into=value.merged_into,
            revision=value.revision,
            provenance=cls._provenance(value),
            lineage=lineage,
        )

    @classmethod
    def _alias(cls, value: Alias, lineage: SemanticLineage) -> AliasView:
        return AliasView(
            alias_id=value.alias_id,
            entity_id=value.entity_id,
            value=value.value,
            normalized_value=value.normalized_value,
            revision=value.revision,
            provenance=cls._provenance(value),
            lineage=lineage,
        )

    @classmethod
    def _fact(cls, value: Fact, lineage: SemanticLineage) -> FactView:
        return FactView(
            fact_id=value.fact_id,
            subject_entity_id=value.subject_entity_id,
            relationship_type_id=value.relationship_type_id,
            object=FactObjectView(
                entity_id=value.object.entity_id, literal=value.object.literal
            ),
            assertion_kind=value.assertion_kind,
            confidence=value.confidence,
            valid_from=value.valid_from,
            valid_until=value.valid_until,
            revision=value.revision,
            provenance=cls._provenance(value),
            lineage=lineage,
        )

    def resolve_entity(self, request: ResolveEntityRequest) -> ResolveEntityResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(("type", "semantic_pack", "entity", "alias"))
        lineages, semantic_types, semantic_packs, lineage_omitted = self._lineages(
            rows, authorization
        )
        entities, entity_omissions = self._entities(
            (
                Entity.model_validate(row.payload)
                for row in rows
                if row.record_kind == "entity"
            ),
            authorization,
            lineages,
        )
        aliases, alias_omissions = self._partition(
            (
                Alias.model_validate(row.payload)
                for row in rows
                if row.record_kind == "alias"
            ),
            authorization,
        )
        aliases_by_entity: dict[str, list[Alias]] = {}
        for alias in aliases:
            aliases_by_entity.setdefault(alias.entity_id, []).append(alias)
        aliases = [
            item
            for item in aliases
            if item.entity_id in {entity.entity_id for entity in entities}
        ]
        snapshot = self._permission_snapshot(
            (*semantic_types, *semantic_packs, *entities, *aliases)
        )
        query = request.query.casefold()
        candidates: list[ResolveCandidate] = []
        for entity in entities:
            if request.type_ids and entity.type_id not in request.type_ids:
                continue
            match = None
            alias_match = None
            if entity.entity_id.casefold() == query:
                match = "entity-id"
            elif entity.canonical_name.casefold() == query:
                match = "canonical-name"
            else:
                alias_match = next(
                    (
                        item
                        for item in aliases_by_entity.get(entity.entity_id, ())
                        if item.normalized_value.casefold() == query
                    ),
                    None,
                )
                if alias_match:
                    match = "alias"
            if match:
                candidates.append(
                    ResolveCandidate(
                        entity=self._entity(entity, lineages[entity.type_id]),
                        match=match,
                        alias=self._alias(alias_match, lineages[entity.type_id])
                        if alias_match
                        else None,
                    )
                )
        candidates.sort(
            key=lambda item: (item.match != "entity-id", item.entity.entity_id)
        )
        shape = {"query": request.query, "type_ids": request.type_ids}
        page, cursor, truncated, gaps, page_state = self._page(
            candidates,
            request=request,
            operation="resolve_entity",
            snapshot=snapshot,
            shape=shape,
        )
        if not page and not gaps:
            gaps = (
                OperationGap(
                    code="not-found", message="No visible entity matched the query."
                ),
            )
        omissions = OperationOmissions(
            present=lineage_omitted
            or entity_omissions.present
            or alias_omissions.present,
            reasons=("policy",)
            if lineage_omitted or entity_omissions.present or alias_omissions.present
            else (),
        )
        result = ResolveEntityResult(
            candidates=page,
            next_cursor=cursor,
            truncated=truncated,
            gaps=gaps,
            omissions=omissions,
            trace=self._trace("resolve_entity", request, snapshot, lineages),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("candidates",),
            page_state=page_state,
        )

    def get_entity(self, request: GetEntityRequest) -> GetEntityResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(
            ("type", "semantic_pack", "entity", "alias", "fact")
        )
        lineages, semantic_types, semantic_packs, lineage_omitted = self._lineages(
            rows, authorization
        )
        entities, eo = self._entities(
            (
                Entity.model_validate(row.payload)
                for row in rows
                if row.record_kind == "entity"
            ),
            authorization,
            lineages,
        )
        entity = next(
            (item for item in entities if item.entity_id == request.entity_id), None
        )
        aliases, ao = self._partition(
            (
                Alias.model_validate(row.payload)
                for row in rows
                if row.record_kind == "alias"
            ),
            authorization,
        )
        facts, fo = self._facts(
            (
                Fact.model_validate(row.payload)
                for row in rows
                if row.record_kind == "fact"
            ),
            authorization,
            {item.entity_id for item in entities},
            lineages,
        )
        aliases = [
            item
            for item in aliases
            if item.entity_id in {entity.entity_id for entity in entities}
        ]
        snapshot = self._permission_snapshot(
            (*semantic_types, *semantic_packs, *entities, *aliases, *facts)
        )
        values: list[Any] = []
        if entity and request.include_aliases:
            values.extend(
                self._alias(item, lineages[entity.type_id])
                for item in aliases
                if item.entity_id == entity.entity_id
            )
        if entity and request.include_facts:
            values.extend(
                self._fact(item, lineages[item.relationship_type_id])
                for item in facts
                if item.subject_entity_id == entity.entity_id
                or item.object.entity_id == entity.entity_id
            )
        values.sort(
            key=lambda item: (
                item.__class__.__name__,
                getattr(item, "alias_id", getattr(item, "fact_id", "")),
            )
        )
        shape = {
            "entity_id": request.entity_id,
            "aliases": request.include_aliases,
            "facts": request.include_facts,
        }
        page, cursor, truncated, gaps, page_state = self._page(
            values,
            request=request,
            operation="get_entity",
            snapshot=snapshot,
            shape=shape,
        )
        aliases_page = tuple(item for item in page if isinstance(item, AliasView))
        facts_page = tuple(item for item in page if isinstance(item, FactView))
        if entity is None and not gaps:
            gaps = (
                OperationGap(
                    code="not-found", message="The visible entity is unavailable."
                ),
            )
        omitted = lineage_omitted or eo.present or ao.present or fo.present
        result = GetEntityResult(
            entity=self._entity(entity, lineages[entity.type_id]) if entity else None,
            aliases=aliases_page,
            facts=facts_page,
            next_cursor=cursor,
            truncated=truncated,
            gaps=gaps,
            omissions=OperationOmissions(
                present=omitted, reasons=("policy",) if omitted else ()
            ),
            trace=self._trace("get_entity", request, snapshot, lineages),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("aliases", "facts"),
            singular_fields=("entity",),
            page_state=page_state,
        )

    def traverse_relationships(
        self, request: TraverseRelationshipsRequest
    ) -> TraverseRelationshipsResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(("type", "semantic_pack", "entity", "fact"))
        lineages, semantic_types, semantic_packs, lineage_omitted = self._lineages(
            rows, authorization
        )
        entities, eo = self._entities(
            (
                Entity.model_validate(row.payload)
                for row in rows
                if row.record_kind == "entity"
            ),
            authorization,
            lineages,
        )
        facts, fo = self._facts(
            (
                Fact.model_validate(row.payload)
                for row in rows
                if row.record_kind == "fact"
            ),
            authorization,
            {item.entity_id for item in entities},
            lineages,
        )
        snapshot = self._permission_snapshot(
            (*semantic_types, *semantic_packs, *entities, *facts)
        )
        by_id = {item.entity_id: item for item in entities}
        root = by_id.get(request.root_entity_id)
        hops: list[RelationshipHop] = []
        emitted_edges: set[tuple[str, str, str]] = set()
        seen = {request.root_entity_id}
        queue = deque([(request.root_entity_id, 0)])
        edge_limited = node_limited = False
        while queue and root:
            current, depth = queue.popleft()
            if depth >= request.budget.max_depth:
                continue
            for fact in facts:
                if (
                    request.relationship_type_ids
                    and fact.relationship_type_id not in request.relationship_type_ids
                ):
                    continue
                pairs: list[tuple[str, str]] = []
                if fact.object.entity_id:
                    if (
                        request.direction in {"outgoing", "both"}
                        and fact.subject_entity_id == current
                    ):
                        pairs.append((fact.subject_entity_id, fact.object.entity_id))
                    if (
                        request.direction in {"incoming", "both"}
                        and fact.object.entity_id == current
                    ):
                        pairs.append((fact.subject_entity_id, fact.object.entity_id))
                for source_id, target_id in pairs:
                    source, target = by_id.get(source_id), by_id.get(target_id)
                    if not source or not target:
                        continue
                    edge_key = (fact.fact_id, source_id, target_id)
                    if edge_key in emitted_edges:
                        continue
                    if len(hops) >= request.budget.max_edges:
                        edge_limited = True
                        break
                    hop = RelationshipHop(
                        depth=depth + 1,
                        source=self._entity(source, lineages[source.type_id]),
                        relationship=self._fact(
                            fact, lineages[fact.relationship_type_id]
                        ),
                        target=self._entity(target, lineages[target.type_id]),
                    )
                    if hop not in hops:
                        hops.append(hop)
                        emitted_edges.add(edge_key)
                    neighbor = target_id if current == source_id else source_id
                    if neighbor not in seen:
                        if len(seen) >= request.budget.max_nodes:
                            node_limited = True
                        else:
                            seen.add(neighbor)
                            queue.append((neighbor, depth + 1))
                if edge_limited:
                    break
            if edge_limited:
                break
        hops.sort(
            key=lambda item: (
                item.depth,
                item.relationship.fact_id,
                item.source.entity_id,
                item.target.entity_id,
            )
        )
        shape = {
            "root": request.root_entity_id,
            "types": request.relationship_type_ids,
            "direction": request.direction,
            "depth": request.budget.max_depth,
        }
        page, cursor, truncated, gaps, page_state = self._page(
            hops,
            request=request,
            operation="traverse_relationships",
            snapshot=snapshot,
            shape=shape,
        )
        gap_list = list(gaps)
        if root is None:
            gap_list.append(
                OperationGap(
                    code="not-found", message="The visible root entity is unavailable."
                )
            )
        if edge_limited:
            gap_list.append(
                OperationGap(
                    code="edge-budget", message="Traversal reached its edge budget."
                )
            )
        if node_limited:
            gap_list.append(
                OperationGap(
                    code="node-budget", message="Traversal reached its node budget."
                )
            )
        nodes = {request.root_entity_id} if root else set()
        for hop in page:
            nodes.update((hop.source.entity_id, hop.target.entity_id))
        omitted = lineage_omitted or eo.present or fo.present
        result = TraverseRelationshipsResult(
            root=self._entity(root, lineages[root.type_id]) if root else None,
            nodes=tuple(
                self._entity(by_id[item], lineages[by_id[item].type_id])
                for item in sorted(nodes)
            ),
            hops=tuple(page),
            next_cursor=cursor,
            truncated=truncated or edge_limited or node_limited,
            gaps=tuple(gap_list),
            omissions=OperationOmissions(
                present=omitted, reasons=("policy",) if omitted else ()
            ),
            trace=self._trace("traverse_relationships", request, snapshot, lineages),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("nodes", "hops"),
            singular_fields=("root",),
            page_state=page_state,
        )

    def compare_observations(
        self, request: CompareObservationsRequest
    ) -> CompareObservationsResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(("type", "semantic_pack", "entity", "fact"))
        lineages, semantic_types, semantic_packs, lineage_omitted = self._lineages(
            rows, authorization
        )
        entities, eo = self._entities(
            (
                Entity.model_validate(row.payload)
                for row in rows
                if row.record_kind == "entity"
            ),
            authorization,
            lineages,
        )
        facts, fo = self._facts(
            (
                Fact.model_validate(row.payload)
                for row in rows
                if row.record_kind == "fact"
            ),
            authorization,
            {item.entity_id for item in entities},
            lineages,
        )
        omissions = OperationOmissions(
            present=lineage_omitted or eo.present or fo.present,
            reasons=("policy",) if lineage_omitted or eo.present or fo.present else (),
        )
        snapshot = self._permission_snapshot(
            (*semantic_types, *semantic_packs, *entities, *facts)
        )
        by_id = {item.fact_id: item for item in facts}
        selected = [by_id[item] for item in request.fact_ids if item in by_id]
        gaps = [
            OperationGap(
                code="not-found", message="A requested observation is unavailable."
            )
            for item in request.fact_ids
            if item not in by_id
        ]
        truncated = len(selected) > request.budget.max_items
        if truncated:
            selected = selected[: request.budget.max_items]
            gaps.append(
                OperationGap(
                    code="item-budget", message="The response reached its item budget."
                )
            )
        differences: list[ObservationDifference] = []
        if selected:
            values = {
                (item.relationship_type_id, item.object.model_dump_json())
                for item in selected
            }
            code = "agree" if len(values) == 1 else "conflict"
            differences.append(
                ObservationDifference(
                    code=code,
                    fact_ids=tuple(item.fact_id for item in selected),
                    message="Visible observations agree."
                    if code == "agree"
                    else "Visible observations conflict.",
                )
            )
            if request.stale_after_seconds is not None:
                as_of = request.as_of or datetime.now(UTC)
                stale = [
                    item.fact_id
                    for item in selected
                    if (as_of - item.provenance.observed_at).total_seconds()
                    > request.stale_after_seconds
                ]
                if stale:
                    differences.append(
                        ObservationDifference(
                            code="stale",
                            fact_ids=tuple(stale),
                            message="Visible observations exceed the requested freshness bound.",
                        )
                    )
        result = CompareObservationsResult(
            observations=tuple(
                self._fact(item, lineages[item.relationship_type_id])
                for item in selected
            ),
            differences=tuple(differences),
            truncated=truncated,
            gaps=tuple(gaps),
            omissions=omissions,
            trace=self._trace("compare_observations", request, snapshot, lineages),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("observations", "differences"),
        )

    def explain_conflict(
        self, request: ExplainConflictRequest
    ) -> ExplainConflictResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(
            ("type", "semantic_pack", "entity", "conflict", "fact")
        )
        lineages, semantic_types, semantic_packs, lineage_omitted = self._lineages(
            rows, authorization
        )
        entities, eo = self._entities(
            (
                Entity.model_validate(row.payload)
                for row in rows
                if row.record_kind == "entity"
            ),
            authorization,
            lineages,
        )
        conflicts, co = self._partition(
            (
                Conflict.model_validate(row.payload)
                for row in rows
                if row.record_kind == "conflict"
            ),
            authorization,
        )
        facts, fo = self._facts(
            (
                Fact.model_validate(row.payload)
                for row in rows
                if row.record_kind == "fact"
            ),
            authorization,
            {item.entity_id for item in entities},
            lineages,
        )
        snapshot = self._permission_snapshot(
            (
                *semantic_types,
                *semantic_packs,
                *entities,
                *self._conflict_snapshot(conflicts, {item.fact_id for item in facts}),
                *facts,
            )
        )
        conflict = next(
            (item for item in conflicts if item.conflict_id == request.conflict_id),
            None,
        )
        visible = {item.fact_id: item for item in facts}
        selected = (
            [visible[item] for item in conflict.fact_ids if item in visible]
            if conflict
            else []
        )
        gaps: list[OperationGap] = []
        if conflict is None:
            gaps.append(
                OperationGap(
                    code="not-found", message="The visible conflict is unavailable."
                )
            )
        elif len(selected) != len(conflict.fact_ids):
            gaps.append(
                OperationGap(
                    code="partial-evidence",
                    message="Some conflict evidence is unavailable.",
                )
            )
        truncated = len(selected) > request.budget.max_items
        if truncated:
            selected = selected[: request.budget.max_items]
            gaps.append(
                OperationGap(
                    code="item-budget", message="The response reached its item budget."
                )
            )
        resolution_fact_id = (
            conflict.resolution_fact_id
            if conflict and conflict.resolution_fact_id in visible
            else None
        )
        if conflict and conflict.resolution_fact_id and resolution_fact_id is None:
            gaps.append(
                OperationGap(
                    code="partial-evidence",
                    message="The conflict resolution evidence is unavailable.",
                )
            )
        omitted = lineage_omitted or eo.present or co.present or fo.present
        result = ExplainConflictResult(
            conflict_id=conflict.conflict_id if conflict else None,
            state=conflict.state if conflict else None,
            reason=conflict.reason if conflict else None,
            facts=tuple(
                self._fact(item, lineages[item.relationship_type_id])
                for item in selected
            ),
            resolution_fact_id=resolution_fact_id,
            explanation="The conflict is grounded in the visible exact fact revisions."
            if conflict
            else "",
            truncated=truncated,
            gaps=tuple(gaps),
            omissions=OperationOmissions(
                present=omitted, reasons=("policy",) if omitted else ()
            ),
            trace=self._trace("explain_conflict", request, snapshot, lineages),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("facts",),
            singular_fields=("conflict_id", "state", "reason", "resolution_fact_id"),
        )

    def assemble_cross_source_evidence(
        self, request: AssembleCrossSourceEvidenceRequest
    ) -> AssembleCrossSourceEvidenceResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(
            ("type", "semantic_pack", "entity", "fact", "conflict")
        )
        lineages, semantic_types, semantic_packs, lineage_omitted = self._lineages(
            rows, authorization
        )
        entities, eo = self._entities(
            (
                Entity.model_validate(row.payload)
                for row in rows
                if row.record_kind == "entity"
            ),
            authorization,
            lineages,
        )
        facts, fo = self._facts(
            (
                Fact.model_validate(row.payload)
                for row in rows
                if row.record_kind == "fact"
            ),
            authorization,
            {item.entity_id for item in entities},
            lineages,
        )
        conflicts, co = self._partition(
            (
                Conflict.model_validate(row.payload)
                for row in rows
                if row.record_kind == "conflict"
            ),
            authorization,
        )
        snapshot = self._permission_snapshot(
            (
                *semantic_types,
                *semantic_packs,
                *entities,
                *facts,
                *self._conflict_snapshot(conflicts, {item.fact_id for item in facts}),
            )
        )
        conflicted = {
            fact_id
            for item in conflicts
            if item.state == "open"
            for fact_id in item.fact_ids
        }
        statements = [
            EvidenceStatement(
                fact=self._fact(item, lineages[item.relationship_type_id]),
                citations=item.provenance.evidence,
                conflicted=item.fact_id in conflicted,
            )
            for item in facts
            if item.subject_entity_id in request.entity_ids
            and (
                not request.relationship_type_ids
                or item.relationship_type_id in request.relationship_type_ids
            )
        ]
        statements.sort(key=lambda item: item.fact.fact_id)
        shape = {
            "entities": request.entity_ids,
            "types": request.relationship_type_ids,
            "decision": request.decision,
        }
        page, cursor, truncated, gaps, page_state = self._page(
            statements,
            request=request,
            operation="assemble_cross_source_evidence",
            snapshot=snapshot,
            shape=shape,
        )
        citations = {
            item.model_dump_json(): item
            for statement in page
            for item in statement.citations
        }
        citation_limited = len(citations) > 256
        citation_keys = sorted(citations)[:256]
        if citation_limited:
            gaps = (
                *gaps,
                OperationGap(
                    code="item-budget",
                    message="The citation projection reached its item budget.",
                ),
            )
        omitted = lineage_omitted or eo.present or fo.present or co.present
        result = AssembleCrossSourceEvidenceResult(
            decision=request.decision,
            statements=tuple(page),
            citations=tuple(citations[key] for key in citation_keys),
            next_cursor=cursor,
            truncated=truncated or citation_limited,
            gaps=gaps,
            omissions=OperationOmissions(
                present=omitted, reasons=("policy",) if omitted else ()
            ),
            trace=self._trace(
                "assemble_cross_source_evidence", request, snapshot, lineages
            ),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("statements", "citations"),
            singular_fields=("decision",),
            page_state=page_state,
        )

    def list_ontology_packages(
        self, request: ListOntologyPackagesRequest
    ) -> ListOntologyPackagesResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(
            ("ontology_package", "ontology_package_version", "semantic_pack", "type")
        )
        versions = {
            row.record_id: OntologyPackageVersion.model_validate(row.payload)
            for row in rows
            if row.record_kind == "ontology_package_version"
        }
        packages: list[OntologyPackageView] = []
        omitted = False
        for row in rows:
            if row.record_kind != "ontology_package":
                continue
            item = OntologyPackage.model_validate(row.payload)
            if item.state not in request.states:
                continue
            version = versions.get(f"{item.package_id}@{item.proposed_version}")
            if (
                version is None
                or version.change_set_digest != item.change_set_digest
                or version.content_digest != item.reviewed_content_digest
                or not self._visible(version, authorization)
            ):
                continue
            packages.append(
                OntologyPackageView(
                    package_id=item.package_id,
                    state=item.state,
                    proposed_version=item.proposed_version,
                    published_version=item.published_version,
                    change_set_id=item.change_set_id,
                    change_set_digest=item.change_set_digest,
                    reviewed_content_digest=item.reviewed_content_digest,
                    version_content_digest=version.content_digest,
                    provenance=self._provenance(version),
                    reviewer=item.reviewer,
                    review_digest=item.review_digest,
                    revision=item.revision,
                )
            )
        semantic_packs: list[SemanticPackView] = []
        lineages, _types, authorized_packs, lineage_omitted = self._lineages(
            rows, authorization
        )
        authorized_pack_keys = {
            (item.pack_id, item.version) for item in authorized_packs
        }
        for row in rows:
            if row.record_kind != "semantic_pack":
                continue
            pack = SemanticPack.model_validate(row.payload)
            verification = pack.signature_verification
            if (
                verification is None
                or verification.status != "trusted"
                or not self._visible(pack, authorization)
                or (pack.pack_id, pack.version) not in authorized_pack_keys
                or any(
                    type_id not in lineages
                    or lineages[type_id].pack_id != pack.pack_id
                    or lineages[type_id].pack_version != pack.version
                    for type_id in pack.type_ids
                )
            ):
                continue
            semantic_packs.append(
                SemanticPackView(
                    pack_id=pack.pack_id,
                    version=pack.version,
                    content_digest=pack.content_digest,
                    type_ids=pack.type_ids,
                    signature_issuer=pack.signature.issuer,
                    signature_key_id=pack.signature.key_id,
                    verification_digest=verification.request_digest,
                    provenance=self._provenance(pack),
                    revision=pack.revision,
                )
            )
        combined: list[Any] = sorted(
            (*packages, *semantic_packs),
            key=lambda item: (
                item.__class__.__name__,
                item.pack_id if hasattr(item, "pack_id") else item.package_id,
            ),
        )
        snapshot = self._permission_snapshot(combined)
        package_shape = {"states": request.states}
        page, cursor, truncated, gaps, page_state = self._page(
            combined,
            request=request,
            operation="list_ontology_packages",
            snapshot=snapshot,
            shape=package_shape,
        )
        omitted = omitted or lineage_omitted
        result = ListOntologyPackagesResult(
            packages=tuple(
                item for item in page if isinstance(item, OntologyPackageView)
            ),
            semantic_packs=tuple(
                item for item in page if isinstance(item, SemanticPackView)
            ),
            next_cursor=cursor,
            truncated=truncated,
            gaps=gaps,
            omissions=OperationOmissions(
                present=omitted, reasons=("policy",) if omitted else ()
            ),
            trace=self._trace("list_ontology_packages", request, snapshot, lineages),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("packages", "semantic_packs"),
            page_state=page_state,
        )

    def list_ontology_proposal_history(
        self, request: ListOntologyProposalHistoryRequest
    ) -> ListOntologyProposalHistoryResult:
        started_at = self._clock()
        authorization = self._scope(request.scope)
        rows, snapshot = self._snapshot(
            ("ontology_change_set", "ontology_package", "ontology_package_version")
        )
        proposals = [
            OntologyChangeSet.model_validate(row.payload)
            for row in rows
            if row.record_kind == "ontology_change_set"
        ]
        packages = {
            item.package_id: item
            for item in (
                OntologyPackage.model_validate(row.payload)
                for row in rows
                if row.record_kind == "ontology_package"
            )
        }
        versions = {
            (item.package_id, item.version): item
            for item in (
                OntologyPackageVersion.model_validate(row.payload)
                for row in rows
                if row.record_kind == "ontology_package_version"
            )
        }
        views: list[OntologyProposalHistoryView] = []
        omitted = False
        for item in proposals:
            if item.package_id != request.package_id:
                continue
            package = packages.get(item.package_id)
            if (
                item.authorship.actor_id != request.scope.subject_id
                or item.authorship.policy_digest != request.scope.policy_digest
                or package is None
                or package.change_set_id != item.change_set_id
                or package.change_set_digest != item.change_digest
                or package.authorship != item.authorship
            ):
                continue
            version = versions.get((package.package_id, package.proposed_version))
            if (
                version is None
                or version.change_set_digest != item.change_digest
                or not self._visible(version, authorization)
            ):
                continue
            views.append(
                OntologyProposalHistoryView(
                    change_set_id=item.change_set_id,
                    package_id=item.package_id,
                    state=item.state,
                    change_digest=item.change_digest,
                    proposed_version=version.version,
                    proposal_content_digest=version.content_digest,
                    provenance=self._provenance(version),
                    base_version=item.base_version,
                    base_digest=item.base_digest,
                    supersedes_change_set_id=item.supersedes_change_set_id,
                    revision=item.revision,
                )
            )
        views.sort(key=lambda item: (item.revision, item.change_set_id))
        snapshot = self._permission_snapshot(views)
        proposal_shape = {"package_id": request.package_id}
        page, cursor, truncated, gaps, page_state = self._page(
            views,
            request=request,
            operation="list_ontology_proposal_history",
            snapshot=snapshot,
            shape=proposal_shape,
        )
        result = ListOntologyProposalHistoryResult(
            proposals=tuple(page),
            next_cursor=cursor,
            truncated=truncated,
            gaps=gaps,
            omissions=OperationOmissions(
                present=omitted,
                reasons=("policy",) if omitted else (),
            ),
            trace=self._trace("list_ontology_proposal_history", request, snapshot),
        )
        return self._finish(
            result,
            request,
            started_at=started_at,
            collection_fields=("proposals",),
            page_state=page_state,
        )


__all__ = [
    "AuthorizedSemanticScope",
    "SEMANTIC_PROCESSING_DIGEST",
    "SEMANTIC_PROCESSING_VERSION",
    "SemanticOperationsService",
    "authorize_semantic_scope_from_claims",
    "semantic_scope_authorizer_from_context",
]
