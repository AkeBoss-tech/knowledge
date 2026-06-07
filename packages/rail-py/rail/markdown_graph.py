from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rail.source_dependencies import iter_document_dependencies, load_dependency_manifest


DEFAULT_INCLUDE = ["topics/**/*.md", "research_plan/**/*.md"]
DEFAULT_EXCLUDE = [
    ".git/**",
    ".krail/**",
    ".rail/**",
    ".ontology/**",
    ".venv/**",
    "node_modules/**",
    "research_plan/graph/*.md",
    "docs/data/*.md",
]
DEFAULT_JSON_PATH = "research_plan/graph/graph.json"
DEFAULT_MERMAID_PATH = "research_plan/graph/graph.mmd"
DEFAULT_SUMMARY_PATH = "research_plan/graph/summary.md"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class MarkdownGraphConfig:
    include: list[str]
    exclude: list[str]
    json_path: str
    mermaid_path: str
    summary_path: str
    docs_json_path: str | None = None
    docs_mermaid_path: str | None = None


def slugify(value: Any, *, fallback: str = "item") -> str:
    slug = _SLUG_RE.sub("-", str(value).lower()).strip("-")
    return slug or fallback


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_str_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _frontmatter(text: str) -> dict[str, Any] | None:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    loaded = yaml.safe_load(match.group(1)) or {}
    if not isinstance(loaded, dict):
        return None
    return loaded


