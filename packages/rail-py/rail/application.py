"""Cohesive local application services and the provider-v1 adapter.

The legacy :class:`KnowledgeRuntime` remains the compatibility facade.  It owns
the mature implementation details while this module owns application routing and
the implementation-independent provider boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from krail.epistemic_history import EpistemicHistory
from krail.provider.capabilities import CapabilityNegotiationRequest

from krail.provider.v1 import (
    CONTRACT_ID,
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
    IntegrityFinding,
    IntegrityRequest,
    IntegrityResult,
    LineageEdge,
    LineageRequest,
    LineageResult,
    MAX_EVIDENCE_ITEM_BYTES,
    MAX_LINEAGE_EDGES,
    ProviderInfoRequest,
    ProviderInfoResult,
    ResourcePayload,
    ResourceRef,
    ResourceTypeDescriptor,
    RetrieveEvidenceRequest,
    RetrieveEvidenceResult,
    SearchHit,
    SearchRequest,
    SearchResult,
)

if TYPE_CHECKING:
    from rail.knowledge import KnowledgeRuntime


CAPABILITIES = [
    "describe_types",
    "search",
    "find",
    "get_resource",
    "retrieve_evidence",
    "explain",
    "lineage",
    "integrity",
]


def _distribution_version() -> str:
    try:
        return version("krail")
    except PackageNotFoundError:
        import rail

        return rail.__version__


def _utf8_prefix(value: str, byte_limit: int) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    if len(raw) <= byte_limit:
        return value, False
    return raw[:byte_limit].decode("utf-8", errors="ignore"), True


class KnowledgeApplicationService:
    """One application seam for capture, promotion, topics, and retrieval."""

    def __init__(self, runtime: "KnowledgeRuntime") -> None:
        self.runtime = runtime
        self.provider = LocalKnowledgeProvider(self)
        from rail.context_brief import ContextBriefService

        self.context_briefs = ContextBriefService(
            self.provider,
            EpistemicHistory(runtime.project_path),
        )
        from rail.capability_publication import LocalCapabilityPublication
        from rail.outcome_observations import OutcomeObservationService
        from rail.verification_evidence import VerificationEvidenceService

        self.capability_publication = LocalCapabilityPublication()
        self.verification_evidence = VerificationEvidenceService()
        self.outcome_observations = OutcomeObservationService()
        from rail.permissions import PermissionPolicy
        from rail.semantic import (
            JsonSemanticStore,
            SemanticOperationsService,
            SemanticRepository,
        )
        from rail.semantic.operations import AuthorizedSemanticScope

        semantic_repository = SemanticRepository(
            JsonSemanticStore(runtime.project_path / ".krail" / "semantic.json"),
            tenant_id="local",
            project_id=runtime.project_path.name,
        )
        permission_policy = PermissionPolicy(runtime.project_path)

        def authorize_local_semantic_scope(scope):
            # The request proposes exact bindings; local policy reconstructs
            # their classification and read decision from project state. Hosted
            # deployments inject signed-context verification instead.
            if (
                scope.tenant_id != "local"
                or scope.project_id != runtime.project_path.name
            ):
                raise PermissionError("semantic scope is unavailable")
            evidence_keys = set()
            for requested in scope.allowed_sources:
                target = requested.source.resource_id
                metadata = permission_policy.metadata_for_path(target)
                decision = permission_policy.authorize("read", target, metadata)
                visibility = str(
                    metadata.get("classification")
                    or metadata.get("visibility")
                    or "public"
                ).lower()
                classification = (
                    visibility
                    if visibility
                    in {"public", "internal", "confidential", "restricted"}
                    else "public"
                )
                source_id = str(metadata.get("source_id") or target)
                if decision.allowed and (source_id, classification) == (
                    requested.source_id,
                    requested.classification,
                ):
                    evidence_keys.add(requested.source.exact_key)
            return AuthorizedSemanticScope(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                subject_id=scope.subject_id,
                policy_digest=scope.policy_digest,
                authorization_context_digest=scope.scope_digest,
                request_scope_digest=scope.scope_digest,
                evidence_keys=frozenset(evidence_keys),
            )

        self.semantic_operations = SemanticOperationsService(
            semantic_repository,
            cursor_key=hashlib.sha256(
                ("krail.semantic-cursor:" + str(runtime.project_path)).encode()
            ).digest(),
            authorize_scope=authorize_local_semantic_scope,
        )

    def context_brief(self, request):
        """Assemble a bounded brief without performing provider or external writes."""
        return self.context_briefs.assemble(request)

    def assemble_verification_evidence(self, request):
        """Interpret supplied bounded artifacts without executing or mutating."""
        return self.verification_evidence.assemble(request)

    def ingest_outcome_evidence(self, envelope):
        """Interpret a pinned provider observation without fetching newer state."""
        return self.outcome_observations.ingest(
            envelope.request,
            previous=envelope.prior_observation,
        )

    def semantic_operation(self, operation: str, request):
        method = getattr(self.semantic_operations, operation, None)
        if method is None or operation.startswith("_"):
            raise ValueError("semantic operation is not published")
        return method(request)

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self.runtime._search_impl(query, **kwargs)

    def find(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self.runtime._find_impl(query, **kwargs)

    def capture(self, **kwargs: Any) -> dict[str, Any]:
        return self.runtime._capture_impl(**kwargs)

    def topic_list(self, **kwargs: Any) -> dict[str, Any]:
        return self.runtime._topic_list_impl(**kwargs)

    def inbox_list(self, **kwargs: Any) -> dict[str, Any]:
        return self.runtime._inbox_list_impl(**kwargs)

    def topic_upsert(self, topic: str, **kwargs: Any) -> dict[str, Any]:
        return self.runtime._topic_upsert_impl(topic, **kwargs)

    def inbox_promote(self, capture_path: str, **kwargs: Any) -> dict[str, Any]:
        return self.runtime._inbox_promote_impl(capture_path, **kwargs)


class LocalKnowledgeProvider:
    """Repo-backed, bounded implementation of :mod:`krail.provider.v1`."""

    def __init__(self, application: KnowledgeApplicationService) -> None:
        self.application = application
        self.project_path = application.runtime.project_path
        self.authority = "git+" + self.project_path.as_uri()

    def provider_info(self, request: ProviderInfoRequest) -> ProviderInfoResult:
        import rail

        installed = _distribution_version()
        runtime_version = rail.__version__
        runtime_major = runtime_version.split(".", 1)[0]
        installed_compatible = installed.split(".", 1)[0] == runtime_major
        compatible = installed_compatible
        diagnostic = (
            f"runtime and installed distribution agree on major version {runtime_major}"
            if installed_compatible
            else f"installation skew: runtime {runtime_version}, installed distribution {installed}"
        )
        if request.consumer_version:
            consumer_major = request.consumer_version.split(".", 1)[0]
            consumer_compatible = runtime_major == consumer_major
            compatible = installed_compatible and consumer_compatible
            if not consumer_compatible:
                diagnostic = f"version skew: provider {runtime_version}, consumer {request.consumer_version}"
        return ProviderInfoResult(
            provider="krail.local",
            provider_version=runtime_version,
            installed_distribution_version=installed,
            capabilities=CAPABILITIES,
            compatible=compatible,
            diagnostic=diagnostic,
        )

    def capability_descriptor(self, capability_id: str = "krail.context-brief"):
        """Publish an immutable KRAIL capability without granting access."""
        return self.application.capability_publication.descriptor(capability_id)

    def negotiate_capability(self, request: CapabilityNegotiationRequest):
        return self.application.capability_publication.negotiate(request)

    def context_brief(self, request):
        """Delegate to the accepted K2.1 service; do not duplicate assembly."""
        return self.application.context_brief(request)

    def assemble_verification_evidence(self, request):
        return self.application.assemble_verification_evidence(request)

    def ingest_outcome_evidence(self, envelope):
        return self.application.ingest_outcome_evidence(envelope)

    def semantic_operation(self, operation: str, request):
        return self.application.semantic_operation(operation, request)

    def describe_types(self, request: DescribeTypesRequest) -> DescribeTypesResult:
        del request
        return DescribeTypesResult(
            types=[
                ResourceTypeDescriptor(
                    resource_type="topic",
                    title="Durable topic",
                    media_types=["text/markdown"],
                ),
                ResourceTypeDescriptor(
                    resource_type="capture",
                    title="Inbox capture",
                    media_types=["text/markdown"],
                ),
                ResourceTypeDescriptor(
                    resource_type="source",
                    title="Source record",
                    media_types=["text/markdown", "application/json", "text/yaml"],
                ),
                ResourceTypeDescriptor(
                    resource_type="artifact",
                    title="Project artifact",
                    media_types=["text/markdown", "application/json", "text/plain"],
                ),
                ResourceTypeDescriptor(
                    resource_type="document",
                    title="Project document",
                    media_types=[
                        "text/markdown",
                        "application/json",
                        "text/plain",
                        "text/yaml",
                    ],
                ),
            ]
        )

    @staticmethod
    def _resource_type(relative: str) -> str:
        if relative.startswith("topics/inbox/"):
            return "capture"
        if relative.startswith("topics/"):
            return "topic"
        if relative.startswith("sources/"):
            return "source"
        if relative.startswith("artifacts/"):
            return "artifact"
        return "document"

    def _path(self, resource_id: str) -> Path:
        path = (self.project_path / resource_id).resolve()
        try:
            path.relative_to(self.project_path)
        except ValueError as exc:
            raise ValueError(
                "resource_id must stay inside the project authority"
            ) from exc
        if not path.is_file():
            raise FileNotFoundError(f"resource not found: {resource_id}")
        return path

    def _ref(self, relative: str) -> ResourceRef:
        path = self._path(relative)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return ResourceRef(
            authority=self.authority,
            resource_type=self._resource_type(relative),
            resource_id=relative,
            version=f"content:{digest}",
            digest=f"sha256:{digest}",
        )

    @staticmethod
    def _encode_cursor(query: str, resource_types: list[str], offset: int) -> str:
        shape = json.dumps(
            {"query": query, "resource_types": sorted(set(resource_types))},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = json.dumps(
            {"s": hashlib.sha256(shape.encode()).hexdigest(), "o": offset},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(
        query: str, resource_types: list[str], cursor: str | None
    ) -> int:
        if not cursor:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            shape = json.dumps(
                {"query": query, "resource_types": sorted(set(resource_types))},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if payload.get("s") != hashlib.sha256(shape.encode()).hexdigest():
                raise ValueError
            offset = int(payload["o"])
        except Exception as exc:
            raise ValueError("cursor is invalid for this search") from exc
        if offset < 0 or offset >= 100:
            raise ValueError("cursor exceeds the bounded provider-v1 result window")
        return offset

    def _hit(self, item: dict[str, Any]) -> SearchHit | None:
        relative = str(item.get("path") or "")
        if not relative:
            return None
        try:
            ref = self._ref(relative)
        except (FileNotFoundError, UnicodeError, ValueError):
            return None
        preview, _ = _utf8_prefix(
            str(item.get("snippet") or item.get("context") or ""), 4096
        )
        return SearchHit(
            ref=ref,
            title=str(item.get("title") or Path(relative).stem),
            preview=preview,
            score=max(0.0, float(item.get("score") or 0.0)),
        )

    def search(self, request: SearchRequest) -> SearchResult:
        offset = self._decode_cursor(
            request.query, request.resource_types, request.cursor
        )
        # Materialize at most the contract-wide window before type filtering so
        # opaque cursor offsets remain stable across pages.
        legacy = self.application.search(
            request.query, limit=100, explain=False, rag=False
        )
        hits = [
            hit
            for item in legacy.get("hits", [])
            if (hit := self._hit(item)) is not None
        ]
        if request.resource_types:
            allowed = set(request.resource_types)
            hits = [hit for hit in hits if hit.ref.resource_type in allowed]
        page = hits[offset : offset + request.limit]
        more = len(hits) > offset + request.limit and offset + request.limit < 100
        return SearchResult(
            hits=page,
            next_cursor=self._encode_cursor(
                request.query, request.resource_types, offset + request.limit
            )
            if more
            else None,
            truncated=more,
        )

    def find(self, request: FindRequest) -> FindResult:
        hits: list[SearchHit] = []
        missing: list[str] = []
        for identifier in request.identifiers:
            try:
                ref = self._ref(identifier)
                if ref.resource_type != request.resource_type:
                    raise FileNotFoundError(identifier)
                hits.append(SearchHit(ref=ref, title=Path(identifier).stem))
            except (FileNotFoundError, ValueError):
                missing.append(identifier)
        return FindResult(hits=hits, missing_identifiers=missing)

    def get_resource(self, request: GetResourceRequest) -> GetResourceResult:
        if request.ref.authority != self.authority:
            raise ValueError("resource authority is not served by this provider")
        path = self._path(request.ref.resource_id)
        raw = path.read_bytes()
        exact = self._ref(request.ref.resource_id)
        if exact.exact_key != request.ref.exact_key:
            raise ValueError(
                "resource version or digest no longer matches the exact requested source"
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "provider-v1 local resource reads support UTF-8 text only"
            ) from exc
        content, truncated = _utf8_prefix(content, request.max_bytes)
        media_type = (
            "text/markdown"
            if path.suffix.lower() == ".md"
            else "application/json"
            if path.suffix.lower() == ".json"
            else "text/plain"
        )
        return GetResourceResult(
            resource=ResourcePayload(
                ref=exact, media_type=media_type, content=content, truncated=truncated
            )
        )

    def retrieve_evidence(
        self, request: RetrieveEvidenceRequest
    ) -> RetrieveEvidenceResult:
        result = self.search(
            SearchRequest(
                query=request.query,
                resource_types=request.resource_types,
                limit=request.max_items,
            )
        )
        remaining = request.max_total_bytes
        items: list[EvidenceItem] = []
        truncated = result.truncated
        for hit in result.hits:
            if remaining <= 0:
                truncated = True
                break
            resource = self.get_resource(
                GetResourceRequest(
                    ref=hit.ref, max_bytes=min(MAX_EVIDENCE_ITEM_BYTES, remaining)
                )
            ).resource
            excerpt = resource.content.strip()
            if not excerpt:
                continue
            excerpt, clipped = _utf8_prefix(
                excerpt, min(MAX_EVIDENCE_ITEM_BYTES, remaining)
            )
            items.append(
                EvidenceItem(
                    source=hit.ref,
                    locator=f"{hit.ref.resource_id}#utf8:0-{len(excerpt.encode('utf-8'))}",
                    excerpt=excerpt,
                    media_type=resource.media_type,
                    relevance=min(1.0, hit.score or 0.0),
                )
            )
            remaining -= len(excerpt.encode("utf-8"))
            truncated = truncated or clipped or resource.truncated
        if not items:
            raise LookupError("no exact, readable evidence matched the request")
        fingerprint = "\n".join(item.source.digest for item in items)
        packet_id = (
            "packet:"
            + hashlib.sha256(f"{request.query}\n{fingerprint}".encode()).hexdigest()[
                :24
            ]
        )
        return RetrieveEvidenceResult(
            evidence=EvidencePacket(
                packet_id=packet_id,
                query=request.query,
                generated_at=datetime.now(UTC),
                items=items,
                truncated=truncated,
            )
        )

    def explain(self, request: ExplainRequest) -> ExplainResult:
        if request.refs:
            items: list[EvidenceItem] = []
            for ref in request.refs[: request.max_evidence_items]:
                resource = self.get_resource(
                    GetResourceRequest(ref=ref, max_bytes=MAX_EVIDENCE_ITEM_BYTES)
                ).resource
                excerpt = resource.content.strip()
                if excerpt:
                    items.append(
                        EvidenceItem(
                            source=ref,
                            locator=f"{ref.resource_id}#utf8:0-{len(excerpt.encode('utf-8'))}",
                            excerpt=excerpt,
                            media_type=resource.media_type,
                        )
                    )
            if not items:
                raise LookupError(
                    "no exact, readable evidence was supplied for explanation"
                )
            packet = EvidencePacket(
                packet_id="packet:"
                + hashlib.sha256(request.question.encode()).hexdigest()[:24],
                query=request.question,
                generated_at=datetime.now(UTC),
                items=items,
            )
        else:
            packet = self.retrieve_evidence(
                RetrieveEvidenceRequest(
                    query=request.question, max_items=request.max_evidence_items
                )
            ).evidence
        sources = ", ".join(item.source.resource_id for item in packet.items)
        return ExplainResult(
            explanation=f"Bounded evidence for {request.question!r} was retrieved from: {sources}.",
            evidence=packet,
        )

    def lineage(self, request: LineageRequest) -> LineageResult:
        exact = self.get_resource(
            GetResourceRequest(ref=request.ref, max_bytes=1)
        ).resource.ref
        nodes: list[ResourceRef] = [exact]
        edges: list[LineageEdge] = []
        seen = {exact.exact_key}
        queue: list[tuple[ResourceRef, int]] = [(exact, 0)]
        truncated = False
        while queue:
            current, depth = queue.pop(0)
            neighbors = self._lineage_neighbors(current)
            if depth >= request.max_depth:
                truncated = truncated or bool(neighbors)
                continue
            for source, target, relation, neighbor in neighbors:
                if neighbor.exact_key not in seen:
                    if len(nodes) >= request.max_nodes:
                        truncated = True
                        continue
                    seen.add(neighbor.exact_key)
                    nodes.append(neighbor)
                    queue.append((neighbor, depth + 1))
                edge = LineageEdge(source=source, target=target, relation=relation)
                if edge not in edges:
                    if len(edges) >= MAX_LINEAGE_EDGES:
                        truncated = True
                    else:
                        edges.append(edge)
        return LineageResult(root=exact, nodes=nodes, edges=edges, truncated=truncated)

    def _lineage_neighbors(
        self, ref: ResourceRef
    ) -> list[tuple[ResourceRef, ResourceRef, str, ResourceRef]]:
        path = self._path(ref.resource_id)
        if path.suffix.lower() != ".md":
            return []
        metadata, _body = self.application.runtime._split_markdown_frontmatter(
            path.read_text(encoding="utf-8")
        )
        neighbors: list[tuple[ResourceRef, ResourceRef, str, ResourceRef]] = []
        source_paths = self.application.runtime._ensure_list_of_strings(
            metadata.get("source_captures")
        )
        source_paths += self.application.runtime._ensure_list_of_strings(
            metadata.get("source_path")
        )
        for relative in dict.fromkeys(source_paths):
            try:
                source = self._ref(relative)
            except (FileNotFoundError, ValueError):
                continue
            neighbors.append((source, ref, "promoted-into", source))
        promoted_to = metadata.get("promoted_to")
        if isinstance(promoted_to, str) and promoted_to.strip():
            try:
                target = self._ref(promoted_to.strip())
            except (FileNotFoundError, ValueError):
                pass
            else:
                neighbors.append((ref, target, "promoted-into", target))
        return neighbors

    def integrity(self, request: IntegrityRequest) -> IntegrityResult:
        findings: list[IntegrityFinding] = []
        truncated = False
        for index, ref in enumerate(request.refs):
            try:
                self.get_resource(GetResourceRequest(ref=ref, max_bytes=1))
            except FileNotFoundError:
                findings.append(
                    IntegrityFinding(
                        code="resource-missing",
                        severity="error",
                        message="The exact resource is unavailable.",
                        ref=ref,
                    )
                )
            except ValueError as exc:
                findings.append(
                    IntegrityFinding(
                        code="exact-reference-mismatch",
                        severity="error",
                        message=str(exc),
                        ref=ref,
                    )
                )
            if len(findings) >= request.max_findings:
                truncated = index < len(request.refs) - 1
                break
        return IntegrityResult(
            status="fail" if findings else "pass",
            findings=findings,
            truncated=truncated,
        )


__all__ = [
    "CAPABILITIES",
    "KnowledgeApplicationService",
    "LocalKnowledgeProvider",
    "CONTRACT_ID",
]
