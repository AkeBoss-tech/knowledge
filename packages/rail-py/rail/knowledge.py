from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rail.markdown_graph import (
    build_markdown_graph,
    export_graph,
    filter_documents,
    filter_edges,
    filter_entities,
    load_or_build_graph,
)


DEFAULT_PACKS: dict[str, dict[str, Any]] = {
    "research-intelligence": {
        "id": "research-intelligence",
        "name": "Research Intelligence",
        "entities": [
            "Paper",
            "Method",
            "Package",
            "Dataset",
            "Benchmark",
            "Claim",
            "Limitation",
            "OpenProblem",
            "Experiment",
        ],
        "link_types": [
            "Paper INTRODUCES Method",
            "Package IMPLEMENTS Method",
            "Paper EVALUATES_ON Benchmark",
            "Claim SUPPORTED_BY EvidenceChunk",
            "Experiment TESTS Claim",
        ],
        "workflows": [
            "add_new_paper",
            "weekly_literature_refresh",
            "register_experiment",
            "build_sota_report",
        ],
    },
    "company-brain": {
        "id": "company-brain",
        "name": "Company Brain",
        "entities": ["Person", "Team", "Role", "System", "Workflow", "Policy", "Dataset", "Metric", "Permission", "Claim"],
        "link_types": [
            "Person BELONGS_TO Team",
            "Team OWNS System",
            "Workflow USES System",
            "Policy GOVERNS Workflow",
            "System STORES Dataset",
        ],
        "workflows": ["initial_company_map", "daily_refresh", "weekly_exec_brief", "stale_doc_review"],
    },
    "software-architecture": {
        "id": "software-architecture",
        "name": "Software Architecture",
        "entities": ["Service", "Module", "API", "Database", "Queue", "Dependency", "Decision", "Risk", "Claim"],
        "link_types": [
            "Service EXPOSES API",
            "Module DEPENDS_ON Module",
            "Service WRITES Database",
            "Decision AFFECTS Service",
        ],
        "workflows": ["map_codebase", "capture_architecture_decision", "dependency_review"],
    },
    "policy-compiler": {
        "id": "policy-compiler",
        "name": "Policy Compiler",
        "entities": ["Policy", "Control", "Requirement", "Exception", "Owner", "System", "Dataset", "Claim"],
        "link_types": [
            "Policy REQUIRES Control",
            "Control APPLIES_TO System",
            "Exception WAIVES Requirement",
            "Claim SUPPORTED_BY EvidenceChunk",
        ],
        "workflows": ["compile_policy", "review_exceptions", "evidence_gap_scan"],
    },
}

LOCAL_RUNNERS: dict[str, dict[str, str]] = {
    "codex_cli": {
        "command_env": "CODEX_CLI_COMMAND",
        "default_command": "codex",
        "description": "Codex CLI local runner",
    },
    "claude_code": {
        "command_env": "CLAUDE_CODE_COMMAND",
        "default_command": "claude",
        "description": "Claude Code local runner",
    },
    "gemini_cli": {
        "command_env": "GEMINI_CLI_COMMAND",
        "default_command": "gemini",
        "description": "Gemini CLI local runner",
    },
    "cursor_cli": {
        "command_env": "CURSOR_CLI_COMMAND",
        "default_command": "agent",
        "description": "Cursor CLI local runner",
    },
    "copilot_cli": {
        "command_env": "COPILOT_CLI_COMMAND",
        "default_command": "gh copilot suggest",
        "description": "GitHub Copilot CLI helper",
    },
}


_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_WIKILINK_RE = re.compile(r"\[\[([A-Za-z0-9_.-]+):([^\]]+)\]\]")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "about",
    "as",
    "for",
    "from",
    "how",
    "is",
    "it",
    "known",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "who",
    "why",
}


@dataclass
class SearchHit:
    path: str
    title: str
    score: float
    matched_terms: list[str]
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "score": round(self.score, 3),
            "matched_terms": self.matched_terms,
            "snippet": self.snippet,
        }


