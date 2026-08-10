"""Bounded, policy-shaped semantic graph reads over the governed kernel."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections import deque
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Sequence

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
    canonical_digest,
)
from rail.semantic.packs import SemanticPack
from rail.semantic.repository import SemanticRepository, SemanticRow


SEMANTIC_PROCESSING_VERSION = "semantic-operations/1.0.0"
SEMANTIC_PROCESSING_DIGEST = canonical_digest(
    {"component": "semantic-operations", "version": SEMANTIC_PROCESSING_VERSION}
)


class SemanticOperationsService:
    """Six fixed graph reads plus governed package projections; never a query DSL."""

    def __init__(
        self,
        repository: SemanticRepository,
        *,
        cursor_key: bytes,
        authorize_scope: Callable[[SemanticReadScope], SemanticReadScope],
    ) -> None:
        if len(cursor_key) < 32:
            raise ValueError("semantic cursor key must contain at least 32 bytes")
        self.repository = repository
        self._cursor_key = bytes(cursor_key)
        self._authorize_scope = authorize_scope

    def _scope(self, scope: SemanticReadScope) -> None:
        verified = self._authorize_scope(scope)
        if (
            verified != scope
            or scope.tenant_id != self.repository.tenant_id
            or scope.project_id != self.repository.project_id
        ):
            raise PermissionError("semantic scope is unavailable")

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
    def _visible(value: Any, scope: SemanticReadScope) -> bool:
        provenance = getattr(value, "provenance", None)
        if provenance is None:
            return True
        authorities = set(scope.allowed_authorities)
        resource_types = set(scope.allowed_resource_types)
        return all(
            ref.authority in authorities
            and (not resource_types or ref.resource_type in resource_types)
            for ref in provenance.evidence
        )

    def _partition(self, values: Iterable[Any], scope: SemanticReadScope) -> tuple[list[Any], OperationOmissions]:
        visible: list[Any] = []
        omitted = False
        for value in values:
            if self._visible(value, scope):
                visible.append(value)
            else:
                omitted = True
        return visible, OperationOmissions(present=omitted, reasons=("authority",) if omitted else ())

    def _facts(
        self, values: Iterable[Fact], scope: SemanticReadScope, visible_entity_ids: set[str]
    ) -> tuple[list[Fact], OperationOmissions]:
        authorized, omissions = self._partition(values, scope)
        visible = [
            fact
            for fact in authorized
            if fact.subject_entity_id in visible_entity_ids
            and (fact.object.entity_id is None or fact.object.entity_id in visible_entity_ids)
        ]
        omitted = omissions.present or len(visible) != len(authorized)
        return visible, OperationOmissions(present=omitted, reasons=("authority",) if omitted else ())

    def _cursor(
        self, *, operation: str, scope: SemanticReadScope, snapshot: str, shape: object, offset: int
    ) -> str:
        body = {
            "operation": operation,
            "scope": scope.scope_digest,
            "snapshot": snapshot,
            "shape": canonical_digest(shape),
            "offset": offset,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._cursor_key, raw, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(raw + b"." + signature.encode()).decode().rstrip("=")

    def _offset(
        self, cursor: str | None, *, operation: str, scope: SemanticReadScope, snapshot: str, shape: object
    ) -> tuple[int, bool]:
        if cursor is None:
            return 0, False
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            body_raw, supplied = raw.rsplit(b".", 1)
            expected = hmac.new(self._cursor_key, body_raw, hashlib.sha256).hexdigest().encode()
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            body = json.loads(body_raw)
            if (
                body["operation"] != operation
                or body["scope"] != scope.scope_digest
                or body["shape"] != canonical_digest(shape)
            ):
                raise ValueError
            if body["snapshot"] != snapshot:
                return 0, True
            offset = int(body["offset"])
            if offset < 0:
                raise ValueError
            return offset, False
        except Exception as exc:
            raise ValueError("semantic cursor is invalid for this operation and scope") from exc

    @staticmethod
    def _trace(operation: str, request: Any, snapshot: str) -> OperationTrace:
        return OperationTrace(
            operation=operation,
            request_digest=canonical_digest(request.model_dump(mode="json")),
            snapshot_digest=snapshot,
            processing_version=SEMANTIC_PROCESSING_VERSION,
            processing_digest=SEMANTIC_PROCESSING_DIGEST,
        )

    def _page(
        self, values: Sequence[Any], *, request: Any, operation: str, snapshot: str, shape: object
    ) -> tuple[list[Any], str | None, bool, tuple[OperationGap, ...]]:
        offset, stale = self._offset(
            request.cursor, operation=operation, scope=request.scope, snapshot=snapshot, shape=shape
        )
        if stale:
            return [], None, True, (OperationGap(code="cursor-stale", message="The graph snapshot changed; restart the read."),)
        limit = min(request.budget.max_items, request.budget.max_nodes)
        selected: list[Any] = []
        byte_total = 0
        byte_limited = False
        for value in values[offset : offset + limit]:
            size = len(json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode())
            if byte_total + size > request.budget.max_bytes:
                byte_limited = True
                break
            selected.append(value)
            byte_total += size
        consumed = len(selected)
        more = offset + consumed < len(values)
        truncated = more or byte_limited
        next_cursor = (
            self._cursor(
                operation=operation,
                scope=request.scope,
                snapshot=snapshot,
                shape=shape,
                offset=offset + consumed,
            )
            if more and consumed
            else None
        )
        gaps: list[OperationGap] = []
        if byte_limited:
            gaps.append(OperationGap(code="byte-budget", message="The response reached its byte budget."))
        elif more:
            gaps.append(OperationGap(code="item-budget", message="The response reached its item budget."))
        return selected, next_cursor, truncated, tuple(gaps)

    @staticmethod
    def _provenance(value: Any) -> ProvenanceView:
        return ProvenanceView(
            evidence=value.provenance.evidence,
            observed_at=value.provenance.observed_at,
            processing_version=value.provenance.processing_version,
            processing_digest=value.provenance.processing_digest,
        )

    @classmethod
    def _entity(cls, value: Entity) -> EntityView:
        return EntityView(
            entity_id=value.entity_id,
            type_id=value.type_id,
            canonical_name=value.canonical_name,
            state=value.state,
            merged_into=value.merged_into,
            revision=value.revision,
            provenance=cls._provenance(value),
        )

    @classmethod
    def _alias(cls, value: Alias) -> AliasView:
        return AliasView(
            alias_id=value.alias_id,
            entity_id=value.entity_id,
            value=value.value,
            normalized_value=value.normalized_value,
            revision=value.revision,
            provenance=cls._provenance(value),
        )

    @classmethod
    def _fact(cls, value: Fact) -> FactView:
        return FactView(
            fact_id=value.fact_id,
            subject_entity_id=value.subject_entity_id,
            relationship_type_id=value.relationship_type_id,
            object=FactObjectView(entity_id=value.object.entity_id, literal=value.object.literal),
            assertion_kind=value.assertion_kind,
            confidence=value.confidence,
            valid_from=value.valid_from,
            valid_until=value.valid_until,
            revision=value.revision,
            provenance=cls._provenance(value),
        )

    def resolve_entity(self, request: ResolveEntityRequest) -> ResolveEntityResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("entity", "alias"))
        entities, entity_omissions = self._partition(
            (Entity.model_validate(row.payload) for row in rows if row.record_kind == "entity"), request.scope
        )
        aliases, alias_omissions = self._partition(
            (Alias.model_validate(row.payload) for row in rows if row.record_kind == "alias"), request.scope
        )
        aliases_by_entity: dict[str, list[Alias]] = {}
        for alias in aliases:
            aliases_by_entity.setdefault(alias.entity_id, []).append(alias)
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
                    (item for item in aliases_by_entity.get(entity.entity_id, ()) if item.normalized_value.casefold() == query),
                    None,
                )
                if alias_match:
                    match = "alias"
            if match:
                candidates.append(
                    ResolveCandidate(
                        entity=self._entity(entity), match=match, alias=self._alias(alias_match) if alias_match else None
                    )
                )
        candidates.sort(key=lambda item: (item.match != "entity-id", item.entity.entity_id))
        shape = {"query": request.query, "type_ids": request.type_ids}
        page, cursor, truncated, gaps = self._page(candidates, request=request, operation="resolve_entity", snapshot=snapshot, shape=shape)
        if not page and not gaps:
            gaps = (OperationGap(code="not-found", message="No visible entity matched the query."),)
        omissions = OperationOmissions(
            present=entity_omissions.present or alias_omissions.present,
            reasons=("authority",) if entity_omissions.present or alias_omissions.present else (),
        )
        return ResolveEntityResult(candidates=page, next_cursor=cursor, truncated=truncated, gaps=gaps, omissions=omissions, trace=self._trace("resolve_entity", request, snapshot))

    def get_entity(self, request: GetEntityRequest) -> GetEntityResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("entity", "alias", "fact"))
        entities, eo = self._partition((Entity.model_validate(row.payload) for row in rows if row.record_kind == "entity"), request.scope)
        entity = next((item for item in entities if item.entity_id == request.entity_id), None)
        aliases, ao = self._partition((Alias.model_validate(row.payload) for row in rows if row.record_kind == "alias"), request.scope)
        facts, fo = self._facts(
            (Fact.model_validate(row.payload) for row in rows if row.record_kind == "fact"),
            request.scope,
            {item.entity_id for item in entities},
        )
        values: list[Any] = []
        if entity and request.include_aliases:
            values.extend(self._alias(item) for item in aliases if item.entity_id == entity.entity_id)
        if entity and request.include_facts:
            values.extend(self._fact(item) for item in facts if item.subject_entity_id == entity.entity_id or item.object.entity_id == entity.entity_id)
        values.sort(key=lambda item: (item.__class__.__name__, getattr(item, "alias_id", getattr(item, "fact_id", ""))))
        shape = {"entity_id": request.entity_id, "aliases": request.include_aliases, "facts": request.include_facts}
        page, cursor, truncated, gaps = self._page(values, request=request, operation="get_entity", snapshot=snapshot, shape=shape)
        aliases_page = tuple(item for item in page if isinstance(item, AliasView))
        facts_page = tuple(item for item in page if isinstance(item, FactView))
        if entity is None and not gaps:
            gaps = (OperationGap(code="not-found", message="The visible entity is unavailable."),)
        omitted = eo.present or ao.present or fo.present
        return GetEntityResult(entity=self._entity(entity) if entity else None, aliases=aliases_page, facts=facts_page, next_cursor=cursor, truncated=truncated, gaps=gaps, omissions=OperationOmissions(present=omitted, reasons=("authority",) if omitted else ()), trace=self._trace("get_entity", request, snapshot))

    def traverse_relationships(self, request: TraverseRelationshipsRequest) -> TraverseRelationshipsResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("entity", "fact"))
        entities, eo = self._partition((Entity.model_validate(row.payload) for row in rows if row.record_kind == "entity"), request.scope)
        facts, fo = self._facts(
            (Fact.model_validate(row.payload) for row in rows if row.record_kind == "fact"),
            request.scope,
            {item.entity_id for item in entities},
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
                if request.relationship_type_ids and fact.relationship_type_id not in request.relationship_type_ids:
                    continue
                pairs: list[tuple[str, str]] = []
                if fact.object.entity_id:
                    if request.direction in {"outgoing", "both"} and fact.subject_entity_id == current:
                        pairs.append((fact.subject_entity_id, fact.object.entity_id))
                    if request.direction in {"incoming", "both"} and fact.object.entity_id == current:
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
                    hop = RelationshipHop(depth=depth + 1, source=self._entity(source), relationship=self._fact(fact), target=self._entity(target))
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
        hops.sort(key=lambda item: (item.depth, item.relationship.fact_id, item.source.entity_id, item.target.entity_id))
        shape = {"root": request.root_entity_id, "types": request.relationship_type_ids, "direction": request.direction, "depth": request.budget.max_depth}
        page, cursor, truncated, gaps = self._page(hops, request=request, operation="traverse_relationships", snapshot=snapshot, shape=shape)
        gap_list = list(gaps)
        if root is None:
            gap_list.append(OperationGap(code="not-found", message="The visible root entity is unavailable."))
        if edge_limited:
            gap_list.append(OperationGap(code="edge-budget", message="Traversal reached its edge budget."))
        if node_limited:
            gap_list.append(OperationGap(code="node-budget", message="Traversal reached its node budget."))
        nodes = {request.root_entity_id} if root else set()
        for hop in page:
            nodes.update((hop.source.entity_id, hop.target.entity_id))
        omitted = eo.present or fo.present
        return TraverseRelationshipsResult(root=self._entity(root) if root else None, nodes=tuple(self._entity(by_id[item]) for item in sorted(nodes)), hops=tuple(page), next_cursor=cursor, truncated=truncated or edge_limited or node_limited, gaps=tuple(gap_list), omissions=OperationOmissions(present=omitted, reasons=("authority",) if omitted else ()), trace=self._trace("traverse_relationships", request, snapshot))

    def compare_observations(self, request: CompareObservationsRequest) -> CompareObservationsResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("entity", "fact"))
        entities, eo = self._partition(
            (Entity.model_validate(row.payload) for row in rows if row.record_kind == "entity"), request.scope
        )
        facts, fo = self._facts(
            (Fact.model_validate(row.payload) for row in rows if row.record_kind == "fact"),
            request.scope,
            {item.entity_id for item in entities},
        )
        omissions = OperationOmissions(
            present=eo.present or fo.present,
            reasons=("authority",) if eo.present or fo.present else (),
        )
        by_id = {item.fact_id: item for item in facts}
        selected = [by_id[item] for item in request.fact_ids if item in by_id]
        gaps = [OperationGap(code="not-found", message="A requested observation is unavailable.") for item in request.fact_ids if item not in by_id]
        truncated = len(selected) > request.budget.max_items
        if truncated:
            selected = selected[: request.budget.max_items]
            gaps.append(OperationGap(code="item-budget", message="The response reached its item budget."))
        differences: list[ObservationDifference] = []
        if selected:
            values = {(item.relationship_type_id, item.object.model_dump_json()) for item in selected}
            code = "agree" if len(values) == 1 else "conflict"
            differences.append(ObservationDifference(code=code, fact_ids=tuple(item.fact_id for item in selected), message="Visible observations agree." if code == "agree" else "Visible observations conflict."))
            if request.stale_after_seconds is not None:
                as_of = request.as_of or datetime.now(UTC)
                stale = [item.fact_id for item in selected if (as_of - item.provenance.observed_at).total_seconds() > request.stale_after_seconds]
                if stale:
                    differences.append(ObservationDifference(code="stale", fact_ids=tuple(stale), message="Visible observations exceed the requested freshness bound."))
        return CompareObservationsResult(observations=tuple(self._fact(item) for item in selected), differences=tuple(differences), truncated=truncated, gaps=tuple(gaps), omissions=omissions, trace=self._trace("compare_observations", request, snapshot))

    def explain_conflict(self, request: ExplainConflictRequest) -> ExplainConflictResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("entity", "conflict", "fact"))
        entities, eo = self._partition((Entity.model_validate(row.payload) for row in rows if row.record_kind == "entity"), request.scope)
        conflicts, co = self._partition((Conflict.model_validate(row.payload) for row in rows if row.record_kind == "conflict"), request.scope)
        facts, fo = self._facts(
            (Fact.model_validate(row.payload) for row in rows if row.record_kind == "fact"),
            request.scope,
            {item.entity_id for item in entities},
        )
        conflict = next((item for item in conflicts if item.conflict_id == request.conflict_id), None)
        visible = {item.fact_id: item for item in facts}
        selected = [visible[item] for item in conflict.fact_ids if item in visible] if conflict else []
        gaps: list[OperationGap] = []
        if conflict is None:
            gaps.append(OperationGap(code="not-found", message="The visible conflict is unavailable."))
        elif len(selected) != len(conflict.fact_ids):
            gaps.append(OperationGap(code="partial-evidence", message="Some conflict evidence is unavailable."))
        truncated = len(selected) > request.budget.max_items
        if truncated:
            selected = selected[: request.budget.max_items]
            gaps.append(OperationGap(code="item-budget", message="The response reached its item budget."))
        omitted = eo.present or co.present or fo.present
        return ExplainConflictResult(conflict_id=conflict.conflict_id if conflict else None, state=conflict.state if conflict else None, reason=conflict.reason if conflict else None, facts=tuple(self._fact(item) for item in selected), resolution_fact_id=conflict.resolution_fact_id if conflict else None, explanation="The conflict is grounded in the visible exact fact revisions." if conflict else "", truncated=truncated, gaps=tuple(gaps), omissions=OperationOmissions(present=omitted, reasons=("authority",) if omitted else ()), trace=self._trace("explain_conflict", request, snapshot))

    def assemble_cross_source_evidence(self, request: AssembleCrossSourceEvidenceRequest) -> AssembleCrossSourceEvidenceResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("entity", "fact", "conflict"))
        entities, eo = self._partition((Entity.model_validate(row.payload) for row in rows if row.record_kind == "entity"), request.scope)
        facts, fo = self._facts(
            (Fact.model_validate(row.payload) for row in rows if row.record_kind == "fact"),
            request.scope,
            {item.entity_id for item in entities},
        )
        conflicts, co = self._partition((Conflict.model_validate(row.payload) for row in rows if row.record_kind == "conflict"), request.scope)
        conflicted = {fact_id for item in conflicts if item.state == "open" for fact_id in item.fact_ids}
        statements = [
            EvidenceStatement(fact=self._fact(item), citations=item.provenance.evidence, conflicted=item.fact_id in conflicted)
            for item in facts
            if item.subject_entity_id in request.entity_ids
            and (not request.relationship_type_ids or item.relationship_type_id in request.relationship_type_ids)
        ]
        statements.sort(key=lambda item: item.fact.fact_id)
        shape = {"entities": request.entity_ids, "types": request.relationship_type_ids, "decision": request.decision}
        page, cursor, truncated, gaps = self._page(statements, request=request, operation="assemble_cross_source_evidence", snapshot=snapshot, shape=shape)
        citations = {item.model_dump_json(): item for statement in page for item in statement.citations}
        citation_limited = len(citations) > 256
        citation_keys = sorted(citations)[:256]
        if citation_limited:
            gaps = (*gaps, OperationGap(code="item-budget", message="The citation projection reached its item budget."))
        omitted = eo.present or fo.present or co.present
        return AssembleCrossSourceEvidenceResult(decision=request.decision, statements=tuple(page), citations=tuple(citations[key] for key in citation_keys), next_cursor=cursor, truncated=truncated or citation_limited, gaps=gaps, omissions=OperationOmissions(present=omitted, reasons=("authority",) if omitted else ()), trace=self._trace("assemble_cross_source_evidence", request, snapshot))

    def list_ontology_packages(self, request: ListOntologyPackagesRequest) -> ListOntologyPackagesResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("ontology_package", "ontology_package_version", "semantic_pack"))
        versions = {row.record_id: OntologyPackageVersion.model_validate(row.payload) for row in rows if row.record_kind == "ontology_package_version"}
        packages: list[OntologyPackageView] = []
        omitted = False
        for row in rows:
            if row.record_kind != "ontology_package":
                continue
            item = OntologyPackage.model_validate(row.payload)
            if item.state not in request.states:
                continue
            version = versions.get(f"{item.package_id}@{item.proposed_version}")
            if version is None or not self._visible(version, request.scope):
                omitted = True
                continue
            packages.append(OntologyPackageView(package_id=item.package_id, state=item.state, proposed_version=item.proposed_version, published_version=item.published_version, change_set_id=item.change_set_id, change_set_digest=item.change_set_digest, reviewed_content_digest=item.reviewed_content_digest, reviewer=item.reviewer, review_digest=item.review_digest, revision=item.revision))
        semantic_packs: list[SemanticPackView] = []
        for row in rows:
            if row.record_kind != "semantic_pack":
                continue
            pack = SemanticPack.model_validate(row.payload)
            verification = pack.signature_verification
            if verification is None or verification.status != "trusted" or not self._visible(pack, request.scope):
                omitted = True
                continue
            semantic_packs.append(SemanticPackView(pack_id=pack.pack_id, version=pack.version, content_digest=pack.content_digest, type_ids=pack.type_ids, signature_issuer=pack.signature.issuer, signature_key_id=pack.signature.key_id, verification_digest=verification.request_digest, revision=pack.revision))
        combined: list[Any] = sorted((*packages, *semantic_packs), key=lambda item: (item.__class__.__name__, item.pack_id if hasattr(item, "pack_id") else item.package_id))
        page, cursor, truncated, gaps = self._page(combined, request=request, operation="list_ontology_packages", snapshot=snapshot, shape={"states": request.states})
        return ListOntologyPackagesResult(packages=tuple(item for item in page if isinstance(item, OntologyPackageView)), semantic_packs=tuple(item for item in page if isinstance(item, SemanticPackView)), next_cursor=cursor, truncated=truncated, gaps=gaps, omissions=OperationOmissions(present=omitted, reasons=("authority",) if omitted else ()), trace=self._trace("list_ontology_packages", request, snapshot))

    def list_ontology_proposal_history(self, request: ListOntologyProposalHistoryRequest) -> ListOntologyProposalHistoryResult:
        self._scope(request.scope)
        rows, snapshot = self._snapshot(("ontology_change_set",))
        proposals = [OntologyChangeSet.model_validate(row.payload) for row in rows]
        # Change sets are visible only when their authorship policy is the exact live policy.
        visible = [item for item in proposals if item.package_id == request.package_id and item.authorship.policy_digest == request.scope.policy_digest]
        omitted = len(visible) != len([item for item in proposals if item.package_id == request.package_id])
        views = [OntologyProposalHistoryView(change_set_id=item.change_set_id, package_id=item.package_id, state=item.state, change_digest=item.change_digest, base_version=item.base_version, base_digest=item.base_digest, supersedes_change_set_id=item.supersedes_change_set_id, revision=item.revision) for item in visible]
        views.sort(key=lambda item: (item.revision, item.change_set_id))
        page, cursor, truncated, gaps = self._page(views, request=request, operation="list_ontology_proposal_history", snapshot=snapshot, shape={"package_id": request.package_id})
        return ListOntologyProposalHistoryResult(proposals=tuple(page), next_cursor=cursor, truncated=truncated, gaps=gaps, omissions=OperationOmissions(present=omitted, reasons=("policy",) if omitted else ()), trace=self._trace("list_ontology_proposal_history", request, snapshot))


__all__ = ["SEMANTIC_PROCESSING_DIGEST", "SEMANTIC_PROCESSING_VERSION", "SemanticOperationsService"]
