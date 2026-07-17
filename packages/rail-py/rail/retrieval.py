from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


RETRIEVER_SCHEMA_VERSION = "krail.retriever/v1"
EVIDENCE_PACKET_SCHEMA_VERSION = "krail.evidence-packet/v1"
QUERY_PLAN_SCHEMA_VERSION = "krail.query-plan/v1"
RANKER_VERSION = "deterministic_rrf_v2"

_TRUST_STATES = frozenset({
    "candidate", "draft", "needs_evidence", "reviewed", "partially_verified",
    "verified", "supported", "stale", "conflicted", "blocked", "unknown",
})


@dataclass(frozen=True)
class RetrieverDefinition:
    id: str
    description: str
    source_types: tuple[str, ...] = ()
    deterministic: bool = True
    permission_filtered: bool = True
    version: str = RETRIEVER_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_types"] = list(self.source_types)
        return payload


@dataclass(frozen=True)
class QueryPlan:
    query: str
    intent: str
    retrievers: tuple[str, ...]
    reasons: dict[str, str]
    version: str = QUERY_PLAN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["retrievers"] = list(self.retrievers)
        return payload


@dataclass
class EvidencePacket:
    query: str
    plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    retrieval_trace: dict[str, Any]
    source_freshness: dict[str, Any] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    suggested_next_actions: list[str] = field(default_factory=list)
    version: str = EVIDENCE_PACKET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeterministicQueryPlanner:
    """Small explainable planner for selecting local read-only retrievers."""

    CODE_TERMS = frozenset({
        "class", "function", "method", "module", "package", "symbol", "import",
        "dependency", "api", "endpoint", "code", "implementation", "test",
    })
    RECENT_TERMS = frozenset({
        "recent", "latest", "today", "yesterday", "changed", "change", "new",
        "updated", "stale", "fresh", "last", "week", "month",
    })
    OWNERSHIP_TERMS = frozenset({
        "who", "owner", "owns", "maintainer", "expert", "team", "responsible",
    })

    def plan(self, query: str, *, vector_enabled: bool = True, graph_enabled: bool = True) -> QueryPlan:
        terms = {item.strip(".,:;!?()[]{}\"'").lower() for item in query.split() if item.strip()}
        retrievers: list[str] = ["lexical"]
        reasons = {"lexical": "exact terms and inverse-document-frequency signals"}
        intents: list[str] = []
        if vector_enabled:
            retrievers.append("vector")
            reasons["vector"] = "semantic similarity broadens recall"
        if graph_enabled:
            retrievers.append("graph")
            reasons["graph"] = "typed topic and entity relationships add structural evidence"
        if terms & self.CODE_TERMS:
            retrievers.append("exact_code")
            reasons["exact_code"] = "query contains code or symbol language"
            intents.append("code")
        if terms & self.RECENT_TERMS:
            retrievers.append("recency")
            reasons["recency"] = "query asks about changes or freshness"
            intents.append("recent")
        if terms & self.OWNERSHIP_TERMS:
            retrievers.append("ownership")
            reasons["ownership"] = "query asks about ownership or expertise"
            intents.append("ownership")
        return QueryPlan(
            query=query,
            intent="_".join(intents) if intents else "general",
            retrievers=tuple(dict.fromkeys(retrievers)),
            reasons=reasons,
        )


def _stable_record_id(hit: dict[str, Any]) -> str:
    return str(hit.get("record_id") or hit.get("path") or hit.get("id") or "")


