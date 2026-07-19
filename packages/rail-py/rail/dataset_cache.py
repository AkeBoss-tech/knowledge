"""Build and inspect rebuildable DuckDB caches for catalogued datasets."""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any

from rail.datasets import load_catalog, snapshot_catalog, validate_catalog


DEFAULT_CACHE_PATH = "artifacts/data/datasets.duckdb"
DEFAULT_CACHE_STATE_PATH = "research_plan/state/dataset_cache.json"
_IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_]")


def _safe_identifier(value: str, *, prefix: str = "") -> str:
    normalized = _IDENTIFIER_RE.sub("_", value).strip("_") or "dataset"
    if normalized[0].isdigit():
        normalized = f"_{normalized}"
    return f"{prefix}{normalized.lower()}"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _state_path(root: Path) -> Path:
    return root / DEFAULT_CACHE_STATE_PATH


def _load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {"datasets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"datasets": {}}
    except json.JSONDecodeError:
        return {"datasets": {}}


def _write_state(root: Path, state: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dataset_indexes(dataset: dict[str, Any], columns: set[str]) -> list[str]:
    requested = dataset.get("indexes", [])
    if isinstance(requested, str):
        requested = [requested]
    primary_key = dataset.get("primary_key") or []
    if isinstance(primary_key, str):
        primary_key = [primary_key]
    return list(dict.fromkeys(str(value) for value in [*primary_key, *requested] if str(value) in columns))


def _sqlite_type(declared: str) -> str:
    value = declared.upper()
    if "INT" in value:
        return "BIGINT"
    if any(token in value for token in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE"
    if "BOOL" in value:
        return "BOOLEAN"
    return "VARCHAR"


def _copy_sqlite(connection, target_table: str, path: Path, dataset: dict[str, Any]) -> None:
    source_sql = str(dataset.get("query") or "").strip()
    table = str(dataset.get("table") or "").strip()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source:
        if table:
            escaped_table = table.replace('"', '""')
            definition = source.execute(f'PRAGMA table_info("{escaped_table}")').fetchall()
            columns = [(str(row[1]), _sqlite_type(str(row[2] or ""))) for row in definition]
            select_sql = f'SELECT * FROM "{escaped_table}"'
        else:
            cursor = source.execute(f"SELECT * FROM ({source_sql}) AS source_query LIMIT 0")
            columns = [(str(item[0]), "VARCHAR") for item in cursor.description or []]
            select_sql = source_sql
        if not columns:
            raise ValueError("SQLite dataset produced no columns")
        connection.execute(f"DROP TABLE IF EXISTS {_quote(target_table)}")
        connection.execute(
            f"CREATE TABLE {_quote(target_table)} ({', '.join(f'{_quote(name)} {data_type}' for name, data_type in columns)})"
        )
        cursor = source.execute(select_sql)
        placeholders = ", ".join("?" for _ in columns)
        insert = f"INSERT INTO {_quote(target_table)} VALUES ({placeholders})"
        while rows := cursor.fetchmany(10_000):
            connection.executemany(insert, rows)


def build_cache(project_path: str | Path, *, dataset_ids: list[str] | None = None) -> dict[str, Any]:
    root = Path(project_path).resolve()
    validation = validate_catalog(root)
    if not validation["ok"]:
        return {"status": "invalid", **validation}
    snapshots = snapshot_catalog(root, write=True)
    if snapshots["errors"]:
        return {"status": "source_error", **snapshots}
    selected = set(dataset_ids or [])
    catalog = {str(item["id"]): item for item in load_catalog(root)["datasets"]}
    unknown = sorted(selected - set(catalog))
    if unknown:
        return {"status": "invalid", "errors": [f"unknown dataset ids: {', '.join(unknown)}"]}
    records = {str(item["id"]): item for item in snapshots["datasets"]}
    chosen = sorted(selected or set(catalog))
    cache_path = root / DEFAULT_CACHE_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import duckdb
    except ImportError as exc:
        return {"status": "unavailable", "error": "DuckDB is required: pip install 'krail[local]'", "detail": str(exc)}

    state = _load_state(root)
    state_datasets = dict(state.get("datasets") or {})
    built: list[dict[str, Any]] = []
    connection = duckdb.connect(str(cache_path))
    try:
        for dataset_id in chosen:
            dataset = catalog[dataset_id]
            source_path = root / str(dataset["path"])
            table_name = _safe_identifier(dataset_id, prefix="data_")
            data_format = str(dataset["format"]).lower()
            if data_format == "sqlite":
                _copy_sqlite(connection, table_name, source_path, dataset)
            else:
                readers = {
                    "csv": "read_csv_auto",
                    "json": "read_json_auto",
                    "jsonl": "read_json_auto",
                    "parquet": "read_parquet",
                }
                reader = readers[data_format]
                connection.execute(f"CREATE OR REPLACE TABLE {_quote(table_name)} AS SELECT * FROM {reader}(?)", [str(source_path)])
            columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table_name)})").fetchall()}
            indexes = _dataset_indexes(dataset, columns)
            for column in indexes:
                index_name = _safe_identifier(f"idx_{table_name}_{column}")
                connection.execute(f"DROP INDEX IF EXISTS {_quote(index_name)}")
                connection.execute(f"CREATE INDEX {_quote(index_name)} ON {_quote(table_name)} ({_quote(column)})")
            row_count = int(connection.execute(f"SELECT count(*) FROM {_quote(table_name)}").fetchone()[0])
            source = records[dataset_id]
            entry = {
                "id": dataset_id,
                "table": table_name,
                "row_count": row_count,
                "indexes": indexes,
                "source_content_hash": source["content_hash"],
                "source_schema_hash": source["schema_hash"],
                "built_at": _dt.datetime.now(_dt.UTC).isoformat(),
            }
            state_datasets[dataset_id] = entry
            built.append(entry)
    finally:
        connection.close()
    state = {"cache_path": DEFAULT_CACHE_PATH, "updated_at": _dt.datetime.now(_dt.UTC).isoformat(), "datasets": state_datasets}
    _write_state(root, state)
    return {"status": "built", "cache_path": DEFAULT_CACHE_PATH, "datasets": built, "state_path": DEFAULT_CACHE_STATE_PATH}


