from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


DEFAULT_DEPENDENCY_PATHS = ["sources/dependencies.yaml", "research_plan/dependencies.yaml"]
DEFAULT_SNAPSHOT_PATH = "research_plan/state/source_snapshots.json"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def dependency_manifest_path(project_path: str | Path) -> Path | None:
    root = Path(project_path).resolve()
    for rel in DEFAULT_DEPENDENCY_PATHS:
        path = root / rel
        if path.exists():
            return path
    return None


def load_dependency_manifest(project_path: str | Path) -> dict[str, Any]:
    path = dependency_manifest_path(project_path)
    if not path:
        return {"documents": [], "path": None}
    data = _read_yaml(path)
    data["path"] = str(path.relative_to(Path(project_path).resolve()))
    return data


def iter_document_dependencies(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(manifest.get("documents")):
        if not isinstance(item, dict):
            continue
        doc_path = str(item.get("path") or "").strip()
        if not doc_path:
            continue
        for dep in _as_list(item.get("depends_on")):
            if not isinstance(dep, dict):
                continue
            source_id = str(dep.get("id") or "").strip()
            if not source_id:
                continue
            rows.append({"document": doc_path, "source": dep})
    return rows


def dependency_sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for row in iter_document_dependencies(manifest):
        source = dict(row["source"])
        source_id = str(source["id"])
        existing = sources.get(source_id, {})
        documents = sorted(set([*existing.get("documents", []), row["document"]]))
        merged = {**existing, **source, "id": source_id, "documents": documents}
        sources[source_id] = merged
    return sorted(sources.values(), key=lambda item: item["id"])


def validate_dependency_manifest(project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).resolve()
    manifest = load_dependency_manifest(root)
    errors: list[str] = []
    warnings: list[str] = []
    documents = _as_list(manifest.get("documents"))
    if manifest.get("path") is None:
        return {"ok": False, "path": None, "errors": ["dependency manifest missing"], "warnings": [], "documents": 0, "sources": 0}
    if not isinstance(manifest.get("documents"), list):
        errors.append("documents must be a list")
    seen_sources: set[str] = set()
    for index, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            errors.append(f"document entry {index} must be a mapping")
            continue
        doc_path = str(item.get("path") or "").strip()
        if not doc_path:
            errors.append(f"document entry {index} missing path")
            continue
        if not (root / doc_path).exists():
            warnings.append(f"document path does not exist: {doc_path}")
        deps = item.get("depends_on")
        if not isinstance(deps, list) or not deps:
            warnings.append(f"document has no dependencies: {doc_path}")
            continue
        for dep_index, dep in enumerate(deps, start=1):
            if not isinstance(dep, dict):
                errors.append(f"{doc_path} dependency {dep_index} must be a mapping")
                continue
            source_id = str(dep.get("id") or "").strip()
            if not source_id:
                errors.append(f"{doc_path} dependency {dep_index} missing id")
            else:
                seen_sources.add(source_id)
            if not dep.get("url") and not dep.get("path"):
                errors.append(f"{source_id or doc_path} dependency must define url or path")
            if not dep.get("type"):
                warnings.append(f"{source_id or doc_path} dependency missing type")
    return {
        "ok": not errors,
        "path": manifest.get("path"),
        "errors": errors,
        "warnings": warnings,
        "documents": len(documents),
        "sources": len(seen_sources),
    }


def snapshot_state_path(project_path: str | Path) -> Path:
    return Path(project_path).resolve() / DEFAULT_SNAPSHOT_PATH


def load_snapshot_state(project_path: str | Path) -> dict[str, Any]:
    path = snapshot_state_path(project_path)
    if not path.exists():
        return {"sources": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"sources": {}}
    data.setdefault("sources", {})
    return data


def write_snapshot_state(project_path: str | Path, state: dict[str, Any]) -> None:
    path = snapshot_state_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_url(url: str, *, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "krail-source-check/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def _github_repo_api_url(url: str) -> str | None:
    match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s#?]+)", url)
    if not match:
        return None
    owner = match.group(1)
    repo = match.group(2).removesuffix(".git")
    return f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD"


def snapshot_source(project_path: str | Path, source: dict[str, Any]) -> dict[str, Any]:
    root = Path(project_path).resolve()
    source_id = str(source.get("id") or "").strip()
    source_type = str(source.get("type") or "url").strip()
    url = str(source.get("url") or "").strip()
    local_path = str(source.get("path") or "").strip()
    checked_at = _dt.datetime.now(_dt.UTC).isoformat()
    payload = ""
    status = "ok"
    error = None

    try:
        if local_path:
            path = (root / local_path).resolve()
            payload = path.read_text(encoding="utf-8", errors="ignore")
        elif source_type == "github_repo" and url:
            api_url = _github_repo_api_url(url)
            payload = _fetch_url(api_url or url)
        elif source_type == "arxiv" and url:
            payload = _fetch_url(url)
        elif url:
            payload = _fetch_url(url)
        else:
            raise ValueError("source must define url or path")
    except (OSError, urllib.error.URLError, ValueError) as exc:
        status = "error"
        error = str(exc)
        payload = ""

    current_hash = _hash_text(payload) if payload else None
    return {
        "source_id": source_id,
        "type": source_type,
        "url": url or None,
        "path": local_path or None,
        "last_checked_at": checked_at,
        "current_hash": current_hash,
        "status": status,
        "error": error,
        "bytes": len(payload.encode("utf-8")) if payload else 0,
    }


def check_sources(project_path: str | Path, *, write: bool = True) -> dict[str, Any]:
    root = Path(project_path).resolve()
    manifest = load_dependency_manifest(root)
    sources = dependency_sources(manifest)
    previous = load_snapshot_state(root)
    previous_sources = previous.get("sources", {})
    current_sources: dict[str, dict[str, Any]] = {}
    changed: list[str] = []
    errors: list[dict[str, Any]] = []
    for source in sources:
        source_id = source["id"]
        old = previous_sources.get(source_id, {})
        snapshot = snapshot_source(root, source)
        snapshot["last_hash"] = old.get("current_hash")
        snapshot["changed"] = bool(snapshot.get("current_hash") and old.get("current_hash") and snapshot.get("current_hash") != old.get("current_hash"))
        snapshot["documents"] = source.get("documents", [])
        current_sources[source_id] = snapshot
        if snapshot["changed"]:
            changed.append(source_id)
        if snapshot["status"] != "ok":
            errors.append({"source_id": source_id, "error": snapshot.get("error")})
    state = {
        "checked_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "manifest_path": manifest.get("path"),
        "sources": current_sources,
    }
    if write:
        write_snapshot_state(root, state)
    return {
        "status": "checked",
        "manifest_path": manifest.get("path"),
        "sources": list(current_sources.values()),
        "changed_sources": changed,
        "errors": errors,
        "state_path": DEFAULT_SNAPSHOT_PATH,
    }


def changed_sources(project_path: str | Path) -> dict[str, Any]:
    state = load_snapshot_state(project_path)
    changed = [source_id for source_id, item in sorted(state.get("sources", {}).items()) if item.get("changed")]
    return {"changed_sources": changed, "state_path": DEFAULT_SNAPSHOT_PATH}


def affected_documents(project_path: str | Path, *, source_ids: list[str] | None = None) -> dict[str, Any]:
    root = Path(project_path).resolve()
    manifest = load_dependency_manifest(root)
    state = load_snapshot_state(root)
    if source_ids is None:
        source_ids = [source_id for source_id, item in state.get("sources", {}).items() if item.get("changed")]
    selected = set(source_ids)
    affected: dict[str, list[str]] = {}
    for row in iter_document_dependencies(manifest):
        source_id = row["source"]["id"]
        if source_id in selected:
            affected.setdefault(row["document"], []).append(source_id)
    return {
        "changed_sources": sorted(selected),
        "affected_documents": [
            {"path": path, "sources": sorted(sources)}
            for path, sources in sorted(affected.items())
        ],
        "manifest_path": manifest.get("path"),
    }