def reciprocal_rank_fusion(
    ranked_results: dict[str, list[dict[str, Any]]],
    *,
    limit: int,
    rank_constant: int = 60,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Fuse independent rankings without treating their raw scores as comparable."""

    weights = dict(weights or {})
    merged: dict[str, dict[str, Any]] = {}
    for retriever, hits in ranked_results.items():
        weight = float(weights.get(retriever, 1.0))
        seen: set[str] = set()
        for rank, source in enumerate(hits, start=1):
            record_id = _stable_record_id(source)
            if not record_id or record_id in seen:
                continue
            seen.add(record_id)
            item = merged.setdefault(
                record_id,
                {
                    **source,
                    "record_id": record_id,
                    "retrievers": [],
                    "rank_signals": {},
                    "rrf_score": 0.0,
                },
            )
            # Prefer non-empty presentation fields from any retriever.
            for key in ("path", "title", "snippet", "matched_terms"):
                if not item.get(key) and source.get(key):
                    item[key] = source[key]
            item["retrievers"].append(retriever)
            item["rank_signals"][retriever] = {
                "rank": rank,
                "raw_score": source.get("score"),
                "weight": weight,
            }
            item["rrf_score"] += weight / (rank_constant + rank)

    ordered = sorted(
        merged.values(),
        key=lambda item: (-float(item["rrf_score"]), str(item.get("path") or item["record_id"])),
    )
    if ordered:
        ceiling = float(ordered[0]["rrf_score"]) or 1.0
        for item in ordered:
            item["rrf_score"] = round(float(item["rrf_score"]), 8)
            item["score"] = round(float(item["rrf_score"]) / ceiling, 6)
    return ordered[:limit]


def retrieval_trace(
    *,
    query: str,
    plan: QueryPlan,
    ranked_results: dict[str, list[dict[str, Any]]],
    fused: Iterable[dict[str, Any]],
    permission_filtered: dict[str, int] | None = None,
    rank_constant: int = 60,
) -> dict[str, Any]:
    fused_list = list(fused)
    digest_payload = {
        "query": query,
        "plan": plan.to_dict(),
        "ranked_paths": {
            key: [_stable_record_id(hit) for hit in hits]
            for key, hits in sorted(ranked_results.items())
        },
    }
    digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "ranker": RANKER_VERSION,
        "rank_constant": rank_constant,
        "plan": plan.to_dict(),
        "retrievers": {
            key: {
                "candidates": len(hits),
                "top_records": [_stable_record_id(hit) for hit in hits[:5]],
            }
            for key, hits in ranked_results.items()
        },
        "permission_filtered": dict(permission_filtered or {}),
        "fused_records": len(fused_list),
        "trace_digest": f"sha256:{digest}",
    }


def classify_record(path: str, metadata: dict[str, Any], *, stale: bool = False) -> dict[str, str]:
    """Return explicit source, trust, and freshness dimensions for a result."""

    normalized = path.replace("\\", "/")
    if normalized.startswith("topics/inbox/"):
        source_type = "capture"
        default_trust = "candidate"
    elif normalized.startswith("topics/"):
        source_type = "topic"
        default_trust = "reviewed"
    elif normalized.startswith("sources/"):
        source_type = "source"
        default_trust = "unknown"
    elif normalized.startswith("artifacts/"):
        source_type = "artifact"
        default_trust = "draft"
    elif normalized.startswith("research_plan/"):
        source_type = "operational"
        default_trust = "unknown"
    else:
        source_type = "document"
        default_trust = "unknown"

    declared = str(
        metadata.get("trust_state")
        or metadata.get("promotion_state")
        or metadata.get("claim_status")
        or ""
    ).strip().lower()
    trust_state = declared if declared in _TRUST_STATES else default_trust
    declared_freshness = str(metadata.get("freshness") or "").strip().lower()
    freshness = "stale" if stale else (declared_freshness or "unknown")
    return {
        "source_type": source_type,
        "trust_state": trust_state,
        "freshness": freshness,
    }


def expand_markdown_context(text: str, terms: list[str], *, max_chars: int = 1200) -> dict[str, Any]:
    """Expand a Markdown match to its containing heading section."""

    lines = text.splitlines()
    if not lines:
        return {"kind": "document", "heading": None, "text": ""}
    match_index = 0
    lowered_terms = [term.lower() for term in terms]
    for index, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in lowered_terms):
            match_index = index
            break
    heading_index: int | None = None
    heading_level = 7
    for index in range(match_index, -1, -1):
        match = re.match(r"^(#{1,6})\s+(.+)$", lines[index].strip())
        if match:
            heading_index = index
            heading_level = len(match.group(1))
            break
    start = heading_index if heading_index is not None else max(0, match_index - 3)
    end = len(lines)
    if heading_index is not None:
        for index in range(heading_index + 1, len(lines)):
            match = re.match(r"^(#{1,6})\s+", lines[index].strip())
            if match and len(match.group(1)) <= heading_level:
                end = index
                break
    else:
        end = min(len(lines), match_index + 8)
    content = "\n".join(lines[start:end]).strip()
    heading = lines[heading_index].lstrip("#").strip() if heading_index is not None else None
    return {
        "kind": "markdown_section" if heading is not None else "document_excerpt",
        "heading": heading,
        "start_line": start + 1,
        "end_line": min(end, len(lines)),
        "text": content[:max_chars],
        "truncated": len(content) > max_chars,
    }