def cache_status(project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).resolve()
    state = _load_state(root)
    cache_path = root / DEFAULT_CACHE_PATH
    snapshots = snapshot_catalog(root, write=False)
    current = {str(item["id"]): item for item in snapshots.get("datasets", [])}
    results = []
    for dataset_id, entry in sorted((state.get("datasets") or {}).items()):
        snapshot = current.get(dataset_id)
        fresh = bool(snapshot and snapshot.get("content_hash") == entry.get("source_content_hash") and snapshot.get("schema_hash") == entry.get("source_schema_hash"))
        results.append({**entry, "fresh": fresh, "current_source_hash": snapshot.get("content_hash") if snapshot else None})
    return {"cache_path": DEFAULT_CACHE_PATH, "exists": cache_path.exists(), "datasets": results, "source_errors": snapshots.get("errors", [])}


def validate_cache(project_path: str | Path) -> dict[str, Any]:
    """Verify cache freshness and that recorded cache tables remain usable."""
    root = Path(project_path).resolve()
    status = cache_status(root)
    errors: list[str] = []
    if not status["exists"]:
        errors.append("dataset cache missing; run `krail --local datasets cache-build`")
    for source_error in status["source_errors"]:
        errors.append(f"source snapshot failed: {source_error.get('id')}: {source_error.get('error')}")
    for entry in status["datasets"]:
        if not entry["fresh"]:
            errors.append(f"stale cache table: {entry['table']} ({entry['id']})")
    if errors:
        return {"ok": False, "cache_path": DEFAULT_CACHE_PATH, "errors": errors, "datasets": status["datasets"]}
    try:
        import duckdb
        connection = duckdb.connect(str(root / DEFAULT_CACHE_PATH), read_only=True)
        try:
            available = {str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()}
            for entry in status["datasets"]:
                table = str(entry["table"])
                if table not in available:
                    errors.append(f"recorded cache table is missing: {table}")
                    continue
                actual_rows = int(connection.execute(f"SELECT count(*) FROM {_quote(table)}").fetchone()[0])
                if actual_rows != int(entry["row_count"]):
                    errors.append(f"row-count drift for {table}: expected {entry['row_count']}, found {actual_rows}")
        finally:
            connection.close()
    except ImportError:
        errors.append("DuckDB is required to validate the dataset cache")
    return {"ok": not errors, "cache_path": DEFAULT_CACHE_PATH, "errors": errors, "datasets": status["datasets"]}


def benchmark_cache(project_path: str | Path, *, dataset_id: str, iterations: int = 3) -> dict[str, Any]:
    """Measure a small set of repeatable cache queries for a real dataset."""
    if iterations < 1 or iterations > 20:
        raise ValueError("iterations must be between 1 and 20")
    validation = validate_cache(project_path)
    if not validation["ok"]:
        return {"status": "invalid", **validation}
    entry = next((item for item in validation["datasets"] if item["id"] == dataset_id), None)
    if not entry:
        return {"status": "invalid", "errors": [f"unknown cached dataset: {dataset_id}"]}
    import duckdb
    root = Path(project_path).resolve()
    connection = duckdb.connect(str(root / DEFAULT_CACHE_PATH), read_only=True)
    try:
        durations_ms: list[float] = []
        for _ in range(iterations):
            started = time.perf_counter()
            connection.execute(f"SELECT count(*) FROM {_quote(str(entry['table']))}").fetchone()
            durations_ms.append((time.perf_counter() - started) * 1000)
    finally:
        connection.close()
    return {
        "status": "benchmarked",
        "dataset_id": dataset_id,
        "table": entry["table"],
        "row_count": entry["row_count"],
        "iterations": iterations,
        "count_query_ms": {"min": round(min(durations_ms), 3), "median": round(statistics.median(durations_ms), 3), "max": round(max(durations_ms), 3)},
        "cache_path": DEFAULT_CACHE_PATH,
    }
