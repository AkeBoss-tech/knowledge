from __future__ import annotations

from dataclasses import dataclass


DOCS_SCHEMA_VERSION = "krail.docs/v1"


@dataclass(frozen=True)
class BuiltinDoc:
    path: str
    title: str
    content: str

    def as_dict(self, *, include_content: bool = False) -> dict[str, str]:
        result = {"path": self.path, "title": self.title}
        if include_content:
            result["content"] = self.content
        return result


BUILTIN_DOCS = (
    BuiltinDoc(
        path="start",
        title="Agent quick start",
        content=(
            "Inspect a project with `krail --local mode active`, `krail --local pack active`, "
            "and `krail --local doctor`. Use `search` for raw evidence and `think` for a cited "
            "answer shape. Capture raw material into the inbox, then promote trusted material "
            "into durable topic pages."
        ),
    ),
    BuiltinDoc(
        path="knowledge-operations",
        title="Knowledge operations vocabulary",
        content=(
            "An action is a typed operation with input, output, capability, credential, retry, "
            "and idempotency metadata. A retriever is a read-only evidence producer. A trigger "
            "observes an event and starts a workflow; listener remains a compatibility alias. "
            "A run is a unified inspection view over workflow and agent execution records."
        ),
    ),
    BuiltinDoc(
        path="retrieval-v2",
        title="Retrieval v2",
        content=(
            "Retrieval v2 plans a query, runs eligible lexical, vector, graph, exact-code, "
            "recency, and ownership retrievers, fuses rankings deterministically with "
            "reciprocal-rank fusion, expands neighboring context, and returns an evidence packet with "
            "citations, trust, freshness, gaps, suggested actions, and a retrieval trace."
        ),
    ),
    BuiltinDoc(
        path="migration-1.1",
        title="Migrating to KRAIL 1.1",
        content=(
            "KRAIL 1.1 is additive. Existing search and listener commands remain supported. "
            "Use trigger as the preferred event vocabulary, inspect actions before execution, "
            "and treat search scores as deterministic ranker outputs rather than comparable "
            "absolute relevance probabilities."
        ),
    ),
)


def search_builtin_docs(query: str, *, limit: int = 10) -> dict[str, object]:
    terms = [term.lower() for term in query.split() if term.strip()]
    ranked: list[tuple[int, BuiltinDoc]] = []
    for doc in BUILTIN_DOCS:
        haystack = f"{doc.path} {doc.title} {doc.content}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            ranked.append((score, doc))
    ranked.sort(key=lambda item: (-item[0], item[1].path))
    return {
        "version": DOCS_SCHEMA_VERSION,
        "query": query,
        "results": [dict(doc.as_dict(), score=score) for score, doc in ranked[: max(0, limit)]],
    }


def query_builtin_doc(path: str) -> dict[str, object]:
    normalized = path.strip().strip("/").removesuffix(".md")
    for doc in BUILTIN_DOCS:
        if doc.path == normalized:
            return {"version": DOCS_SCHEMA_VERSION, "document": doc.as_dict(include_content=True)}
    raise KeyError(f"unknown built-in document: {path}")
