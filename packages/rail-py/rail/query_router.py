"""Bounded, provenance-carrying queries across KRAIL data backends."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from rail.dataset_cache import DEFAULT_CACHE_PATH, DEFAULT_CACHE_STATE_PATH
from rail.datasets import load_catalog


_READ_ONLY_RE = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
_TABLE_RE = re.compile(r"\b(?:from|join)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)


def _require_read_only(sql: str) -> str:
    normalized = sql.strip().rstrip(";").strip()
    if not normalized or not _READ_ONLY_RE.match(normalized):
        raise ValueError("only SELECT and WITH queries are allowed")
    return normalized


def _bounded_rows(execute, sql: str, limit: int) -> tuple[list[str], list[list[Any]], bool]:
    if limit < 1 or limit > 10_000:
        raise ValueError("limit must be between 1 and 10000")
    wrapped = f"SELECT * FROM ({sql}) AS krail_query_result LIMIT {limit + 1}"
    cursor = execute(wrapped)
    columns = [str(item[0]) for item in cursor.description or []]
    rows = cursor.fetchall()
    return columns, [list(row) for row in rows[:limit]], len(rows) > limit


def _cache_state(root: Path) -> dict[str, Any]:
    path = root / DEFAULT_CACHE_STATE_PATH
    if not path.exists():
        return {"datasets": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"datasets": {}}


def query_cache(project_path: str | Path, sql: str, *, limit: int = 100) -> dict[str, Any]:
    root = Path(project_path).resolve()
    query = _require_read_only(sql)
    cache_path = root / DEFAULT_CACHE_PATH
    if not cache_path.exists():
        raise FileNotFoundError("dataset cache missing; run `krail --local datasets cache-build`")
    import duckdb

    connection = duckdb.connect(str(cache_path), read_only=True)
    try:
        columns, rows, truncated = _bounded_rows(connection.execute, query, limit)
    finally:
        connection.close()
    tables = {item.lower() for item in _TABLE_RE.findall(query)}
    state = _cache_state(root)
    datasets = [entry for entry in (state.get("datasets") or {}).values() if str(entry.get("table", "")).lower() in tables]
    return {
        "backend": "dataset_cache",
        "cache_path": DEFAULT_CACHE_PATH,
        "datasets": datasets,
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "limit": limit,
    }


def query_ontology(project_path: str | Path, sql: str, *, limit: int = 100, ontology_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_path).resolve()
    query = _require_read_only(sql)
    resolved_ontology_path = Path(ontology_path) if ontology_path else root / ".ontology" / "onto.duckdb"
    if not resolved_ontology_path.exists():
        raise FileNotFoundError("ontology DuckDB mirror missing; run hydration first")
    import duckdb

    connection = duckdb.connect(str(resolved_ontology_path), read_only=True)
    try:
        columns, rows, truncated = _bounded_rows(connection.execute, query, limit)
    finally:
        connection.close()
    return {"backend": "ontology_duckdb", "ontology_path": str(resolved_ontology_path.relative_to(root)) if resolved_ontology_path.is_relative_to(root) else str(resolved_ontology_path), "columns": columns, "rows": rows, "truncated": truncated, "limit": limit}


def query_sqlite_source(project_path: str | Path, dataset_id: str, sql: str, *, limit: int = 100) -> dict[str, Any]:
    root = Path(project_path).resolve()
    query = _require_read_only(sql)
    catalog = {str(item.get("id")): item for item in load_catalog(root).get("datasets", [])}
    dataset = catalog.get(dataset_id)
    if not dataset:
        raise ValueError(f"unknown dataset: {dataset_id}")
    if str(dataset.get("format", "")).lower() != "sqlite":
        raise ValueError(f"dataset '{dataset_id}' is not a SQLite source")
    source_path = root / str(dataset["path"])
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as connection:
        columns, rows, truncated = _bounded_rows(connection.execute, query, limit)
    return {
        "backend": "source_sqlite",
        "dataset_id": dataset_id,
        "source_path": str(dataset["path"]),
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "limit": limit,
    }


def route_sql(project_path: str | Path, sql: str, *, backend: str = "auto", limit: int = 100, ontology_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_path).resolve()
    if backend == "cache":
        return query_cache(root, sql, limit=limit)
    if backend == "ontology":
        return query_ontology(root, sql, limit=limit, ontology_path=ontology_path)
    if backend != "auto":
        raise ValueError("backend must be auto, cache, or ontology")
    cache_state = _cache_state(root)
    cache_tables = {str(item.get("table", "")).lower() for item in (cache_state.get("datasets") or {}).values()}
    query_tables = {item.lower() for item in _TABLE_RE.findall(sql)}
    if query_tables & cache_tables:
        return query_cache(root, sql, limit=limit)
    return query_ontology(root, sql, limit=limit, ontology_path=ontology_path)
