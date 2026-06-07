"""
Small local JSON store used by the optional API runtime.

This intentionally preserves the old query/mutation call shape while removing
the remote backend dependency. It is not meant to be a database abstraction for
the long term; it is a bridge while the API is slimmed down around local project
state.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings


class LocalStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.local_store_path

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _write(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _collection(fn_path: str) -> str:
        prefix, _, action = fn_path.partition(":")
        if prefix == "configs":
            if "Api" in action:
                return "apiConfigs"
            if "Ontology" in action:
                return "ontologyConfigs"
            if "Pipeline" in action:
                return "pipelineConfigs"
        return prefix or "default"

    @staticmethod
    def _id_keys(collection: str) -> tuple[str, ...]:
        if collection == "jobs":
            return ("jobId", "_id", "id")
        if collection in {"apiConfigs", "ontologyConfigs", "pipelineConfigs", "connectorTemplates"}:
            return ("slug", "_id", "id")
        if collection == "projects":
            return ("slug", "_id", "id")
        return ("_id", "id", "slug", "jobId", "sessionId")

    @classmethod
    def _record_id(cls, collection: str, record: dict[str, Any]) -> str | None:
        for key in cls._id_keys(collection):
            value = record.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _matches(record: dict[str, Any], args: dict[str, Any]) -> bool:
        for key, value in args.items():
            if value in (None, "") or key in {"limit", "offset"}:
                continue
            aliases = {
                "projectId": ("projectId", "_id", "id"),
                "id": ("id", "_id", "jobId", "sessionId"),
                "slug": ("slug",),
                "jobId": ("jobId", "_id", "id"),
                "sessionId": ("sessionId", "_id", "id"),
            }.get(key, (key,))
            if not any(str(record.get(alias, "")) == str(value) for alias in aliases):
                return False
        return True

    async def query(self, fn_path: str, args: dict | None = None):
        args = args or {}
        data = self._read()
        collection = self._collection(fn_path)
        records = list(data.get(collection, []))
        action = fn_path.partition(":")[2]

        if action.startswith("list"):
            filtered = [item for item in records if self._matches(item, args)]
            limit = args.get("limit")
            return filtered[: int(limit)] if limit else filtered

        if action.startswith("get"):
            for item in records:
                if self._matches(item, args):
                    return item
            return None

        return None

    async def mutation(self, fn_path: str, args: dict | None = None):
        args = dict(args or {})
        data = self._read()
        collection = self._collection(fn_path)
        records = list(data.get(collection, []))
        action = fn_path.partition(":")[2]

        if action.startswith(("delete", "remove")):
            before = len(records)
            records = [item for item in records if not self._matches(item, args)]
            data[collection] = records
            self._write(data)
            return {"removed": before - len(records)}

        if action in {"appendLog"}:
            job_id = args.get("jobId")
            for item in records:
                if item.get("jobId") == job_id:
                    item.setdefault("logs", []).append(args)
                    self._write(data)
                    return item
            return None

        if action.startswith(("update", "upsert", "pause", "resume")):
            for idx, item in enumerate(records):
                if self._matches(item, args):
                    updated = {**item, **args, "updatedAt": time.time()}
                    records[idx] = updated
                    data[collection] = records
                    self._write(data)
                    return updated

        record_id = (
            args.get("jobId")
            or args.get("sessionId")
            or args.get("slug")
            or args.get("_id")
            or args.get("id")
            or f"local_{collection}_{uuid.uuid4().hex[:12]}"
        )
        record = {
            "_id": record_id,
            "id": record_id,
            **args,
            "createdAt": args.get("createdAt", time.time()),
            "updatedAt": time.time(),
        }
        if collection == "jobs":
            record.setdefault("jobId", record_id)
        records.append(record)
        data[collection] = records
        self._write(data)

        if collection == "jobs":
            return {"jobId": record["jobId"], "status": record.get("status", "queued")}
        return record_id


local_store = LocalStore()
