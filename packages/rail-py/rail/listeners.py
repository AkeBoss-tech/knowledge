from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_LISTENER_TYPES = {"file", "http", "rss", "schedule", "command", "github"}

LISTENER_TEMPLATES: dict[str, dict[str, Any]] = {
    "website_change_monitor": {
        "id": "website_change_monitor",
        "type": "http",
        "url": "https://example.com",
        "interval": "1h",
        "change_detection": {"mode": "hash", "normalize": "readable_text"},
        "on_change": {"workflow": "refresh_source_notes", "dry_run_first": True},
    },
    "github_issue_triage": {
        "id": "github_issue_triage",
        "type": "github",
        "repo": "owner/repo",
        "events": ["issues.opened"],
        "interval": "5m",
        "on_change": {"workflow": "triage_github_issue", "dry_run_first": True},
    },
    "new_source_ingest": {
        "id": "new_source_ingest",
        "type": "file",
        "glob": "sources/**/*",
        "on_change": {"workflow": "ingest_new_sources"},
    },
    "rss_literature_watch": {
        "id": "rss_literature_watch",
        "type": "rss",
        "url": "https://example.com/feed.xml",
        "interval": "1h",
        "on_change": {"workflow": "weekly_literature_refresh", "dry_run_first": True},
    },
    "email_research_inbox": {
        "id": "email_research_inbox",
        "type": "command",
        "run": "python scripts/check_email_research_inbox.py",
        "interval": "10m",
        "on_event": {"workflow": "triage_inbox", "dry_run_first": True},
    },
    "weekly_review": {
        "id": "weekly_review",
        "type": "schedule",
        "interval": "7d",
        "on_change": {"workflow": "weekly_review", "dry_run_first": True},
    },
}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _digest(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _parse_interval(value: Any, *, default_seconds: int = 60) -> int:
    if value is None:
        return default_seconds
    if isinstance(value, (int, float)):
        return max(1, int(value))
    text = str(value).strip().lower()
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", text)
    if not match:
        return default_seconds
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _normalize_http_body(body: bytes, *, mode: str = "raw") -> str:
    text = body.decode("utf-8", errors="replace")
    if mode in {"readable_text", "text"}:
        text = re.sub(r"(?is)<script.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
    return text


def emit_event(
    *,
    source: str,
    target: str,
    payload: dict[str, Any] | None = None,
    changed: bool = True,
    hash: str | None = None,
) -> dict[str, Any]:
    """Return a command-listener event object suitable for JSON stdout."""

    event: dict[str, Any] = {
        "source": source,
        "target": target,
        "changed": changed,
        "payload": payload or {},
    }
    if hash:
        event["hash"] = hash
    return event


class ListenerEngine:
    """Poll local listener specs and turn observations into replayable events."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.project_path = Path(runtime.project_path)
        self.krail_dir = Path(runtime.krail_dir)
        self.listeners_dir = self.project_path / "research_plan" / "listeners"
        self.events_dir = self.project_path / "research_plan" / "events"
        self.state_path = self.krail_dir / "listener_state.json"
        self.locks_dir = self.krail_dir / "locks"

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"listeners": {}, "dedupe": {}}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"listeners": {}, "dedupe": {}}
        if not isinstance(data, dict):
            return {"listeners": {}, "dedupe": {}}
        data.setdefault("listeners", {})
        data.setdefault("dedupe", {})
        return data

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _listener_files(self) -> list[Path]:
        return sorted(self.listeners_dir.glob("*.yaml")) + sorted(self.listeners_dir.glob("*.yml"))

    def _load_spec(self, path: Path) -> dict[str, Any]:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"listener spec must be a mapping: {path}")
        data.setdefault("id", path.stem)
        data["_path"] = str(path.relative_to(self.project_path))
        return data

    def _spec_path_for(self, listener_id: str) -> Path:
        return self.listeners_dir / f"{_safe_id(listener_id)}.yaml"

    def init_spec(self, template: str, *, listener_id: str | None = None, force: bool = False) -> dict[str, Any]:
        if template in LISTENER_TEMPLATES:
            spec = json.loads(json.dumps(LISTENER_TEMPLATES[template]))
        elif template in SUPPORTED_LISTENER_TYPES:
            spec = {"id": listener_id or f"{template}_listener", "type": template}
        else:
            raise ValueError(f"Unknown listener template or type: {template}")
        if listener_id:
            spec["id"] = listener_id
        path = self._spec_path_for(str(spec["id"]))
        if path.exists() and not force:
            return {"status": "exists", "path": str(path.relative_to(self.project_path)), "listener": spec}
        self.listeners_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
        return {"status": "written", "path": str(path.relative_to(self.project_path)), "listener": spec}

    def templates(self) -> dict[str, Any]:
        return {"templates": sorted(LISTENER_TEMPLATES), "types": sorted(SUPPORTED_LISTENER_TYPES)}

    def validate_spec(self, listener_id: str | None = None) -> dict[str, Any]:
        specs = [self._resolve_spec(listener_id)] if listener_id else [self._load_spec(path) for path in self._listener_files()]
        results = [self._validate_loaded_spec(spec) for spec in specs]
        errors = [error for result in results for error in result["errors"]]
        return {"ok": not errors, "results": results, "errors": errors}

    def _validate_loaded_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        listener_id = spec.get("id")
        kind = str(spec.get("type") or "").strip().lower()
        if not isinstance(listener_id, str) or not listener_id.strip():
            errors.append("id must be a non-empty string")
        if kind not in SUPPORTED_LISTENER_TYPES:
            errors.append(f"type must be one of: {', '.join(sorted(SUPPORTED_LISTENER_TYPES))}")
        if kind == "file" and not (spec.get("path") or spec.get("glob")):
            errors.append("file listener requires path or glob")
        if kind in {"http", "rss"} and not spec.get("url"):
            errors.append(f"{kind} listener requires url")
        if kind == "command" and not spec.get("run"):
            errors.append("command listener requires run")
        if kind == "github" and not spec.get("repo"):
            errors.append("github listener requires repo")
        trigger = self._trigger(spec)
        workflow = trigger.get("workflow")
        if workflow:
            try:
                self.runtime.workflow_show(str(workflow))
            except Exception as exc:
                warnings.append(f"trigger workflow {workflow!r} is not materialized or available: {exc}")
        return {"id": listener_id, "type": kind, "path": spec.get("_path"), "ok": not errors, "errors": errors, "warnings": warnings}

    def list_specs(self) -> dict[str, Any]:
        listeners: list[dict[str, Any]] = []
        for path in self._listener_files():
            try:
                spec = self._load_spec(path)
                listeners.append(
                    {
                        "id": spec["id"],
                        "type": spec.get("type"),
                        "enabled": spec.get("enabled", True),
                        "path": spec["_path"],
                        "workflow": self._trigger(spec).get("workflow"),
                        "state": self._load_state().get("listeners", {}).get(str(spec["id"]), {}),
                    }
                )
            except Exception as exc:
                listeners.append({"id": path.stem, "path": str(path.relative_to(self.project_path)), "error": str(exc)})
        return {"listeners": listeners}

    def show_spec(self, listener_id: str) -> dict[str, Any]:
        return {"listener": self._resolve_spec(listener_id)}

    def _resolve_spec(self, listener_id: str) -> dict[str, Any]:
        for path in self._listener_files():
            spec = self._load_spec(path)
            if spec.get("id") == listener_id or path.stem == listener_id:
                return spec
        raise FileNotFoundError(f"Listener not found: {listener_id}")

    def _trigger(self, spec: dict[str, Any]) -> dict[str, Any]:
        trigger = spec.get("on_change") or spec.get("on_event") or {}
        return trigger if isinstance(trigger, dict) else {}

    def test(self, listener_id: str) -> dict[str, Any]:
        spec = self._resolve_spec(listener_id)
        validation = self._validate_loaded_spec(spec)
        if not validation["ok"]:
            return {"status": "invalid", "listener": spec["id"], "validation": validation}
        state = self._load_state()
        observed, _ = self._observe(spec, state)
        return {"status": "ok", "listener": spec["id"], "observations": observed}

    def poll(
        self,
        listener_id: str | None = None,
        *,
        dry_run: bool = False,
        execute: bool = True,
    ) -> dict[str, Any]:
        specs = [self._resolve_spec(listener_id)] if listener_id else [self._load_spec(path) for path in self._listener_files()]
        state = self._load_state()
        results = []
        for spec in specs:
            validation = self._validate_loaded_spec(spec)
            if not validation["ok"]:
                results.append({"listener": spec.get("id"), "status": "invalid", "validation": validation, "events": []})
                continue
            if spec.get("enabled", True) is False:
                results.append({"listener": spec["id"], "status": "disabled", "events": []})
                continue
            if not dry_run:
                lock = self._acquire_listener_lock(str(spec["id"]))
            else:
                lock = None
            try:
                current_listener_state = dict(state.get("listeners", {}).get(str(spec["id"]), {}))
                if self._in_backoff(current_listener_state):
                    results.append({"listener": spec["id"], "status": "backoff", "state": current_listener_state, "events": []})
                    continue
                try:
                    observations, next_state = self._observe(spec, state)
                except Exception as exc:
                    error_state = self._failure_state(current_listener_state, exc)
                    if not dry_run:
                        state.setdefault("listeners", {})[str(spec["id"])] = error_state
                        self._write_state(state)
                    results.append({"listener": spec["id"], "status": "error", "error": str(exc), "state": error_state, "events": []})
                    continue
                events = [self._event_from_observation(spec, observation) for observation in observations if observation.get("changed")]
                emitted = []
                for event in events:
                    dedupe_key = str(event["dedupe_key"])
                    if state.get("dedupe", {}).get(dedupe_key):
                        continue
                    if dry_run:
                        event["status"] = "dry_run"
                    else:
                        event["status"] = "recorded"
                        self._append_event(event)
                        state.setdefault("dedupe", {})[dedupe_key] = event["id"]
                        if execute:
                            event["status"] = "dispatched"
                            self._append_event_update(event)
                            event["workflow_result"] = self._invoke_trigger(spec, event)
                            event["status"] = self._status_from_workflow_result(event.get("workflow_result"))
                            self._append_event_update(event)
                    emitted.append(event)
                if not dry_run:
                    next_state = self._success_state(next_state)
                    state.setdefault("listeners", {})[str(spec["id"])] = next_state
                    self._write_state(state)
                results.append({"listener": spec["id"], "status": "ok", "events": emitted, "observations": observations})
            finally:
                if lock and lock.exists():
                    lock.unlink()
        return {"status": "ok", "dry_run": dry_run, "results": results}

    def _listener_lock_path(self, listener_id: str) -> Path:
        return self.locks_dir / f"listener-{_safe_id(listener_id)}.lock"

    def _acquire_listener_lock(self, listener_id: str) -> Path:
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        lock = self._listener_lock_path(listener_id)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"listener already running: {listener_id} ({lock})") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"listener": listener_id, "pid": os.getpid(), "created_at": _now().isoformat()}) + "\n")
        return lock

    def _in_backoff(self, listener_state: dict[str, Any]) -> bool:
        next_retry_at = listener_state.get("next_retry_at")
        if not next_retry_at:
            return False
        try:
            return _dt.datetime.fromisoformat(str(next_retry_at)) > _now()
        except Exception:
            return False

    def _failure_state(self, listener_state: dict[str, Any], exc: Exception) -> dict[str, Any]:
        now = _now()
        failure_count = int(listener_state.get("failure_count") or 0) + 1
        backoff_seconds = min(3600, 30 * (2 ** min(failure_count - 1, 6)))
        next_state = dict(listener_state)
        next_state.update(
            {
                "status": "error",
                "last_error": str(exc),
                "last_error_at": now.isoformat(),
                "failure_count": failure_count,
                "next_retry_at": (now + _dt.timedelta(seconds=backoff_seconds)).isoformat(),
            }
        )
        return next_state

    def _success_state(self, listener_state: dict[str, Any]) -> dict[str, Any]:
        next_state = dict(listener_state)
        next_state.update({"status": "ok", "last_success_at": _now().isoformat(), "failure_count": 0})
        next_state.pop("next_retry_at", None)
        next_state.pop("last_error", None)
        return next_state

    def _status_from_workflow_result(self, result: Any) -> str:
        if result is None:
            return "recorded"
        if not isinstance(result, dict):
            return "done"
        status = str(result.get("status") or "")
        if status == "dispatched" and isinstance(result.get("dispatch"), dict):
            nested = str(result["dispatch"].get("status") or "")
            if nested in {"done", "failed", "blocked"}:
                return nested
        if status == "created" and result.get("dry_run"):
            return "dry_run"
        if status in {"done", "dry_run", "created", "dispatched"}:
            return "done" if status != "dry_run" else "dry_run"
        if status in {"blocked", "failed", "invalid", "not_materialized"}:
            return status
        return "done"

    def _observe(self, spec: dict[str, Any], state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        kind = str(spec.get("type") or "").strip().lower()
        listener_state = dict(state.get("listeners", {}).get(str(spec["id"]), {}))
        if kind == "file":
            return self._observe_file(spec, listener_state)
        if kind == "http":
            return self._observe_http(spec, listener_state)
        if kind == "rss":
            return self._observe_rss(spec, listener_state)
        if kind == "schedule":
            return self._observe_schedule(spec, listener_state)
        if kind == "command":
            return self._observe_command(spec, listener_state)
        if kind == "github":
            return self._observe_github(spec, listener_state)
        raise ValueError(f"Unsupported listener type: {kind}")

    def _observe_file(self, spec: dict[str, Any], listener_state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        pattern = spec.get("glob") or spec.get("path")
        if not pattern:
            raise ValueError("file listener requires path or glob")
        paths = sorted(self.project_path.glob(str(pattern)))
        known = dict(listener_state.get("hashes") or {})
        next_hashes: dict[str, str] = {}
        observations = []
        for path in paths:
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.project_path))
            digest = _digest(path.read_bytes())
            old = known.get(rel)
            next_hashes[rel] = digest
            observations.append(
                {
                    "source": "file.changed" if old else "file.created",
                    "target": rel,
                    "changed": bool(old and old != digest) or (not old and bool(spec.get("emit_initial", False))),
                    "old_hash": old,
                    "new_hash": digest,
                }
            )
        return observations, {"hashes": next_hashes, "checked_at": _now().isoformat()}

    def _observe_http(self, spec: dict[str, Any], listener_state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        url = str(spec.get("url") or "").strip()
        if not url:
            raise ValueError("http listener requires url")
        request = urllib.request.Request(url, headers={"User-Agent": "krail-listener/1.0"})
        with urllib.request.urlopen(request, timeout=int(spec.get("timeout_seconds") or 20)) as response:
            body = response.read()
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
            status_code = getattr(response, "status", 200)
        detection = spec.get("change_detection") if isinstance(spec.get("change_detection"), dict) else {}
        normalize = str(detection.get("normalize") or detection.get("mode") or "raw")
        content = _normalize_http_body(body, mode=normalize)
        digest = _digest(content)
        old = listener_state.get("hash")
        changed = bool(old and old != digest) or (not old and bool(spec.get("emit_initial", False)))
        observation = {
            "source": "http.url.changed",
            "target": url,
            "changed": changed,
            "old_hash": old,
            "new_hash": digest,
            "status_code": status_code,
            "etag": etag,
            "last_modified": last_modified,
        }
        return [observation], {"hash": digest, "etag": etag, "last_modified": last_modified, "checked_at": _now().isoformat()}

    def _observe_rss(self, spec: dict[str, Any], listener_state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        url = str(spec.get("url") or "").strip()
        if not url:
            raise ValueError("rss listener requires url")
        with urllib.request.urlopen(url, timeout=int(spec.get("timeout_seconds") or 20)) as response:
            root = ET.fromstring(response.read())
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        known = set(listener_state.get("seen") or [])
        seen: list[str] = []
        observations = []
        for item in items[: int(spec.get("limit") or 25)]:
            guid = item.findtext("guid") or item.findtext("link") or item.findtext("title")
            if guid is None:
                guid = ET.tostring(item, encoding="unicode")
            guid_hash = _digest(guid)
            seen.append(guid_hash)
            observations.append(
                {
                    "source": "rss.item.added",
                    "target": guid,
                    "changed": (guid_hash not in known and bool(known)) or (guid_hash not in known and bool(spec.get("emit_initial", False))),
                    "new_hash": guid_hash,
                    "title": item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title"),
                    "link": item.findtext("link") or item.findtext("{http://www.w3.org/2005/Atom}link"),
                }
            )
        return observations, {"seen": list(dict.fromkeys([*seen, *list(known)]))[:500], "checked_at": _now().isoformat()}

    def _observe_schedule(self, spec: dict[str, Any], listener_state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        interval = _parse_interval(spec.get("interval"), default_seconds=3600)
        now = _now()
        last = listener_state.get("last_triggered_at")
        changed = True
        if last:
            try:
                previous = _dt.datetime.fromisoformat(str(last))
                changed = (now - previous).total_seconds() >= interval
            except Exception:
                changed = True
        observation = {"source": "schedule.due", "target": spec["id"], "changed": changed, "interval_seconds": interval, "new_hash": _digest(now.isoformat())}
        next_state = {"last_triggered_at": now.isoformat() if changed else last, "checked_at": now.isoformat()}
        return [observation], next_state

    def _observe_command(self, spec: dict[str, Any], listener_state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        command = spec.get("run")
        if not command:
            raise ValueError("command listener requires run")
        args = shlex.split(str(command)) if isinstance(command, str) else [str(part) for part in command]
        completed = subprocess.run(args, cwd=self.project_path, capture_output=True, text=True, timeout=int(spec.get("timeout_seconds") or 60))
        observations: list[dict[str, Any]] = []
        if completed.stdout.strip():
            payload = json.loads(completed.stdout)
            raw_items = payload if isinstance(payload, list) else payload.get("events", [payload]) if isinstance(payload, dict) else []
            for item in raw_items:
                if isinstance(item, dict):
                    observations.append(
                        {
                            "source": item.get("source") or "command.event",
                            "target": item.get("target") or spec["id"],
                            "changed": bool(item.get("changed", True)),
                            "new_hash": item.get("hash") or _digest(json.dumps(item, sort_keys=True)),
                            "payload": item,
                        }
                    )
        return observations, {"last_exit_code": completed.returncode, "checked_at": _now().isoformat()}

    def _observe_github(self, spec: dict[str, Any], listener_state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        repo = str(spec.get("repo") or "").strip()
        if not repo:
            raise ValueError("github listener requires repo")
        requested = set(spec.get("events") or ["issues.opened"])
        seen = set(listener_state.get("seen") or [])
        next_seen: list[str] = []
        observations: list[dict[str, Any]] = []

        def gh_api(endpoint: str) -> Any:
            completed = subprocess.run(["gh", "api", endpoint], cwd=self.project_path, capture_output=True, text=True, timeout=int(spec.get("timeout_seconds") or 60))
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or f"gh api failed: {endpoint}")
            return json.loads(completed.stdout or "null")

        if "issues.opened" in requested:
            issues = gh_api(f"repos/{repo}/issues?state=open&per_page=50")
            for issue in issues if isinstance(issues, list) else []:
                if "pull_request" in issue:
                    continue
                key = f"issue:{issue.get('id') or issue.get('number')}"
                digest = _digest(key)
                next_seen.append(digest)
                observations.append({"source": "github.issue.opened", "target": str(issue.get("html_url") or key), "changed": digest not in seen and (bool(seen) or bool(spec.get("emit_initial", False))), "new_hash": digest, "payload": issue})
        if "pull_request.opened" in requested or "pulls.opened" in requested:
            pulls = gh_api(f"repos/{repo}/pulls?state=open&per_page=50")
            for pull in pulls if isinstance(pulls, list) else []:
                key = f"pull:{pull.get('id') or pull.get('number')}"
                digest = _digest(key)
                next_seen.append(digest)
                observations.append({"source": "github.pull_request.opened", "target": str(pull.get("html_url") or key), "changed": digest not in seen and (bool(seen) or bool(spec.get("emit_initial", False))), "new_hash": digest, "payload": pull})
        if "check_suite.completed" in requested:
            ref = str(spec.get("ref") or "HEAD")
            suites_payload = gh_api(f"repos/{repo}/commits/{ref}/check-suites?per_page=50")
            suites = suites_payload.get("check_suites") if isinstance(suites_payload, dict) else []
            for suite in suites if isinstance(suites, list) else []:
                if suite.get("status") != "completed":
                    continue
                key = f"check-suite:{suite.get('id')}"
                digest = _digest(f"{key}:{suite.get('conclusion')}")
                next_seen.append(digest)
                observations.append({"source": "github.check_suite.completed", "target": str(suite.get("html_url") or key), "changed": digest not in seen and (bool(seen) or bool(spec.get("emit_initial", False))), "new_hash": digest, "payload": suite})
        return observations, {"seen": list(dict.fromkeys([*next_seen, *list(seen)]))[:1000], "checked_at": _now().isoformat()}

    def _event_from_observation(self, spec: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        occurred_at = _now()
        target = str(observation.get("target") or spec["id"])
        new_hash = str(observation.get("new_hash") or _digest(json.dumps(observation, sort_keys=True, default=str)))
        source = str(observation.get("source") or f"{spec.get('type')}.changed")
        dedupe_key = f"{source}:{spec['id']}:{target}:{new_hash}"
        event_id = f"evt_{occurred_at.strftime('%Y%m%d%H%M%S')}_{hashlib.sha1(dedupe_key.encode('utf-8')).hexdigest()[:10]}"
        trigger = self._trigger(spec)
        return {
            "id": event_id,
            "source": source,
            "listener_id": spec["id"],
            "occurred_at": occurred_at.isoformat(),
            "dedupe_key": dedupe_key,
            "payload": observation,
            "trigger": trigger,
            "status": "pending",
        }

    def _workflow_inputs_for_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return {"event_id": event["id"], "event": event}

    def _events_log_path(self) -> Path:
        return self.events_dir / "events.jsonl"

    def _append_event(self, event: dict[str, Any]) -> None:
        self.events_dir.mkdir(parents=True, exist_ok=True)
        day_path = self.events_dir / f"{event['id']}.json"
        day_path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self._events_log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _append_event_update(self, event: dict[str, Any]) -> None:
        path = self.events_dir / f"{event['id']}.json"
        path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self._events_log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event_id": event["id"], "updated_at": _now().isoformat(), "status": event.get("status"), "workflow_result": event.get("workflow_result")}, sort_keys=True) + "\n")

    def _invoke_trigger(self, spec: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
        trigger = self._trigger(spec)
        workflow = trigger.get("workflow")
        if not workflow:
            return None
        dry_run = bool(trigger.get("dry_run_first", False) or trigger.get("dry_run", False))
        mode = str(trigger.get("mode") or "execute")
        event["triggered_workflow"] = workflow
        event["event_path"] = str((self.events_dir / f"{event['id']}.json").relative_to(self.project_path))
        if mode == "run":
            return self.runtime.workflow_run(str(workflow), runner=str(trigger.get("runner") or "auto"), dry_run=dry_run)
        return self.runtime.workflow_execute(str(workflow), dry_run=dry_run, inputs=self._workflow_inputs_for_event(event))

    def list_events(self, *, limit: int = 20, listener_id: str | None = None) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        if self.events_dir.is_dir():
            for path in sorted(self.events_dir.glob("evt_*.json"), reverse=True):
                try:
                    event = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if listener_id and event.get("listener_id") != listener_id:
                    continue
                events.append(event)
                if len(events) >= limit:
                    break
        return {"events": events}

    def show_event(self, event_id: str) -> dict[str, Any]:
        path = self.events_dir / f"{event_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Event not found: {event_id}")
        return {"event": json.loads(path.read_text(encoding="utf-8"))}

    def replay_event(self, event_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        event = self.show_event(event_id)["event"]
        trigger = event.get("trigger") if isinstance(event.get("trigger"), dict) else {}
        workflow = trigger.get("workflow") or event.get("triggered_workflow")
        if not workflow:
            raise ValueError(f"Event has no workflow trigger: {event_id}")
        result = self.runtime.workflow_execute(str(workflow), dry_run=dry_run, inputs=self._workflow_inputs_for_event(event))
        replay = {"event_id": event_id, "workflow": workflow, "dry_run": dry_run, "result": result, "replayed_at": _now().isoformat()}
        with self._events_log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event_id": event_id, "replay": replay}, sort_keys=True) + "\n")
        return replay

    def daemon(self, *, once: bool = False, interval_seconds: int = 30) -> dict[str, Any]:
        if once:
            return self.poll()
        while True:
            self.poll()
            time.sleep(max(1, interval_seconds))

    def doctor(self) -> dict[str, Any]:
        state = self._load_state()
        validation = self.validate_spec()
        events = self.list_events(limit=500)["events"]
        warnings: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        now = _now()
        specs = [self._load_spec(path) for path in self._listener_files()]
        for spec in specs:
            listener_id = str(spec.get("id"))
            listener_state = state.get("listeners", {}).get(listener_id, {})
            if spec.get("enabled", True) is False:
                continue
            if listener_state.get("status") == "error":
                errors.append({"listener": listener_id, "issue": "failing", "detail": listener_state.get("last_error")})
            last_success = listener_state.get("last_success_at")
            if last_success:
                try:
                    seconds_since = (now - _dt.datetime.fromisoformat(str(last_success))).total_seconds()
                    stale_after = _parse_interval(spec.get("stale_after") or spec.get("interval"), default_seconds=86400) * 2
                    if seconds_since > stale_after:
                        warnings.append({"listener": listener_id, "issue": "not_run_recently", "seconds_since_success": int(seconds_since)})
                except Exception:
                    warnings.append({"listener": listener_id, "issue": "invalid_last_success_at", "value": last_success})
            else:
                warnings.append({"listener": listener_id, "issue": "never_succeeded"})
            workflow = self._trigger(spec).get("workflow")
            if workflow:
                try:
                    self.runtime.workflow_show(str(workflow))
                except Exception as exc:
                    errors.append({"listener": listener_id, "issue": "missing_workflow", "workflow": workflow, "detail": str(exc)})
        unhandled = [event for event in events if event.get("status") in {"pending", "recorded", "dispatched"}]
        if unhandled:
            warnings.append({"issue": "unhandled_events", "count": len(unhandled)})
        log_path = self._events_log_path()
        if log_path.exists() and log_path.stat().st_size > 5_000_000:
            warnings.append({"issue": "large_event_log", "path": str(log_path.relative_to(self.project_path)), "bytes": log_path.stat().st_size})
        stale_locks = [str(path.relative_to(self.project_path)) for path in sorted(self.locks_dir.glob("listener-*.lock"))]
        if stale_locks:
            warnings.append({"issue": "listener_locks_present", "locks": stale_locks})
        return {
            "ok": validation["ok"] and not errors,
            "listeners": len(specs),
            "enabled": len([spec for spec in specs if spec.get("enabled", True) is not False]),
            "validation": validation,
            "errors": errors,
            "warnings": warnings,
            "recent_events": len(events),
        }

    def serve(self, *, host: str = "127.0.0.1", port: int = 8787) -> dict[str, Any]:
        engine = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(payload, dict):
                        raise ValueError("payload must be a JSON object")
                    observation = {
                        "source": payload.get("source") or "webhook.event",
                        "target": payload.get("target") or self.path,
                        "changed": payload.get("changed", True),
                        "new_hash": payload.get("hash") or _digest(json.dumps(payload, sort_keys=True)),
                        "payload": payload,
                    }
                    spec = {"id": payload.get("listener_id") or "webhook", "type": "webhook", "on_event": payload.get("on_event") or {}}
                    event = engine._event_from_observation(spec, observation)
                    event["status"] = "recorded"
                    engine._append_event(event)
                    self.send_response(202)
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "recorded", "event_id": event["id"]}).encode("utf-8"))
                except Exception as exc:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "error": str(exc)}).encode("utf-8"))

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        server = ThreadingHTTPServer((host, port), Handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()


def _safe_id(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:80] or "listener"