def _matches_any(rel_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Treat "dir/**/*.md" as matching both "dir/file.md" and nested files.
        collapsed = pattern.replace("/**/", "/")
        if collapsed != pattern and fnmatch.fnmatch(rel_path, collapsed):
            return True
    return False


def _manifest_graph_config(project_path: Path) -> dict[str, Any]:
    manifest_path = project_path / "rail.yaml"
    if not manifest_path.exists():
        return {}
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    graph = manifest.get("graph") if isinstance(manifest, dict) else None
    return graph if isinstance(graph, dict) else {}


def load_config(project_path: str | Path) -> MarkdownGraphConfig:
    root = Path(project_path).resolve()
    graph = _manifest_graph_config(root)
    export = graph.get("export") if isinstance(graph.get("export"), dict) else {}
    docs_export = graph.get("docs_export") if isinstance(graph.get("docs_export"), dict) else {}
    return MarkdownGraphConfig(
        include=_as_str_list(graph.get("include")) or list(DEFAULT_INCLUDE),
        exclude=[*DEFAULT_EXCLUDE, *_as_str_list(graph.get("exclude"))],
        json_path=str(export.get("json") or graph.get("json_path") or DEFAULT_JSON_PATH),
        mermaid_path=str(export.get("mermaid") or graph.get("mermaid_path") or DEFAULT_MERMAID_PATH),
        summary_path=str(export.get("summary") or graph.get("summary_path") or DEFAULT_SUMMARY_PATH),
        docs_json_path=docs_export.get("json") or export.get("docs_json"),
        docs_mermaid_path=docs_export.get("mermaid") or export.get("docs_mermaid"),
    )


def iter_markdown_files(project_path: str | Path, config: MarkdownGraphConfig | None = None) -> list[Path]:
    root = Path(project_path).resolve()
    cfg = config or load_config(root)
    files: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if _matches_any(rel, cfg.exclude):
            continue
        if _matches_any(rel, cfg.include):
            files.append(path)
    return files


def _entity_id(name: str) -> str:
    return f"entity:{slugify(name)}"


def _source_id(source: str) -> str:
    return f"source:{slugify(source)}"


def _topic_id(topic: str) -> str:
    return f"topic:{slugify(topic)}"


def _add_node(nodes: dict[str, dict[str, Any]], node_id: str, payload: dict[str, Any]) -> None:
    existing = nodes.get(node_id, {})
    merged = {**existing, **payload, "id": node_id}
    nodes[node_id] = {key: value for key, value in merged.items() if value is not None}


def _add_edge(edges: dict[str, dict[str, Any]], from_id: str, rel_type: str, to_id: str, payload: dict[str, Any]) -> None:
    edge_id = f"{from_id}::{rel_type}::{to_id}::{payload.get('source', '')}"
    if edge_id not in edges:
        edges[edge_id] = {
            "id": edge_id,
            "from": from_id,
            "type": rel_type,
            "to": to_id,
            **{key: value for key, value in payload.items() if value is not None},
        }


def _document_title(path: Path, metadata: dict[str, Any]) -> str:
    title = metadata.get("title")
    if title:
        return str(title)
    return path.stem.replace("-", " ").replace("_", " ").strip() or path.name


def _entity_metadata(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for name in _as_str_list(metadata.get("entities")):
        entities[name] = {"name": name, "entity_type": "Concept"}
    for item in _as_list(metadata.get("entity_metadata")):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"]).strip()
        if not name:
            continue
        entities[name] = {
            "name": name,
            "entity_type": item.get("entity_type") or item.get("type") or "Concept",
            "status": item.get("status"),
        }
    return entities


def build_markdown_graph(project_path: str | Path, *, write: bool = True) -> dict[str, Any]:
    root = Path(project_path).resolve()
    config = load_config(root)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    documents: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []

    for path in iter_markdown_files(root, config):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            warnings.append({"path": rel, "warning": f"could not read markdown: {exc}"})
            continue
        metadata = _frontmatter(text)
        if metadata is None:
            continue

        title = _document_title(path, metadata)
        kind = str(metadata.get("kind") or metadata.get("type") or "document")
        doc_id = f"doc:{rel}"
        topics = _as_str_list(metadata.get("topics"))
        sources = _as_str_list(metadata.get("sources"))
        for key in ["url", "source_url"]:
            if metadata.get(key):
                sources.append(str(metadata[key]))
        sources = sorted(set(sources))
        entity_meta = _entity_metadata(metadata)

        documents.append(
            {
                "id": doc_id,
                "path": rel,
                "title": title,
                "kind": kind,
                "topics": topics,
                "entities": sorted(entity_meta),
                "sources": sources,
                "relations": len(_as_list(metadata.get("relations"))),
                "updated": metadata.get("updated") or metadata.get("captured_at"),
            }
        )
        _add_node(
            nodes,
            doc_id,
            {
                "label": title,
                "nodeType": "document",
                "kind": kind,
                "path": rel,
                "updated": metadata.get("updated") or metadata.get("captured_at"),
            },
        )

        for topic in topics:
            topic_id = _topic_id(topic)
            _add_node(nodes, topic_id, {"label": topic, "nodeType": "topic"})
            _add_edge(edges, doc_id, "has_topic", topic_id, {"source": rel})

        for entity in entity_meta.values():
            entity_id = _entity_id(entity["name"])
            _add_node(
                nodes,
                entity_id,
                {
                    "label": entity["name"],
                    "nodeType": "entity",
                    "entityType": entity.get("entity_type") or "Concept",
                    "status": entity.get("status"),
                },
            )
            _add_edge(edges, doc_id, "mentions", entity_id, {"source": rel})

        for source in sources:
            source_id = _source_id(source)
            _add_node(nodes, source_id, {"label": source, "nodeType": "source", "url": source})
            _add_edge(edges, doc_id, "cites", source_id, {"source": rel})

        for relation in _as_list(metadata.get("relations")):
            if not isinstance(relation, dict):
                warnings.append({"path": rel, "warning": "relation entry is not an object"})
                continue
            if not relation.get("from") or not relation.get("type") or not relation.get("to"):
                warnings.append({"path": rel, "warning": "relation is missing from/type/to"})
                continue
            from_label = str(relation["from"]).strip()
            to_label = str(relation["to"]).strip()
            rel_type = str(relation["type"]).strip()
            from_id = _entity_id(from_label)
            to_id = _entity_id(to_label)
            _add_node(nodes, from_id, {"label": from_label, "nodeType": "entity"})
            _add_node(nodes, to_id, {"label": to_label, "nodeType": "entity"})
            _add_edge(edges, from_id, rel_type, to_id, {"source": rel, "document": doc_id})

    try:
        dependency_manifest = load_dependency_manifest(root)
        for row in iter_document_dependencies(dependency_manifest):
            doc_path = row["document"]
            source = row["source"]
            source_key = str(source["id"])
            doc_id = f"doc:{doc_path}"
            source_id = _source_id(source_key)
            _add_node(
                nodes,
                doc_id,
                {
                    "label": Path(doc_path).stem.replace("-", " ").replace("_", " "),
                    "nodeType": "document",
                    "path": doc_path,
                },
            )
            _add_node(
                nodes,
                source_id,
                {
                    "label": source_key,
                    "nodeType": "source",
                    "sourceType": source.get("type"),
                    "url": source.get("url"),
                    "path": source.get("path"),
                    "refresh": source.get("refresh"),
                },
            )
            _add_edge(
                edges,
                doc_id,
                "depends_on",
                source_id,
                {
                    "source": dependency_manifest.get("path") or "sources/dependencies.yaml",
                    "document": doc_id,
                    "role": source.get("role"),
                    "refresh": source.get("refresh"),
                },
            )
    except Exception as exc:
        warnings.append({"path": "sources/dependencies.yaml", "warning": f"could not load source dependencies: {exc}"})

    graph = {
        "mode": "markdown_frontmatter",
        "generatedAt": _dt.datetime.now(_dt.UTC).isoformat(),
        "config": {
            "include": config.include,
            "exclude": config.exclude,
            "jsonPath": config.json_path,
            "mermaidPath": config.mermaid_path,
            "summaryPath": config.summary_path,
        },
        "counts": {
            "documents": len(documents),
            "nodes": len(nodes),
            "edges": len(edges),
        },
        "nodeTypeCounts": _counts(nodes.values(), "nodeType"),
        "entityTypeCounts": _counts((node for node in nodes.values() if node.get("nodeType") == "entity"), "entityType"),
        "documents": sorted(documents, key=lambda item: item["path"]),
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": sorted(edges.values(), key=lambda item: item["id"]),
        "warnings": warnings,
    }

    if write:
        written = write_graph(root, graph, config)
        graph["written"] = written
    return graph


def _counts(items: Any, key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "Unspecified")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def mermaid_for_graph(graph: dict[str, Any]) -> str:
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    lines = ["graph TD"]
    for edge in graph.get("edges", []):
        from_node = nodes.get(edge["from"])
        to_node = nodes.get(edge["to"])
        if not from_node or not to_node:
            continue
        from_id = re.sub(r"[^A-Za-z0-9_]", "_", edge["from"])
        to_id = re.sub(r"[^A-Za-z0-9_]", "_", edge["to"])
        from_label = str(from_node.get("label") or edge["from"]).replace('"', '\\"')
        to_label = str(to_node.get("label") or edge["to"]).replace('"', '\\"')
        rel_type = str(edge.get("type") or "related_to").replace('"', '\\"')
        lines.append(f'  {from_id}["{from_label}"] -->|{rel_type}| {to_id}["{to_label}"]')
    return "\n".join(lines) + "\n"


def summary_for_graph(graph: dict[str, Any]) -> str:
    docs = sorted(
        graph.get("documents", []),
        key=lambda item: (-len(item.get("entities") or []), -int(item.get("relations") or 0), item.get("path") or ""),
    )
    lines = [
        "# Markdown Graph Summary",
        "",
        f"Generated: {graph.get('generatedAt')}",
        "",
        f"- Documents: {graph.get('counts', {}).get('documents', 0)}",
        f"- Nodes: {graph.get('counts', {}).get('nodes', 0)}",
        f"- Edges: {graph.get('counts', {}).get('edges', 0)}",
        "",
        "## Node Types",
        "",
        *[f"- {key}: {value}" for key, value in graph.get("nodeTypeCounts", {}).items()],
        "",
        "## Entity Types",
        "",
        *[f"- {key}: {value}" for key, value in graph.get("entityTypeCounts", {}).items()],
        "",
        "## Documents With Metadata",
        "",
    ]
    for doc in docs:
        lines.append(
            "- "
            f"{doc.get('title')} ({doc.get('kind')}) - "
            f"{len(doc.get('entities') or [])} entities, "
            f"{doc.get('relations') or 0} relations, "
            f"{len(doc.get('sources') or [])} sources - "
            f"{doc.get('path')}"
        )
    if graph.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in graph["warnings"]:
            lines.append(f"- {warning.get('path')}: {warning.get('warning')}")
    return "\n".join(lines).rstrip() + "\n"


def write_graph(project_path: Path, graph: dict[str, Any], config: MarkdownGraphConfig) -> list[str]:
    outputs = {
        config.json_path: json.dumps(graph, indent=2, default=str) + "\n",
        config.mermaid_path: mermaid_for_graph(graph),
        config.summary_path: summary_for_graph(graph),
    }
    if config.docs_json_path:
        outputs[config.docs_json_path] = outputs[config.json_path]
    if config.docs_mermaid_path:
        outputs[config.docs_mermaid_path] = outputs[config.mermaid_path]

    written: list[str] = []
    for rel, content in outputs.items():
        path = project_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


def _semantic_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        key: graph.get(key)
        for key in ["mode", "config", "counts", "nodeTypeCounts", "entityTypeCounts", "documents", "nodes", "edges", "warnings"]
    }


def validate_markdown_graph(project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).resolve()
    graph = build_markdown_graph(root, write=False)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = list(graph.get("warnings") or [])
    slug_labels: dict[str, set[str]] = {}

    for doc in graph.get("documents", []):
        if not doc.get("title"):
            errors.append({"path": doc.get("path", ""), "error": "frontmatter title is missing"})
        if not doc.get("kind"):
            errors.append({"path": doc.get("path", ""), "error": "frontmatter kind is missing"})

    node_ids = {node.get("id") for node in graph.get("nodes", [])}
    for node in graph.get("nodes", []):
        if node.get("nodeType") == "entity":
            slug_labels.setdefault(node.get("id", ""), set()).add(str(node.get("label") or ""))
    for node_id, labels in slug_labels.items():
        if len(labels) > 1:
            warnings.append({"path": "", "warning": f"entity slug collision for {node_id}: {sorted(labels)}"})

    for edge in graph.get("edges", []):
        if edge.get("from") not in node_ids:
            errors.append({"path": edge.get("source", ""), "error": f"edge has missing from node: {edge.get('from')}"})
        if edge.get("to") not in node_ids:
            errors.append({"path": edge.get("source", ""), "error": f"edge has missing to node: {edge.get('to')}"})
        if not edge.get("type"):
            errors.append({"path": edge.get("source", ""), "error": "edge relation type is missing"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": graph.get("counts", {}),
    }


def check_markdown_graph(project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).resolve()
    config = load_config(root)
    graph_path = root / config.json_path
    validation = validate_markdown_graph(root)
    if not graph_path.exists():
        return {
            "ok": False,
            "status": "missing",
            "path": config.json_path,
            "validation": validation,
            "message": "graph artifact is missing; run `krail graph build`.",
        }
    stored = json.loads(graph_path.read_text(encoding="utf-8"))
    rebuilt = build_markdown_graph(root, write=False)
    stale = _semantic_graph(stored) != _semantic_graph(rebuilt)
    return {
        "ok": validation["ok"] and not stale,
        "status": "stale" if stale else "fresh",
        "path": config.json_path,
        "validation": validation,
        "message": "graph artifact is stale; run `krail graph build`." if stale else "graph artifact is fresh.",
    }


def load_or_build_graph(project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).resolve()
    config = load_config(root)
    graph_path = root / config.json_path
    if graph_path.exists():
        return json.loads(graph_path.read_text(encoding="utf-8"))
    return build_markdown_graph(root, write=False)


def filter_entities(graph: dict[str, Any], *, entity_type: str | None = None, limit: int = 100) -> dict[str, Any]:
    entities = [node for node in graph.get("nodes", []) if node.get("nodeType") == "entity"]
    if entity_type:
        entities = [node for node in entities if str(node.get("entityType") or "").lower() == entity_type.lower()]
    entities = sorted(entities, key=lambda item: (item.get("entityType") or "", item.get("label") or ""))
    return {"entities": entities[:limit], "count": len(entities), "graphGeneratedAt": graph.get("generatedAt")}


def filter_edges(
    graph: dict[str, Any],
    *,
    entity: str | None = None,
    relation_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    edges = list(graph.get("edges", []))
    if entity:
        wanted = _entity_id(entity)
        entity_lower = entity.lower()
        node_labels = {
            node["id"]: str(node.get("label") or "").lower()
            for node in graph.get("nodes", [])
            if node.get("nodeType") == "entity"
        }
        edges = [
            edge
            for edge in edges
            if edge.get("from") == wanted
            or edge.get("to") == wanted
            or node_labels.get(edge.get("from"), "") == entity_lower
            or node_labels.get(edge.get("to"), "") == entity_lower
        ]
    if relation_type:
        edges = [edge for edge in edges if str(edge.get("type") or "").lower() == relation_type.lower()]
    return {"edges": edges[:limit], "count": len(edges), "graphGeneratedAt": graph.get("generatedAt")}


def filter_documents(
    graph: dict[str, Any],
    *,
    topic: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    entity: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    docs = list(graph.get("documents", []))
    if topic:
        docs = [doc for doc in docs if topic.lower() in {str(item).lower() for item in doc.get("topics", [])}]
    if kind:
        docs = [doc for doc in docs if str(doc.get("kind") or "").lower() == kind.lower()]
    if source:
        docs = [doc for doc in docs if any(source.lower() in str(item).lower() for item in doc.get("sources", []))]
    if entity:
        docs = [doc for doc in docs if entity.lower() in {str(item).lower() for item in doc.get("entities", [])}]
    return {"documents": docs[:limit], "count": len(docs), "graphGeneratedAt": graph.get("generatedAt")}


def export_graph(graph: dict[str, Any], export_format: str) -> str:
    if export_format == "json":
        return json.dumps(graph, indent=2, default=str) + "\n"
    if export_format == "mermaid":
        return mermaid_for_graph(graph)
    if export_format == "summary":
        return summary_for_graph(graph)
    raise ValueError(f"unsupported graph export format: {export_format}")
