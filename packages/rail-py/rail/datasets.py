"""Dataset catalog contracts for large, ontology-backed sources.

The catalog deliberately describes data without loading it.  It is the stable
boundary between authoritative raw data and later cache/projection builders.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CATALOG_PATH = "sources/datasets.yaml"
DEFAULT_SNAPSHOT_PATH = "research_plan/state/dataset_snapshots.json"
SUPPORTED_FORMATS = {"csv", "json", "jsonl", "parquet", "sqlite"}


def catalog_path(project_path: str | Path) -> Path:
    return Path(project_path).resolve() / DEFAULT_CATALOG_PATH


def load_catalog(project_path: str | Path) -> dict[str, Any]:
    path = catalog_path(project_path)
    if not path.exists():
        return {"version": 1, "datasets": [], "path": None}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{DEFAULT_CATALOG_PATH} must contain a YAML mapping")
    data.setdefault("version", 1)
    data.setdefault("datasets", [])
    data["path"] = DEFAULT_CATALOG_PATH
    return data


def validate_catalog(project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).resolve()
    try:
        catalog = load_catalog(root)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {"ok": False, "path": DEFAULT_CATALOG_PATH, "errors": [str(exc)], "warnings": [], "datasets": 0}

    errors: list[str] = []
    warnings: list[str] = []
    datasets = catalog.get("datasets")
    if not isinstance(datasets, list):
        return {"ok": False, "path": catalog.get("path"), "errors": ["datasets must be a list"], "warnings": [], "datasets": 0}

    seen: set[str] = set()
    for index, dataset in enumerate(datasets, start=1):
        prefix = f"dataset entry {index}"
        if not isinstance(dataset, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        dataset_id = str(dataset.get("id") or "").strip()
        if not dataset_id:
            errors.append(f"{prefix} missing id")
        elif dataset_id in seen:
            errors.append(f"duplicate dataset id: {dataset_id}")
        else:
            seen.add(dataset_id)
        data_format = str(dataset.get("format") or "").strip().lower()
        if data_format not in SUPPORTED_FORMATS:
            errors.append(f"{dataset_id or prefix} format must be one of: {', '.join(sorted(SUPPORTED_FORMATS))}")
        source_path = str(dataset.get("path") or "").strip()
        if not source_path:
            errors.append(f"{dataset_id or prefix} missing path")
        elif Path(source_path).is_absolute() or ".." in Path(source_path).parts:
            errors.append(f"{dataset_id or prefix} path must be repository-relative")
        elif not (root / source_path).exists():
            warnings.append(f"dataset path does not exist: {source_path}")
        if data_format == "sqlite" and not (str(dataset.get("table") or "").strip() or str(dataset.get("query") or "").strip()):
            errors.append(f"{dataset_id or prefix} sqlite dataset requires table or query")
        if not dataset.get("primary_key"):
            warnings.append(f"{dataset_id or prefix} missing primary_key; incremental entity resolution will be unavailable")
        indexes = dataset.get("indexes", [])
        if not isinstance(indexes, (str, list)):
            errors.append(f"{dataset_id or prefix} indexes must be a string or list of column names")
        refresh = dataset.get("refresh", "manual")
        if refresh not in {"manual", "append_only", "replace", "scheduled"}:
            errors.append(f"{dataset_id or prefix} refresh must be manual, append_only, replace, or scheduled")
        if "projection" not in dataset:
            warnings.append(f"{dataset_id or prefix} has no semantic projection; it will remain data-only")
    return {"ok": not errors, "path": catalog.get("path"), "errors": errors, "warnings": warnings, "datasets": len(datasets)}


def list_datasets(project_path: str | Path) -> dict[str, Any]:
    catalog = load_catalog(project_path)
    return {"path": catalog.get("path"), "datasets": catalog.get("datasets", [])}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_for(dataset: dict[str, Any], path: Path) -> list[dict[str, str]]:
    data_format = str(dataset.get("format") or "").lower()
    if data_format == "csv":
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            header = next(csv.reader(handle), [])
        return [{"name": name, "type": "unknown"} for name in header]
    if data_format == "sqlite" and dataset.get("table"):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            rows = connection.execute(f'PRAGMA table_info("{str(dataset["table"]).replace(chr(34), chr(34) * 2)}")').fetchall()
        return [{"name": str(row[1]), "type": str(row[2] or "unknown")} for row in rows]
    schema = dataset.get("schema") or []
    return [item for item in schema if isinstance(item, dict)]


def snapshot_catalog(project_path: str | Path, *, write: bool = True) -> dict[str, Any]:
    root = Path(project_path).resolve()
    validation = validate_catalog(root)
    if not validation["ok"]:
        return {"status": "invalid", **validation}
    records: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for dataset in load_catalog(root)["datasets"]:
        dataset_id = str(dataset["id"])
        path = root / str(dataset["path"])
        try:
            schema = _schema_for(dataset, path)
            records[dataset_id] = {
                "id": dataset_id,
                "path": str(dataset["path"]),
                "format": dataset["format"],
                "bytes": path.stat().st_size,
                "content_hash": _hash_file(path),
                "schema_hash": hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
                "schema": schema,
                "snapshotted_at": _dt.datetime.now(_dt.UTC).isoformat(),
            }
        except (OSError, sqlite3.Error) as exc:
            errors.append({"id": dataset_id, "error": str(exc)})
    state = {"catalog_path": DEFAULT_CATALOG_PATH, "snapshotted_at": _dt.datetime.now(_dt.UTC).isoformat(), "datasets": records}
    if write:
        state_path = root / DEFAULT_SNAPSHOT_PATH
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "snapshotted", "datasets": list(records.values()), "errors": errors, "state_path": DEFAULT_SNAPSHOT_PATH}
