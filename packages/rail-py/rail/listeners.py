from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import shlex
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml


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


class ListenerEngine:
    """Poll local listener specs and turn observations into replayable events."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.project_path = Path(runtime.project_path)
        self.krail_dir = Path(runtime.krail_dir)
        self.listeners_dir = self.project_path / "research_plan" / "listeners"
        self.events_dir = self.project_path / "research_plan" / "events"
        self.state_path = self.krail_dir / "listener_state.json"

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
            if spec.get("enabled", True) is False:
                results.append({"listener": spec["id"], "status": "disabled", "events": []})
                continue
            observations, next_state = self._observe(spec, state)
            events = [self._event_from_observation(spec, observation) for observation in observations if observation.get("changed")]
            emitted = []
            for event in events:
                dedupe_key = str(event["dedupe_key"])
                if state.get("dedupe", {}).get(dedupe_key):
                    continue
                if dry_run:
                    event["status"] = "dry_run"
                else:
                    self._append_event(event)
                    state.setdefault("dedupe", {})[dedupe_key] = event["id"]
                    event["status"] = "recorded"
                    if execute:
                        event["workflow_result"] = self._invoke_trigger(spec, event)
                        self._append_event_update(event)
                emitted.append(event)
            if not dry_run:
                state.setdefault("listeners", {})[str(spec["id"])] = next_state
                self._write_state(state)
            results.append({"listener": spec["id"], "status": "ok", "events": emitted, "observations": observations})
        return {"status": "ok", "dry_run": dry_run, "results": results}

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
        return self.runtime.workflow_execute(str(workflow), dry_run=dry_run)

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
        result = self.runtime.workflow_execute(str(workflow), dry_run=dry_run)
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