class KnowledgeRuntime:
    """Small local-first knowledge UX layer over a project repository."""

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path).resolve()

    @property
    def krail_dir(self) -> Path:
        return self.project_path / ".krail"

    @property
    def active_pack_path(self) -> Path:
        return self.krail_dir / "pack.yaml"

    def _iter_docs(self) -> list[Path]:
        ignored_parts = {".git", ".krail", ".rail", "__pycache__", ".pytest_cache", ".venv"}
        suffixes = {".md", ".txt", ".yaml", ".yml", ".json", ".csv"}
        docs: list[Path] = []
        for path in self.project_path.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_parts for part in path.parts):
                continue
            if path.suffix.lower() in suffixes:
                docs.append(path)
        return sorted(docs)

    @staticmethod
    def _terms(query: str) -> list[str]:
        return [term.lower() for term in _WORD_RE.findall(query) if len(term) > 1 and term.lower() not in _STOPWORDS]

    @staticmethod
    def _title_for(path: Path, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or path.stem
        return path.stem.replace("-", " ").replace("_", " ").strip() or path.name

    @staticmethod
    def _snippet(text: str, terms: list[str]) -> str:
        normalized = text.replace("\n", " ")
        lower = normalized.lower()
        positions = [lower.find(term) for term in terms if term in lower]
        if not positions:
            return normalized[:220].strip()
        start = max(min(pos for pos in positions if pos >= 0) - 80, 0)
        return normalized[start : start + 260].strip()

    def search(self, query: str, *, limit: int = 10, explain: bool = False) -> dict[str, Any]:
        terms = self._terms(query)
        hits: list[SearchHit] = []
        if not terms:
            return {"query": query, "hits": [], "explain": "No searchable terms found." if explain else None}

        for path in self._iter_docs():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            lower = text.lower()
            title = self._title_for(path, text)
            rel = str(path.relative_to(self.project_path))
            title_lower = title.lower()
            matched = sorted({term for term in terms if term in lower or term in title_lower})
            if not matched:
                continue
            exact_hits = sum(lower.count(term) for term in matched)
            title_boost = sum(2 for term in matched if term in title_lower)
            path_boost = sum(1 for term in matched if term in rel.lower())
            wikilink_boost = len(_WIKILINK_RE.findall(text)) * 0.05
            score = exact_hits + title_boost + path_boost + wikilink_boost
            hits.append(SearchHit(rel, title, score, matched, self._snippet(text, terms)))

        hits.sort(key=lambda hit: (-hit.score, hit.path))
        result: dict[str, Any] = {"query": query, "hits": [hit.to_dict() for hit in hits[:limit]]}
        if explain:
            result["explain"] = {
                "mode": "local_keyword",
                "signals": ["term_frequency", "title_match", "path_match", "typed_wikilink_count"],
                "note": "Vector, graph, freshness, and integrity boosts are planned but not wired into this local search yet.",
            }
        return result

    def think(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        search = self.search(query, limit=limit, explain=True)
        hits = search["hits"]
        evidence = [
            {"path": hit["path"], "title": hit["title"], "snippet": hit["snippet"], "score": hit["score"]}
            for hit in hits
        ]
        if hits:
            answer = (
                "The local knowledge repo has relevant material, but this phase-1 thinker "
                "does not yet call an LLM. Review the evidence below for the strongest matches."
            )
            confidence = "low"
        else:
            answer = "No matching local evidence was found for this question."
            confidence = "low"
        return {
            "query": query,
            "answer": answer,
            "evidence": evidence,
            "confidence": confidence,
            "gaps": [
                "No vector index or reranker is wired in yet.",
                "No source freshness or claim-evidence scoring is applied yet.",
                "LLM synthesis is intentionally not faked in this local skeleton.",
            ],
            "conflicts": [],
            "suggested_next_actions": [
                "Run `krail capture` to add missing notes or sources.",
                "Run `krail doctor` to check the project structure.",
                "Register important claims in the integrity ledger before promotion.",
            ],
        }

    def capture(
        self,
        *,
        text: str = "",
        file_path: str | None = None,
        url: str | None = None,
        kind: str = "note",
        workflow: str | None = None,
    ) -> dict[str, Any]:
        content_parts: list[str] = []
        if text:
            content_parts.append(text.strip())
        if file_path:
            source = Path(file_path).expanduser().resolve()
            content_parts.append(source.read_text(encoding="utf-8"))
        if url:
            content_parts.append(f"Source URL: {url}")
        if not content_parts:
            raise ValueError("capture requires text, --file, --url, or stdin")

        body = "\n\n".join(part for part in content_parts if part)
        today = _dt.date.today().isoformat()
        digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
        path = self.project_path / "topics" / "inbox" / f"{today}-{digest}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = [
            "---",
            f"type: {kind}",
            f"captured_at: {_dt.datetime.now(_dt.UTC).isoformat()}",
        ]
        if url:
            header.append(f"url: {url}")
        if workflow:
            header.append(f"workflow: {workflow}")
        header.extend(["---", ""])
        path.write_text("\n".join(header) + body.strip() + "\n", encoding="utf-8")
        return {"status": "captured", "path": str(path.relative_to(self.project_path)), "type": kind}

    def list_packs(self) -> dict[str, Any]:
        return {"packs": list(DEFAULT_PACKS.values())}

    def active_pack(self) -> dict[str, Any]:
        if self.active_pack_path.exists():
            data = yaml.safe_load(self.active_pack_path.read_text(encoding="utf-8")) or {}
            return {"active": data}
        return {"active": None}

    def show_pack(self, pack_id: str) -> dict[str, Any]:
        pack = DEFAULT_PACKS.get(pack_id)
        if not pack:
            raise ValueError(f"Unknown pack: {pack_id}")
        return pack

    def use_pack(self, pack_id: str) -> dict[str, Any]:
        pack = self.show_pack(pack_id)
        self.active_pack_path.parent.mkdir(parents=True, exist_ok=True)
        self.active_pack_path.write_text(yaml.safe_dump(pack, sort_keys=False), encoding="utf-8")
        return {"status": "activated", "path": str(self.active_pack_path.relative_to(self.project_path)), "pack": pack}

    def validate_pack(self, pack_id: str | None = None) -> dict[str, Any]:
        pack = self.show_pack(pack_id) if pack_id else (self.active_pack().get("active") or {})
        required = ["id", "entities", "link_types", "workflows"]
        errors = [f"missing {key}" for key in required if not pack.get(key)]
        return {"valid": not errors, "errors": errors, "pack": pack.get("id")}

    def suggest_pack(self) -> dict[str, Any]:
        docs = " ".join(path.name.lower() for path in self._iter_docs())
        scores = {pack_id: 0 for pack_id in DEFAULT_PACKS}
        if any(word in docs for word in ["paper", "experiment", "benchmark", "arxiv"]):
            scores["research-intelligence"] += 3
        if any(word in docs for word in ["team", "policy", "workflow", "onboarding"]):
            scores["company-brain"] += 3
        if any(word in docs for word in ["service", "api", "module", "architecture"]):
            scores["software-architecture"] += 3
        if any(word in docs for word in ["policy", "control", "requirement"]):
            scores["policy-compiler"] += 3
        best = max(scores, key=scores.get)
        return {"suggested": DEFAULT_PACKS[best], "scores": scores}

    def detect_pack(self) -> dict[str, Any]:
        active = self.active_pack().get("active")
        return {"active": active, "suggestion": self.suggest_pack()["suggested"]}

    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        def warn(name: str, ok: bool, detail: str) -> None:
            if not ok:
                warnings.append({"name": name, "detail": detail})

        manifest_path = self.project_path / "rail.yaml"
        check("manifest", manifest_path.exists(), "rail.yaml exists" if manifest_path.exists() else "rail.yaml missing")
        for rel in [".ontology", "topics", "research_plan", "agents", "skills", "specs", "artifacts"]:
            path = self.project_path / rel
            check(f"path:{rel}", path.exists(), f"{rel} exists" if path.exists() else f"{rel} missing")
        pack_state = self.active_pack().get("active")
        check("pack", bool(pack_state), f"active pack: {pack_state.get('id')}" if pack_state else "no active .krail/pack.yaml")
        inbox = self.project_path / "topics" / "inbox"
        check("capture_inbox", inbox.exists(), "topics/inbox exists" if inbox.exists() else "topics/inbox will be created on first capture")
        warn(
            "brief",
            (self.project_path / "topics" / "brief.md").exists(),
            "topics/brief.md is missing; pilot agents work better with a short project brief.",
        )
        warn(
            "research_spec",
            (self.project_path / "specs" / "research_question.yaml").exists(),
            "specs/research_question.yaml is missing; add one for research pilots.",
        )
        warn(
            "current_plan",
            (self.project_path / "research_plan" / "current_plan.md").exists(),
            "research_plan/current_plan.md is missing; workflows need a durable plan anchor.",
        )
        transient_dirs = [
            ".rail/workspaces",
            "research_plan/sessions",
            "research_plan/audits",
            "research_plan/stuck_reports",
        ]
        present_transient = [rel for rel in transient_dirs if (self.project_path / rel).exists()]
        warn(
            "transient_runtime_state",
            not present_transient,
            "transient runtime directories exist and should usually stay uncommitted: " + ", ".join(present_transient),
        )
        if manifest_path.exists():
            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                warn(
                    "frontend_manifest_section",
                    "frontend" not in manifest,
                    "rail.yaml still has a frontend section; KRAIL projects should be headless.",
                )
                graph = manifest.get("graph") if isinstance(manifest.get("graph"), dict) else {}
                if graph:
                    warn(
                        "graph_mode",
                        graph.get("mode") in {"markdown_frontmatter", "markdown_graph", None},
                        "graph.mode should be markdown_frontmatter or markdown_graph for local KRAIL projects.",
                    )
            except Exception as exc:
                warn("manifest_parse", False, f"could not parse rail.yaml for advisory checks: {exc}")
        ok = all(item["ok"] for item in checks if not item["name"].startswith("capture_inbox"))
        return {"ok": ok, "checks": checks, "warnings": warnings}

    def graph_build(self, *, write: bool = True) -> dict[str, Any]:
        return build_markdown_graph(self.project_path, write=write)

    def graph_entities(self, *, entity_type: str | None = None, limit: int = 100) -> dict[str, Any]:
        graph = load_or_build_graph(self.project_path)
        return filter_entities(graph, entity_type=entity_type, limit=limit)

    def graph_edges(
        self,
        *,
        entity: str | None = None,
        relation_type: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        graph = load_or_build_graph(self.project_path)
        return filter_edges(graph, entity=entity, relation_type=relation_type, limit=limit)

    def graph_docs(
        self,
        *,
        topic: str | None = None,
        kind: str | None = None,
        source: str | None = None,
        entity: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        graph = load_or_build_graph(self.project_path)
        return filter_documents(graph, topic=topic, kind=kind, source=source, entity=entity, limit=limit)

    def graph_export(self, *, export_format: str = "json") -> dict[str, Any]:
        graph = load_or_build_graph(self.project_path)
        return {"format": export_format, "content": export_graph(graph, export_format)}

    @property
    def tasks_dir(self) -> Path:
        return self.project_path / "research_plan" / "tasks"

    @property
    def work_orders_dir(self) -> Path:
        return self.project_path / "research_plan" / "work_orders"

    @property
    def sessions_dir(self) -> Path:
        return self.project_path / "research_plan" / "sessions"

    def list_agents(self) -> dict[str, Any]:
        agents = []
        for name, meta in LOCAL_RUNNERS.items():
            command = os.environ.get(meta["command_env"], meta["default_command"])
            executable = shlex.split(command)[0] if command else ""
            agents.append(
                {
                    "name": name,
                    "description": meta["description"],
                    "command": command,
                    "available": bool(executable and shutil_which(executable)),
                }
            )
        return {"agents": agents, "default": "codex_cli"}

    @staticmethod
    def _slug(value: str, *, fallback: str = "task") -> str:
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug[:80] or fallback

    def create_task(
        self,
        title: str,
        *,
        description: str = "",
        runner: str = "codex_cli",
        workflow: str | None = None,
        role: str = "research",
    ) -> dict[str, Any]:
        now = _dt.datetime.now(_dt.UTC)
        digest = hashlib.sha1(f"{title}:{description}:{now.isoformat()}".encode("utf-8")).hexdigest()[:8]
        task_id = f"task_{self._slug(title)}_{digest}"
        payload = {
            "id": task_id,
            "title": title,
            "description": description or title,
            "status": "ready",
            "runner": runner,
            "role": role,
            "workflow": workflow,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        path = self.tasks_dir / f"{task_id}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return {"status": "created", "task": payload, "path": str(path.relative_to(self.project_path))}

    def list_tasks(self) -> dict[str, Any]:
        tasks = []
        for path in sorted(self.tasks_dir.glob("*.json")):
            try:
                tasks.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {"tasks": tasks}

    def _task_path(self, task_id: str) -> Path:
        path = self.tasks_dir / f"{task_id}.json"
        if path.exists():
            return path
        matches = list(self.tasks_dir.glob(f"{task_id}*.json"))
        if len(matches) == 1:
            return matches[0]
        raise FileNotFoundError(f"Task not found: {task_id}")

    def _load_task(self, task_id: str) -> tuple[Path, dict[str, Any]]:
        path = self._task_path(task_id)
        return path, json.loads(path.read_text(encoding="utf-8"))

    def _write_task(self, path: Path, task: dict[str, Any]) -> None:
        task["updated_at"] = _dt.datetime.now(_dt.UTC).isoformat()
        path.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")

    def _work_order_for_task(self, task: dict[str, Any]) -> dict[str, Any]:
        wo_id = f"wo_{task['id']}"
        return {
            "work_order_id": wo_id,
            "task_id": task["id"],
            "title": task["title"],
            "description": task.get("description") or task["title"],
            "runner": task.get("runner") or "codex_cli",
            "role": task.get("role") or "research",
            "workflow": task.get("workflow"),
            "allowed_paths": ["topics", "research_plan", "artifacts", "agents", "skills", "specs"],
            "outputs_required": ["summary", "changed_files", "blockers_or_gaps"],
            "trust": "candidate_until_reviewed",
            "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
        }

    def create_work_order(self, task_id: str) -> dict[str, Any]:
        _, task = self._load_task(task_id)
        work_order = self._work_order_for_task(task)
        self.work_orders_dir.mkdir(parents=True, exist_ok=True)
        path = self.work_orders_dir / f"{work_order['work_order_id']}.json"
        path.write_text(json.dumps(work_order, indent=2) + "\n", encoding="utf-8")
        return {"status": "created", "work_order": work_order, "path": str(path.relative_to(self.project_path))}

    def _runner_command(self, runner: str, prompt: str) -> list[str]:
        if runner not in LOCAL_RUNNERS:
            raise ValueError(f"Unknown runner: {runner}")
        meta = LOCAL_RUNNERS[runner]
        base = os.environ.get(meta["command_env"], meta["default_command"])
        parts = shlex.split(base)
        if runner == "codex_cli":
            return [*parts, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", prompt]
        if runner == "claude_code":
            return [*parts, "--print", "--permission-mode", "bypassPermissions", prompt]
        if runner == "gemini_cli":
            return [*parts, "-p", prompt]
        if runner == "cursor_cli":
            return [*parts, prompt]
        if runner == "copilot_cli":
            return [*parts, prompt]
        return [*parts, prompt]

    def _prompt_for_work_order(self, work_order: dict[str, Any]) -> str:
        return (
            "You are a local KRAIL workflow worker.\n\n"
            f"Project root: {self.project_path}\n"
            f"Work order: {work_order['work_order_id']}\n"
            f"Task: {work_order['title']}\n\n"
            f"{work_order['description']}\n\n"
            "Rules:\n"
            "- Work only inside this project repository.\n"
            "- Prefer evidence files, captures, and integrity records over unsupported claims.\n"
            "- Write useful outputs under topics/, research_plan/, or artifacts/.\n"
            "- End with a concise summary, changed files, gaps, and suggested next actions.\n"
            "- Do not promote generated claims as verified without evidence.\n"
        )

    def dispatch_task(self, task_id: str, *, runner: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        task_path, task = self._load_task(task_id)
        if runner:
            task["runner"] = runner
        work_order_result = self.create_work_order(task["id"])
        work_order = work_order_result["work_order"]
        prompt = self._prompt_for_work_order(work_order)
        command = self._runner_command(work_order["runner"], prompt)
        session_id = f"session_{task['id']}_{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M%S')}"
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "work_order.json").write_text(json.dumps(work_order, indent=2) + "\n", encoding="utf-8")
        (session_dir / "command.json").write_text(json.dumps({"command": command}, indent=2) + "\n", encoding="utf-8")
        if dry_run:
            return {
                "status": "dry_run",
                "session_id": session_id,
                "task_id": task["id"],
                "runner": work_order["runner"],
                "command": command,
                "work_order": work_order_result["path"],
            }

        task["status"] = "running"
        task["session_id"] = session_id
        self._write_task(task_path, task)
        started = _dt.datetime.now(_dt.UTC)
        try:
            completed = subprocess.run(
                command,
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=None,
            )
            (session_dir / "stdout.log").write_text(completed.stdout or "", encoding="utf-8")
            (session_dir / "stderr.log").write_text(completed.stderr or "", encoding="utf-8")
            (session_dir / "exit_code.txt").write_text(str(completed.returncode), encoding="utf-8")
            task["status"] = "done" if completed.returncode == 0 else "failed"
            task["exit_code"] = completed.returncode
        except FileNotFoundError as exc:
            task["status"] = "blocked"
            task["blocker"] = str(exc)
        finally:
            task["started_at"] = started.isoformat()
            task["ended_at"] = _dt.datetime.now(_dt.UTC).isoformat()
            self._write_task(task_path, task)

        return {
            "status": task["status"],
            "session_id": session_id,
            "task": task,
            "session_path": str(session_dir.relative_to(self.project_path)),
        }

    def workflow_list(self) -> dict[str, Any]:
        active = self.active_pack().get("active") or {}
        workflows = active.get("workflows") or []
        return {"workflows": workflows, "pack": active.get("id")}

    def workflow_run(self, workflow_id: str, *, runner: str = "codex_cli", dry_run: bool = False) -> dict[str, Any]:
        active = self.active_pack().get("active") or {}
        known = set(active.get("workflows") or [])
        if known and workflow_id not in known:
            raise ValueError(f"Workflow {workflow_id!r} is not declared by active pack {active.get('id')!r}")
        title = workflow_id.replace("_", " ").replace("-", " ").title()
        description = (
            f"Run the `{workflow_id}` workflow for this KRAIL project. "
            "Inspect current captures, sources, and integrity records; then create or update repo-backed outputs. "
            "If the workflow cannot be completed, record blockers and missing evidence."
        )
        task = self.create_task(title, description=description, runner=runner, workflow=workflow_id, role="research")["task"]
        if dry_run:
            return {"status": "created", "task": task, "dry_run": True}
        dispatch = self.dispatch_task(task["id"], runner=runner)
        return {"status": "dispatched", "task": task, "dispatch": dispatch}


def shutil_which(executable: str) -> str | None:
    from shutil import which

    return which(executable)
