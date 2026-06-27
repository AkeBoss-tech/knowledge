from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PUBLIC_VISIBILITIES = {"", "public", "project"}
RESTRICTED_VISIBILITIES = {"private", "team", "restricted"}


@dataclass(frozen=True)
class PermissionActor:
    """Local actor identity for policy checks.

    KRAIL stays public-by-default for backward compatibility. Actors only matter
    when records opt into restrictive metadata.
    """

    id: str = "local:user"
    type: str = "user"
    roles: tuple[str, ...] = field(default_factory=tuple)
    agent: str | None = None

    @classmethod
    def from_env(cls) -> "PermissionActor":
        actor_id = os.environ.get("KRAIL_ACTOR") or os.environ.get("USER") or "local:user"
        roles = tuple(item.strip() for item in os.environ.get("KRAIL_ROLES", "").split(",") if item.strip())
        agent = os.environ.get("KRAIL_AGENT") or None
        actor_type = "agent" if agent else "user"
        return cls(id=actor_id, type=actor_type, roles=roles, agent=agent)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "roles": list(self.roles), "agent": self.agent}


class PermissionPolicy:
    def __init__(self, project_path: str | Path, actor: PermissionActor | None = None):
        self.project_path = Path(project_path)
        self.actor = actor or PermissionActor.from_env()

    def metadata_for_path(self, rel_path: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        global_rules = self._global_rules()
        for rule in global_rules:
            pattern = str(rule.get("path") or rule.get("pattern") or "")
            if pattern and fnmatch.fnmatch(rel_path, pattern):
                merged.update({key: value for key, value in rule.items() if key not in {"path", "pattern"}})
        if metadata:
            merged.update(metadata)
        return merged

    def can_read(self, target: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        metadata = metadata or {}
        visibility = str(metadata.get("visibility") or "public").lower()
        constrained = any(metadata.get(key) for key in ("allowed_roles", "allowed_agents", "allowed_users", "allowed_actors"))
        if constrained:
            if self._actor_allowed(metadata):
                return True, "actor_allowed"
            return False, "allowlist_not_matched"
        if visibility in PUBLIC_VISIBILITIES:
            return True, "public_default"
        if visibility not in RESTRICTED_VISIBILITIES:
            return True, "unknown_visibility_public_default"
        if self._actor_allowed(metadata):
            return True, "actor_allowed"
        return False, f"visibility:{visibility}"

    def can_write(self, target: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        metadata = metadata or {}
        write_paths = _as_str_list(metadata.get("write") or metadata.get("allowed_write_paths"))
        if write_paths and not any(fnmatch.fnmatch(target, pattern) for pattern in write_paths):
            return False, "write_path_not_allowed"
        return self.can_read(target, metadata)

    def can_execute(self, workflow_id: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        metadata = metadata or {}
        allowed_agents = _as_str_list(metadata.get("agents") or metadata.get("allowed_agents"))
        if allowed_agents and self.actor.agent not in allowed_agents:
            return False, "agent_not_allowed"
        return self.can_read(workflow_id, metadata)

    def filter_readable(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        readable: list[dict[str, Any]] = []
        for result in results:
            target = str(result.get("path") or result.get("id") or result.get("title") or "record")
            metadata = self._metadata_from_record(result)
            allowed, reason = self.can_read(target, metadata)
            if allowed:
                if metadata.get("sensitivity"):
                    self.audit("read", target, "allowed", reason, metadata=metadata)
                readable.append(result)
            else:
                self.audit("read", target, "denied", reason, metadata=metadata)
        return readable

    def doctor(self) -> dict[str, Any]:
        unlabeled_sensitive: list[str] = []
        restricted_records: list[str] = []
        for path in sorted((self.project_path / "topics").rglob("*.md")) if (self.project_path / "topics").exists() else []:
            rel = path.relative_to(self.project_path).as_posix()
            metadata = self.metadata_for_path(rel, _frontmatter(path))
            if metadata.get("sensitivity") and not metadata.get("visibility"):
                unlabeled_sensitive.append(rel)
            if str(metadata.get("visibility") or "public").lower() in RESTRICTED_VISIBILITIES:
                restricted_records.append(rel)
        return {
            "ok": not unlabeled_sensitive,
            "actor": self.actor.to_dict(),
            "public_by_default": True,
            "restricted_records": restricted_records,
            "unlabeled_sensitive": unlabeled_sensitive,
            "audit_log": "research_plan/audit/access.jsonl",
        }

    def audit(self, action: str, target: str, decision: str, reason: str, *, metadata: dict[str, Any] | None = None) -> None:
        audit_dir = self.project_path / "research_plan" / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "actor": self.actor.to_dict(),
            "action": action,
            "target": target,
            "decision": decision,
            "reason": reason,
        }
        if metadata and metadata.get("sensitivity"):
            record["sensitivity"] = _as_str_list(metadata.get("sensitivity"))
        with (audit_dir / "access.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _global_rules(self) -> list[dict[str, Any]]:
        manifest_path = self.project_path / "rail.yaml"
        if not manifest_path.exists():
            return []
        try:
            import yaml

            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return []
        permissions = manifest.get("permissions") if isinstance(manifest, dict) else None
        rules = permissions.get("rules") if isinstance(permissions, dict) else None
        return [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []

    def _actor_allowed(self, metadata: dict[str, Any]) -> bool:
        owners = _as_str_list(metadata.get("owner") or metadata.get("owners"))
        if owners and self.actor.id in owners:
            return True
        allowed_agents = _as_str_list(metadata.get("allowed_agents"))
        if allowed_agents and self.actor.agent in allowed_agents:
            return True
        allowed_roles = _as_str_list(metadata.get("allowed_roles"))
        if allowed_roles and set(self.actor.roles).intersection(allowed_roles):
            return True
        allowed_users = _as_str_list(metadata.get("allowed_users") or metadata.get("allowed_actors"))
        if allowed_users and self.actor.id in allowed_users:
            return True
        return False

    @staticmethod
    def _metadata_from_record(record: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        source = record.get("record") if isinstance(record.get("record"), dict) else record
        for key in ("visibility", "owner", "owners", "allowed_roles", "allowed_agents", "allowed_users", "allowed_actors", "sensitivity"):
            if isinstance(source, dict) and key in source:
                metadata[key] = source[key]
            elif key in record:
                metadata[key] = record[key]
        return metadata


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    try:
        import yaml

        loaded = yaml.safe_load(text[4:end]) or {}
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}
