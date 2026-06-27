from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml


ITEM_STATES = {"pending", "reserved", "running", "done", "failed", "skipped", "reviewed", "promoted"}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _slug(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:80] or "queue"


class QueueEngine:
    """Repo-backed inventory queues with atomic-ish local reservation locks."""

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.root = self.project_path / "research_plan" / "queues"
        self.locks_dir = self.project_path / ".krail" / "locks"

    def _queue_dir(self, queue_id: str) -> Path:
        return self.root / _slug(queue_id)

    def _items_path(self, queue_id: str) -> Path:
        return self._queue_dir(queue_id) / "items.jsonl"

    def _config_path(self, queue_id: str) -> Path:
        return self._queue_dir(queue_id) / "queue.yaml"

    def _claims_dir(self, queue_id: str) -> Path:
        return self._queue_dir(queue_id) / "claims"

    def _lock_path(self, queue_id: str) -> Path:
        return self.locks_dir / f"queue-{_slug(queue_id)}.lock"

    def _acquire_lock(self, queue_id: str) -> Path:
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        lock = self._lock_path(queue_id)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"queue already locked: {queue_id} ({lock})") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"queue": queue_id, "pid": os.getpid(), "created_at": _now().isoformat()}) + "\n")
        return lock

    def _read_items(self, queue_id: str) -> list[dict[str, Any]]:
        path = self._items_path(queue_id)
        if not path.exists():
            raise FileNotFoundError(f"Queue not found: {queue_id}")
        items = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                loaded = json.loads(line)
                if isinstance(loaded, dict):
                    items.append(loaded)
        return items

    def _write_items(self, queue_id: str, items: list[dict[str, Any]]) -> None:
        path = self._items_path(queue_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in items), encoding="utf-8")

    def _load_inventory(self, source: Path) -> list[dict[str, Any]]:
        suffix = source.suffix.lower()
        if suffix == ".csv":
            with source.open(newline="", encoding="utf-8") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        text = source.read_text(encoding="utf-8")
        if suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        loaded = json.loads(text)
        if isinstance(loaded, list):
            return [item for item in loaded if isinstance(item, dict)]
        if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
            return [item for item in loaded["items"] if isinstance(item, dict)]
        raise ValueError("inventory must be CSV, JSONL, a JSON list, or {'items': [...]}")

    def init(self, queue_id: str, *, source: str, key: str, force: bool = False) -> dict[str, Any]:
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = self.project_path / source_path
        if self._items_path(queue_id).exists() and not force:
            return {"status": "exists", "queue": queue_id, "path": str(self._queue_dir(queue_id).relative_to(self.project_path))}
        raw_items = self._load_inventory(source_path)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, payload in enumerate(raw_items):
            raw_key = str(payload.get(key) or "").strip()
            item_id = _slug(raw_key) if raw_key else hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
            if item_id in seen:
                continue
            seen.add(item_id)
            items.append(
                {
                    "id": item_id,
                    "status": "pending",
                    "payload": payload,
                    "attempts": 0,
                    "created_at": _now().isoformat(),
                    "updated_at": _now().isoformat(),
                }
            )
        queue_dir = self._queue_dir(queue_id)
        queue_dir.mkdir(parents=True, exist_ok=True)
        self._claims_dir(queue_id).mkdir(parents=True, exist_ok=True)
        self._config_path(queue_id).write_text(
            yaml.safe_dump({"id": queue_id, "source": str(source_path.relative_to(self.project_path) if source_path.is_relative_to(self.project_path) else source_path), "key": key, "items": len(items)}, sort_keys=False),
            encoding="utf-8",
        )
        self._write_items(queue_id, items)
        return {"status": "initialized", "queue": queue_id, "items": len(items), "path": str(queue_dir.relative_to(self.project_path))}

    def status(self, queue_id: str) -> dict[str, Any]:
        items = self._read_items(queue_id)
        counts = {state: 0 for state in sorted(ITEM_STATES)}
        for item in items:
            counts[str(item.get("status") or "pending")] = counts.get(str(item.get("status") or "pending"), 0) + 1
        claims = []
        for path in sorted(self._claims_dir(queue_id).glob("*.json")):
            try:
                claims.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {"queue": queue_id, "items": len(items), "counts": counts, "claims": claims[-10:]}

    def claim(
        self,
        queue_id: str,
        *,
        limit: int = 10,
        where: list[str] | None = None,
        owner: str | None = None,
        lease_minutes: int = 120,
    ) -> dict[str, Any]:
        lock = self._acquire_lock(queue_id)
        try:
            items = self._read_items(queue_id)
            filters = _parse_filters(where or [])
            selected = []
            for item in items:
                if item.get("status") not in {"pending", "failed"}:
                    continue
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                if any(str(payload.get(k) or item.get(k) or "") != v for k, v in filters.items()):
                    continue
                selected.append(item)
                if len(selected) >= limit:
                    break
            now = _now()
            batch_id = f"batch_{_slug(queue_id)}_{now.strftime('%Y%m%d%H%M%S')}_{hashlib.sha1(','.join(item['id'] for item in selected).encode('utf-8')).hexdigest()[:8]}"
            expires_at = now + _dt.timedelta(minutes=lease_minutes)
            for item in selected:
                item["status"] = "reserved"
                item["batch_id"] = batch_id
                item["claimed_by"] = owner or os.environ.get("USER") or "local"
                item["claimed_at"] = now.isoformat()
                item["expires_at"] = expires_at.isoformat()
                item["attempts"] = int(item.get("attempts") or 0) + 1
                item["updated_at"] = now.isoformat()
            self._write_items(queue_id, items)
            claim = {
                "batch_id": batch_id,
                "queue": queue_id,
                "status": "reserved",
                "owner": owner or os.environ.get("USER") or "local",
                "claimed_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "item_ids": [item["id"] for item in selected],
                "items": selected,
            }
            claim_path = self._claims_dir(queue_id) / f"{batch_id}.json"
            claim_path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return {"status": "claimed", "queue": queue_id, "batch": claim, "path": str(claim_path.relative_to(self.project_path))}
        finally:
            if lock.exists():
                lock.unlink()

    def update_batch(self, queue_id: str, batch_id: str, *, status: str) -> dict[str, Any]:
        if status not in {"done", "failed", "skipped", "reviewed", "promoted", "pending"}:
            raise ValueError("status must be done, failed, skipped, reviewed, promoted, or pending")
        items = self._read_items(queue_id)
        updated = []
        now = _now().isoformat()
        for item in items:
            if item.get("batch_id") == batch_id:
                item["status"] = status
                item["updated_at"] = now
                updated.append(item["id"])
        self._write_items(queue_id, items)
        claim_path = self._claims_dir(queue_id) / f"{batch_id}.json"
        if claim_path.exists():
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            claim["status"] = status
            claim["updated_at"] = now
            claim_path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"status": "updated", "queue": queue_id, "batch_id": batch_id, "item_ids": updated}

    def release(self, queue_id: str, *, stale: bool = False) -> dict[str, Any]:
        items = self._read_items(queue_id)
        now = _now()
        released = []
        for item in items:
            if item.get("status") not in {"reserved", "running"}:
                continue
            expired = False
            if stale and item.get("expires_at"):
                try:
                    expired = _dt.datetime.fromisoformat(str(item["expires_at"])) <= now
                except Exception:
                    expired = True
            if stale and not expired:
                continue
            item["status"] = "pending"
            item.pop("batch_id", None)
            item["updated_at"] = now.isoformat()
            released.append(item["id"])
        self._write_items(queue_id, items)
        return {"status": "released", "queue": queue_id, "item_ids": released}


def _parse_filters(filters: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in filters:
        if "=" not in item:
            raise ValueError(f"filter must be key=value: {item}")
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed
