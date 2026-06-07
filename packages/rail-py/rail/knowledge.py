from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rail.markdown_graph import (
    build_markdown_graph,
    check_markdown_graph,
    export_graph,
    filter_documents,
    filter_edges,
    filter_entities,
    load_or_build_graph,
    validate_markdown_graph,
)
from rail.vector_store import LocalVectorStore


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
            "FailureMode",
            "ProjectIdea",
            "ExecutionFramework",
            "RobotSystem",
            "GeometryTechnique",
            "TaskFamily",
            "Claim",
            "Limitation",
            "OpenProblem",
            "Experiment",
        ],
        "link_types": [
            "Paper INTRODUCES Method",
            "Package IMPLEMENTS Method",
            "Paper EVALUATES_ON Benchmark",
            "Benchmark EXPOSES FailureMode",
            "Method REQUIRES GeometryTechnique",
            "ProjectIdea BUILDS_ON Package",
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

KRAIL_AGENT_ROLES: dict[str, dict[str, str]] = {
    "doctor": {
        "label": "KRAIL Doctor Agent",
        "purpose": "Audit and repair KRAIL project structure, workflow definitions, graph/vector health, and agent scaffolding.",
    },
    "platform": {
        "label": "KRAIL Platform Manager",
        "purpose": "Create and evolve KRAIL workflows, agent roles, prompts, skills, and project operating conventions.",
    },
}

KRAIL_AGENT_PROMPTS: dict[str, str] = {
    "doctor": """# KRAIL Doctor Agent Prompt

You are the KRAIL doctor agent for this local knowledge project.

Your job is to inspect and improve platform health without inventing unsupported
knowledge. Treat the Git repository as the source of truth.

## Responsibilities

1. Run deterministic checks such as `krail --local doctor`, `krail --local graph check`, and `krail --local vector build`.
2. Inspect `rail.yaml`, `.krail/pack.yaml`, `research_plan/workflows/`, `agents/`, `skills/`, and `research_plan/state/`.
3. Identify broken workflow specs, missing prompts, missing required folders, stale graph artifacts, and weak verification gates.
4. Make small repo-backed fixes when safe.
5. Record unresolved blockers and recommended next actions under `research_plan/`.

## Rules

- Do not delete project knowledge unless the work order explicitly asks for cleanup.
- Do not mark generated claims as verified without evidence.
- Prefer local commands and repo-backed files over API-only state.
- Keep changes scoped to KRAIL platform health unless asked to work on domain content.
""",
    "platform": """# KRAIL Platform Manager Prompt

You are the KRAIL platform manager for this local knowledge project.

Your job is to design and maintain workflows, agent roles, prompts, and skills
that let other agents work safely in the knowledge base.

## Responsibilities

1. Convert user goals into durable workflow specs under `research_plan/workflows/`.
2. Create role-specific prompts under `agents/prompts/` and checklists under `agents/checklists/`.
3. Keep workflow steps explicit about runner, role, dependencies, verification, and expected outputs.
4. Add deterministic command steps for `doctor`, graph checks, vector builds, tests, and project-specific verification.
5. Record assumptions, gaps, and follow-up work in `research_plan/`.

## Rules

- Prefer sequential workflows until explicit parallel orchestration exists.
- Treat agent output as candidate state until verification passes.
- Do not broaden runner permissions silently.
- Keep workflow specs readable enough for a human to audit before cron dispatch.
""",
}

KRAIL_AGENT_CHECKLISTS: dict[str, str] = {
    "doctor": """# KRAIL Doctor Checklist

- run `krail --local doctor`
- inspect active pack and workflow specs
- verify graph and vector commands still work
- check that agents and skills have project-specific guidance
- record blockers with exact file paths and commands
- avoid changing domain knowledge without evidence
""",
    "platform": """# KRAIL Platform Manager Checklist

- create or update workflow specs under `research_plan/workflows/`
- include command and agent verification steps
- keep prompts/checklists role-specific
- preserve local-first behavior
- document cron entry points and expected logs
- avoid unbounded autonomous loops
""",
}

WORKFLOW_TEMPLATES: dict[str, dict[str, Any]] = {
    "project_doctor": {
        "id": "project_doctor",
        "description": "Audit KRAIL project health and record platform remediation work.",
        "schedule": "",
        "steps": [
            {"id": "doctor", "kind": "command", "run": "krail --local doctor"},
            {
                "id": "doctor_agent",
                "kind": "agent",
                "role": "doctor",
                "runner": "codex_cli",
                "prompt": "Audit workflow specs, graph/vector health, agent prompts, skills, and project structure. Apply safe fixes and record blockers.",
            },
            {"id": "verify", "kind": "command", "run": "krail --local doctor && krail --local graph check"},
        ],
    },
    "weekly_research_review": {
        "id": "weekly_research_review",
        "description": "Review captures, refresh retrieval artifacts, and audit platform health.",
        "schedule": "0 8 * * 1",
        "steps": [
            {"id": "doctor", "kind": "command", "run": "krail --local doctor"},
            {
                "id": "research_triage",
                "kind": "agent",
                "role": "research",
                "runner": "codex_cli",
                "prompt": "Review new captures and update topics with evidence-backed notes, gaps, and next actions.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
            {
                "id": "audit",
                "kind": "agent",
                "role": "doctor",
                "runner": "codex_cli",
                "prompt": "Audit workflow outputs and record unresolved blockers under research_plan/.",
            },
        ],
    },
    "rag_refresh": {
        "id": "rag_refresh",
        "description": "Refresh graph and local vector artifacts for RAG search.",
        "schedule": "",
        "steps": [
            {"id": "graph_build", "kind": "command", "run": "krail --local graph build"},
            {"id": "graph_check", "kind": "command", "run": "krail --local graph check"},
            {"id": "vector_build", "kind": "command", "run": "krail --local vector build"},
        ],
    },
    "paper_ingest": {
        "id": "paper_ingest",
        "description": "Capture and triage a new research paper or source pointer.",
        "schedule": "",
        "steps": [
            {
                "id": "research",
                "kind": "agent",
                "role": "research",
                "runner": "codex_cli",
                "prompt": "Ingest the provided paper/source pointer, record exact evidence, identify claims, and list gaps.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
            {
                "id": "audit",
                "kind": "agent",
                "role": "doctor",
                "runner": "codex_cli",
                "prompt": "Audit the ingest output for unsupported claims, missing source metadata, and workflow gaps.",
            },
        ],
    },
    "release_readiness": {
        "id": "release_readiness",
        "description": "Check KRAIL project readiness before publishing changes or a release.",
        "schedule": "",
        "steps": [
            {"id": "doctor", "kind": "command", "run": "krail --local doctor"},
            {"id": "graph_check", "kind": "command", "run": "krail --local graph check"},
            {"id": "vector_build", "kind": "command", "run": "krail --local vector build"},
            {
                "id": "release_audit",
                "kind": "agent",
                "role": "doctor",
                "runner": "codex_cli",
                "prompt": "Review readiness for release or handoff. Record test status, blockers, changed files, and next actions.",
            },
        ],
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


def _yaml_scalar(value: str) -> str:
    return json.dumps(value)


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
        ignored_prefixes = ("research_plan/graph/", "docs/data/")
        suffixes = {".md", ".txt", ".yaml", ".yml", ".json", ".csv"}
        docs: list[Path] = []
        for path in self.project_path.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_parts for part in path.parts):
                continue
            rel = path.relative_to(self.project_path).as_posix()
            if rel.startswith(ignored_prefixes):
                continue
            if path.suffix.lower() in suffixes:
                docs.append(path)
        return sorted(docs)

    @staticmethod
    def _terms(query: str) -> list[str]:
        terms: list[str] = []
        for raw in _WORD_RE.findall(query):
            parts = [raw, *re.split(r"[_.-]+", raw)]
            for part in parts:
                term = part.lower()
                if len(term) > 1 and term not in _STOPWORDS:
                    terms.append(term)
        return sorted(set(terms))

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

    def search(self, query: str, *, limit: int = 10, explain: bool = False, rag: bool = False) -> dict[str, Any]:
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
        lexical_hits = [hit.to_dict() for hit in hits[:limit]]
        result: dict[str, Any] = {"query": query, "hits": lexical_hits}
        if rag:
            vector = self.vector_search(query, limit=limit)
            result["vector_hits"] = vector.get("hits", [])
            result["hits"] = self._merge_search_hits(lexical_hits, vector.get("hits", []), limit=limit)
            result["rag"] = {"database": vector.get("database"), "embedding": vector.get("embedding"), "status": vector.get("status", "ok")}
        graph = self._graph_context(query, limit=5)
        if graph:
            graph_boosts = self._graph_boosts(graph)
            for hit in result["hits"]:
                boost = graph_boosts.get(hit["path"], 0)
                if boost:
                    hit["graph_score"] = boost
                    hit["score"] = round(float(hit.get("score") or 0) + boost, 3)
            result["hits"] = sorted(result["hits"], key=lambda item: (-float(item.get("score") or 0), item["path"]))[:limit]
            result["graph_context"] = graph
        if explain:
            result["explain"] = {
                "mode": "local_hybrid" if rag else "local_keyword_graph",
                "signals": ["term_frequency", "title_match", "path_match", "typed_wikilink_count", "markdown_graph_context", "local_vector_cosine" if rag else "vector_optional"],
                "note": "RAG mode uses a local SQLite vector store with deterministic hashed embeddings. Model-backed embeddings can replace this later.",
            }
        return result

    @staticmethod
    def _merge_search_hits(lexical_hits: list[dict[str, Any]], vector_hits: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for hit in lexical_hits:
            key = hit["path"]
            merged[key] = {**hit, "lexical_score": hit.get("score", 0), "vector_score": 0, "score": hit.get("score", 0)}
        for hit in vector_hits:
            key = hit["path"]
            current = merged.get(key, {"path": key, "title": hit.get("title"), "matched_terms": [], "snippet": hit.get("snippet", "")})
            lexical_score = float(current.get("lexical_score") or 0)
            vector_score = float(hit.get("score") or 0)
            current.update(
                {
                    "title": current.get("title") or hit.get("title"),
                    "snippet": current.get("snippet") or hit.get("snippet", ""),
                    "lexical_score": lexical_score,
                    "vector_score": vector_score,
                    "score": round(lexical_score + vector_score * 10, 3),
                }
            )
            merged[key] = current
        return sorted(merged.values(), key=lambda item: (-float(item.get("score") or 0), item["path"]))[:limit]

    @staticmethod
    def _graph_boosts(graph_context: dict[str, Any]) -> dict[str, float]:
        boosts: dict[str, float] = {}
        for doc in graph_context.get("documents", []):
            path = doc.get("path")
            if path:
                boosts[path] = boosts.get(path, 0) + 1.5
        for edge in graph_context.get("edges", []):
            source = edge.get("source")
            if source:
                boosts[source] = boosts.get(source, 0) + 0.75
        return boosts

    def _graph_context(self, query: str, *, limit: int = 5) -> dict[str, Any] | None:
        graph = load_or_build_graph(self.project_path)
        terms = set(self._terms(query))
        if not terms:
            return None
        entities = [
            node for node in graph.get("nodes", [])
            if node.get("nodeType") == "entity" and terms.intersection(set(self._terms(str(node.get("label") or ""))))
        ][:limit]
        if not entities:
            return None
        entity_ids = {entity["id"] for entity in entities}
        edges = [
            edge for edge in graph.get("edges", [])
            if edge.get("from") in entity_ids or edge.get("to") in entity_ids
        ][:limit]
        docs = [
            doc for doc in graph.get("documents", [])
            if any(entity.get("label") in set(doc.get("entities", [])) for entity in entities)
        ][:limit]
        return {"entities": entities, "edges": edges, "documents": docs, "graphGeneratedAt": graph.get("generatedAt")}

    def think(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        search = self.search(query, limit=limit, explain=True, rag=True)
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
            "graph_context": search.get("graph_context"),
            "vector_hits": search.get("vector_hits", []),
            "confidence": confidence,
            "gaps": [
                "Vector retrieval currently uses local hashed embeddings, not model embeddings.",
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
        title: str | None = None,
        topics: list[str] | None = None,
        entities: list[str] | None = None,
        entity_type: str | None = None,
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
            f"title: {_yaml_scalar(title or kind.title())}",
            f"kind: {_yaml_scalar(kind)}",
            f"type: {_yaml_scalar(kind)}",
            f"captured_at: {_dt.datetime.now(_dt.UTC).isoformat()}",
        ]
        if topics:
            header.append("topics:")
            header.extend(f"  - {_yaml_scalar(topic)}" for topic in topics)
        if entities:
            header.append("entities:")
            header.extend(f"  - {_yaml_scalar(entity)}" for entity in entities)
            if entity_type:
                header.append("entity_metadata:")
                header.extend([f"  - name: {_yaml_scalar(entity)}\n    entity_type: {_yaml_scalar(entity_type)}" for entity in entities])
        if url:
            header.append(f"url: {_yaml_scalar(url)}")
        if workflow:
            header.append(f"workflow: {_yaml_scalar(workflow)}")
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
        workflow_validation = self.workflow_validate_all()
        check(
            "workflows",
            workflow_validation["ok"],
            f"{workflow_validation['valid']} valid workflow specs, {workflow_validation['invalid']} invalid",
        )
        for rel in ["agents/prompts/doctor.md", "agents/prompts/platform.md", "skills/krail-platform.md"]:
            path = self.project_path / rel
            check(f"krail_agent:{rel}", path.exists(), f"{rel} exists" if path.exists() else f"{rel} missing; run `krail --local agent scaffold-krail`")
        stale_locks = []
        for lock in sorted(self.locks_dir.glob("workflow-*.lock")):
            stale_locks.append(str(lock.relative_to(self.project_path)))
        warn("workflow_locks", not stale_locks, "workflow lock files exist; remove stale locks only after confirming no workflow is running: " + ", ".join(stale_locks))
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
        present_transient = [
            rel
            for rel in transient_dirs
            if (self.project_path / rel).exists() and any((self.project_path / rel).iterdir())
        ]
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
        return {"ok": ok, "checks": checks, "warnings": warnings, "workflow_validation": workflow_validation}

    def graph_build(self, *, write: bool = True) -> dict[str, Any]:
        return build_markdown_graph(self.project_path, write=write)

    def graph_validate(self) -> dict[str, Any]:
        return validate_markdown_graph(self.project_path)

    def graph_check(self) -> dict[str, Any]:
        return check_markdown_graph(self.project_path)

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

    def vector_build(self, *, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
        return LocalVectorStore(self.project_path, provider=provider, model=model).build(self._iter_docs())

    def vector_search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        store = LocalVectorStore(self.project_path)
        result = store.search(query, limit=limit)
        if result.get("status") == "missing_index":
            self.vector_build()
            result = store.search(query, limit=limit)
        return result

    def ci_init(self, *, path: str = ".github/workflows/krail-local-preview.yml") -> dict[str, Any]:
        rel = path
        target = self.project_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        content = """name: KRAIL Local Preview

on:
  push:
  pull_request:

jobs:
  krail:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install KRAIL
        run: |
          python -m pip install --upgrade pip
          if [ -d packages/rail-py ]; then
            pip install -e packages/rail-py
          else
            pip install "git+https://github.com/AkeBoss-tech/knowledge.git@future#subdirectory=packages/rail-py"
          fi
      - name: Doctor
        run: krail --local doctor
      - name: Build markdown graph
        run: krail --local graph build
      - name: Check markdown graph
        run: krail --local graph check
      - name: Build local vector index
        run: krail --local vector build
      - name: RAG smoke test
        run: krail --local search "project" --rag --explain
"""
        target.write_text(content, encoding="utf-8")
        return {"status": "written", "path": rel}

    @property
    def tasks_dir(self) -> Path:
        return self.project_path / "research_plan" / "tasks"

    @property
    def work_orders_dir(self) -> Path:
        return self.project_path / "research_plan" / "work_orders"

    @property
    def sessions_dir(self) -> Path:
        return self.project_path / "research_plan" / "sessions"

    @property
    def workflow_specs_dir(self) -> Path:
        return self.project_path / "research_plan" / "workflows"

    @property
    def locks_dir(self) -> Path:
        return self.krail_dir / "locks"

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

    def scaffold_krail_agents(self, *, force: bool = False) -> dict[str, Any]:
        written: list[str] = []
        skipped: list[str] = []

        def write(rel: str, content: str) -> None:
            path = self.project_path / rel
            if path.exists() and not force:
                skipped.append(rel)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.rstrip() + "\n", encoding="utf-8")
            written.append(rel)

        for role, meta in KRAIL_AGENT_ROLES.items():
            cfg = {
                "role": role,
                "label": meta["label"],
                "purpose": meta["purpose"],
                "runner": {"default": "codex_cli", "approval_required": role == "platform", "max_retries": 1},
                "permissions": {
                    "read": ["rail.yaml", ".krail/pack.yaml", "research_plan", "topics", "agents", "skills", "specs"],
                    "write": ["research_plan", "agents", "skills", "specs"],
                    "deny": [".git", ".krail/vector.sqlite"],
                },
                "prompts": {
                    "system": f"agents/prompts/{role}.md",
                    "checklist": f"agents/checklists/{role}.md",
                },
                "completion": {"requires": ["summary", "changed_files", "blockers_or_gaps"]},
            }
            write(f"agents/{role}.yaml", yaml.safe_dump(cfg, sort_keys=False))
            write(f"agents/prompts/{role}.md", KRAIL_AGENT_PROMPTS[role])
            write(f"agents/checklists/{role}.md", KRAIL_AGENT_CHECKLISTS[role])

        write(
            "skills/krail-platform.md",
            """# KRAIL Platform Skill

Use this skill when creating or changing KRAIL workflows, agent roles, prompts,
or project operating conventions.

## Workflow Rules

- Store durable workflow specs under `research_plan/workflows/`.
- Use command steps for deterministic checks.
- Use agent steps for research, coding, synthesis, or audit work.
- Add a verification step before any workflow claims completion.
- Prefer dry runs before cron dispatch.

## Required Checks

Run these before handing off a platform change:

```bash
krail --local doctor
krail --local workflow list
krail --local graph check
```
""",
        )
        return {"status": "scaffolded", "written": written, "skipped": skipped}

    def agent_prompt(self, role: str, *, task: str = "") -> dict[str, Any]:
        role = role.strip().lower()
        aliases = {"krail_doctor": "doctor", "krail-platform": "platform", "platform_manager": "platform"}
        role = aliases.get(role, role)
        prompt_path = self.project_path / "agents" / "prompts" / f"{role}.md"
        checklist_path = self.project_path / "agents" / "checklists" / f"{role}.md"
        if prompt_path.exists():
            prompt = prompt_path.read_text(encoding="utf-8")
        elif role in KRAIL_AGENT_PROMPTS:
            prompt = KRAIL_AGENT_PROMPTS[role]
        else:
            prompt = f"# {role.title()} Agent Prompt\n\nWork inside this KRAIL project and follow the work order."
        checklist = ""
        if checklist_path.exists():
            checklist = checklist_path.read_text(encoding="utf-8")
        elif role in KRAIL_AGENT_CHECKLISTS:
            checklist = KRAIL_AGENT_CHECKLISTS[role]
        project_context = (
            f"Project root: {self.project_path}\n"
            "Primary commands:\n"
            "- krail --local doctor\n"
            "- krail --local workflow list\n"
            "- krail --local graph check\n"
            "- krail --local vector build\n"
        )
        rendered = prompt.rstrip() + "\n\n## KRAIL Project Context\n\n" + project_context
        if task:
            rendered += "\n## Task\n\n" + task.strip() + "\n"
        if checklist:
            rendered += "\n## Checklist\n\n" + checklist.strip() + "\n"
        return {"role": role, "prompt": rendered}

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
        role = task.get("role") or "research"
        role_prompt = self.agent_prompt(role, task=task.get("description") or task["title"]).get("prompt", "")
        return {
            "work_order_id": wo_id,
            "task_id": task["id"],
            "title": task["title"],
            "description": task.get("description") or task["title"],
            "runner": task.get("runner") or "codex_cli",
            "role": role,
            "workflow": task.get("workflow"),
            "allowed_paths": ["topics", "research_plan", "artifacts", "agents", "skills", "specs"],
            "outputs_required": ["summary", "changed_files", "blockers_or_gaps"],
            "trust": "candidate_until_reviewed",
            "role_prompt": role_prompt,
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
            f"Role guidance:\n{work_order.get('role_prompt') or 'Use the project role prompt and checklist if available.'}\n\n"
            "Rules:\n"
            "- Work only inside this project repository.\n"
            "- Prefer evidence files, captures, and integrity records over unsupported claims.\n"
            "- Write useful outputs under topics/, research_plan/, or artifacts/.\n"
            "- End with a concise summary, changed files, gaps, and suggested next actions.\n"
            f"- Before exiting, write a JSON result to `{work_order.get('session_result_path', 'session_result.json')}` with keys: summary, changed_files, evidence, blockers_or_gaps, suggested_next_actions.\n"
            "- Do not promote generated claims as verified without evidence.\n"
        )

    def dispatch_task(self, task_id: str, *, runner: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        task_path, task = self._load_task(task_id)
        if runner:
            task["runner"] = runner
        work_order_result = self.create_work_order(task["id"])
        work_order = work_order_result["work_order"]
        session_id = f"session_{task['id']}_{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M%S')}"
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        work_order["session_id"] = session_id
        work_order["session_path"] = str(session_dir.relative_to(self.project_path))
        work_order["session_result_path"] = str((session_dir / "session_result.json").relative_to(self.project_path))
        prompt = self._prompt_for_work_order(work_order)
        command = self._runner_command(work_order["runner"], prompt)
        (session_dir / "work_order.json").write_text(json.dumps(work_order, indent=2) + "\n", encoding="utf-8")
        (session_dir / "command.json").write_text(json.dumps({"command": command}, indent=2) + "\n", encoding="utf-8")
        (session_dir / "session_result.template.json").write_text(
            json.dumps(
                {
                    "summary": "",
                    "changed_files": [],
                    "evidence": [],
                    "blockers_or_gaps": [],
                    "suggested_next_actions": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
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
            result_path = session_dir / "session_result.json"
            if result_path.exists():
                try:
                    task["session_result"] = json.loads(result_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    task["session_result_error"] = str(exc)
            self._write_task(task_path, task)

        return {
            "status": task["status"],
            "session_id": session_id,
            "task": task,
            "session_path": str(session_dir.relative_to(self.project_path)),
        }

    def workflow_templates(self) -> dict[str, Any]:
        return {"templates": sorted(WORKFLOW_TEMPLATES.keys())}

    def _load_workflow_spec_file(self, path: Path) -> dict[str, Any]:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("workflow spec root must be a mapping")
        return data

    def _validate_workflow_spec(self, spec: dict[str, Any], *, path: str | None = None) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        workflow_id = spec.get("id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            errors.append("id must be a non-empty string")
        schedule = spec.get("schedule")
        if schedule is not None and not isinstance(schedule, str):
            errors.append("schedule must be a string when present")
        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("steps must be a non-empty list")
            steps = []
        seen: set[str] = set()
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                errors.append(f"step {index} must be a mapping")
                continue
            step_id = step.get("id")
            if not isinstance(step_id, str) or not step_id.strip():
                errors.append(f"step {index} id must be a non-empty string")
                step_id = f"step_{index}"
            if step_id in seen:
                errors.append(f"duplicate step id: {step_id}")
            seen.add(str(step_id))
            kind = step.get("kind", "command")
            if kind not in {"command", "agent"}:
                errors.append(f"step {step_id} kind must be command or agent")
            if kind == "command":
                run = step.get("run")
                if not isinstance(run, str) or not run.strip():
                    errors.append(f"command step {step_id} requires run")
            if kind == "agent":
                runner = str(step.get("runner") or spec.get("runner") or "codex_cli")
                if runner not in LOCAL_RUNNERS:
                    errors.append(f"agent step {step_id} has unknown runner: {runner}")
                role = step.get("role")
                if role is not None and not isinstance(role, str):
                    errors.append(f"agent step {step_id} role must be a string")
                prompt = step.get("prompt") or step.get("description")
                if not isinstance(prompt, str) or not prompt.strip():
                    warnings.append(f"agent step {step_id} has no prompt; generic prompt will be used")
            on_failure = step.get("on_failure", "stop")
            if on_failure not in {"stop", "continue"}:
                errors.append(f"step {step_id} on_failure must be stop or continue")
            retry = step.get("retry", 0)
            if not isinstance(retry, int) or retry < 0:
                errors.append(f"step {step_id} retry must be a non-negative integer")
            timeout_minutes = step.get("timeout_minutes")
            if timeout_minutes is not None and (not isinstance(timeout_minutes, int) or timeout_minutes <= 0):
                errors.append(f"step {step_id} timeout_minutes must be a positive integer")
        return {"ok": not errors, "id": workflow_id, "path": path, "errors": errors, "warnings": warnings, "steps": len(steps)}

    def workflow_validate(self, workflow_id: str) -> dict[str, Any]:
        shown = self.workflow_show(workflow_id, validate=False)
        validation = self._validate_workflow_spec(shown["workflow"], path=shown["path"])
        return validation

    def workflow_validate_all(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for path in sorted(self.workflow_specs_dir.glob("*.yaml")) + sorted(self.workflow_specs_dir.glob("*.yml")):
            try:
                spec = self._load_workflow_spec_file(path)
                result = self._validate_workflow_spec(spec, path=str(path.relative_to(self.project_path)))
            except Exception as exc:
                result = {"ok": False, "id": path.stem, "path": str(path.relative_to(self.project_path)), "errors": [str(exc)], "warnings": [], "steps": 0}
            results.append(result)
        invalid = [item for item in results if not item.get("ok")]
        return {"ok": not invalid, "valid": len(results) - len(invalid), "invalid": len(invalid), "workflows": results}

    def workflow_runs(self, *, limit: int = 20) -> dict[str, Any]:
        runs: list[dict[str, Any]] = []
        for path in sorted(self.sessions_dir.glob("workflow_*/result.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            runs.append(
                {
                    "run_id": data.get("run_id") or path.parent.name,
                    "workflow": data.get("workflow"),
                    "status": data.get("status"),
                    "started_at": data.get("started_at"),
                    "ended_at": data.get("ended_at"),
                    "duration_seconds": data.get("duration_seconds"),
                    "failed_step": data.get("failed_step"),
                    "path": str(path.parent.relative_to(self.project_path)),
                }
            )
        return {"runs": runs[:limit], "limit": limit}

    def workflow_status(self, run_id: str) -> dict[str, Any]:
        candidates = [
            self.sessions_dir / run_id / "result.json",
            self.sessions_dir / self._slug(run_id) / "result.json",
        ]
        for path in candidates:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        matches = sorted(self.sessions_dir.glob(f"{run_id}*/result.json"))
        if len(matches) == 1:
            return json.loads(matches[0].read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Workflow run not found: {run_id}")

    def workflow_list(self) -> dict[str, Any]:
        active = self.active_pack().get("active") or {}
        raw_pack_workflows = active.get("workflows") or []
        pack_workflows = [item for item in raw_pack_workflows if isinstance(item, str)]
        ignored_pack_items = [item for item in raw_pack_workflows if not isinstance(item, str)]
        spec_workflows = []
        for path in sorted(self.workflow_specs_dir.glob("*.yaml")) + sorted(self.workflow_specs_dir.glob("*.yml")):
            try:
                data = self._load_workflow_spec_file(path)
                validation = self._validate_workflow_spec(data, path=str(path.relative_to(self.project_path)))
            except Exception as exc:
                spec_workflows.append({"id": path.stem, "path": str(path.relative_to(self.project_path)), "valid": False, "error": str(exc)})
                continue
            spec_workflows.append(
                {
                    "id": data.get("id") or path.stem,
                    "path": str(path.relative_to(self.project_path)),
                    "valid": validation["ok"],
                    "steps": len(data.get("steps") or []),
                    "schedule": data.get("schedule"),
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                }
            )
        result: dict[str, Any] = {"workflows": pack_workflows, "specs": spec_workflows, "pack": active.get("id")}
        if ignored_pack_items:
            result["warnings"] = ["active pack has non-string workflow entries; move workflow settings out of the workflows list"]
        return result

    def workflow_init(self, workflow_id: str, *, force: bool = False, template: str | None = None) -> dict[str, Any]:
        rel = Path("research_plan") / "workflows" / f"{self._slug(workflow_id)}.yaml"
        path = self.project_path / rel
        if path.exists() and not force:
            return {"status": "exists", "path": str(rel)}
        path.parent.mkdir(parents=True, exist_ok=True)
        if template:
            if template not in WORKFLOW_TEMPLATES:
                raise ValueError(f"Unknown workflow template: {template}")
            spec = json.loads(json.dumps(WORKFLOW_TEMPLATES[template]))
            spec["id"] = workflow_id
        else:
            spec = {
                "id": workflow_id,
                "description": f"Local KRAIL workflow for {workflow_id}.",
                "schedule": "",
                "steps": [
                    {
                        "id": "doctor",
                        "kind": "command",
                        "run": "krail --local doctor",
                    },
                    {
                        "id": "work",
                        "kind": "agent",
                        "role": "platform",
                        "runner": "codex_cli",
                        "prompt": f"Run the {workflow_id} workflow. Update repo-backed outputs and record gaps.",
                    },
                    {
                        "id": "verify",
                        "kind": "command",
                        "run": "krail --local graph check && krail --local vector build",
                    },
                ],
            }
        path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
        return {"status": "written", "path": str(rel), "workflow": spec, "template": template}

    def _workflow_spec_path(self, workflow_id: str) -> Path:
        candidates = [
            self.workflow_specs_dir / f"{workflow_id}.yaml",
            self.workflow_specs_dir / f"{workflow_id}.yml",
            self.workflow_specs_dir / f"{self._slug(workflow_id)}.yaml",
        ]
        for path in candidates:
            if path.exists():
                return path
        raise FileNotFoundError(f"Workflow spec not found: {workflow_id}")

    def workflow_show(self, workflow_id: str, *, validate: bool = True) -> dict[str, Any]:
        path = self._workflow_spec_path(workflow_id)
        spec = self._load_workflow_spec_file(path)
        result = {"path": str(path.relative_to(self.project_path)), "workflow": spec}
        if validate:
            result["validation"] = self._validate_workflow_spec(spec, path=result["path"])
        return result

    def _workflow_lock_path(self, workflow_id: str) -> Path:
        return self.locks_dir / f"workflow-{self._slug(workflow_id)}.lock"

    def _acquire_workflow_lock(self, workflow_id: str) -> Path:
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        lock = self._workflow_lock_path(workflow_id)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"workflow already running: {workflow_id} ({lock})") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"workflow": workflow_id, "pid": os.getpid(), "created_at": _dt.datetime.now(_dt.UTC).isoformat()}) + "\n")
        return lock

    def workflow_execute(self, workflow_id: str, *, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
        shown = self.workflow_show(workflow_id)
        spec = shown["workflow"]
        validation = shown.get("validation") or self._validate_workflow_spec(spec, path=shown["path"])
        if not validation["ok"]:
            return {"status": "invalid", "workflow": workflow_id, "validation": validation}
        steps = spec.get("steps") or []
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"Workflow {workflow_id!r} must define a non-empty steps list")
        run_id = f"workflow_{self._slug(str(spec.get('id') or workflow_id))}_{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M%S')}"
        run_dir = self.sessions_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "workflow.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
        started_at = _dt.datetime.now(_dt.UTC)
        results: list[dict[str, Any]] = []
        lock: Path | None = None
        if not dry_run and not force:
            lock = self._acquire_workflow_lock(str(spec.get("id") or workflow_id))
        try:
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    raise ValueError(f"Workflow step {index} must be a mapping")
                step_id = str(step.get("id") or f"step_{index}")
                kind = str(step.get("kind") or "command")
                step_result: dict[str, Any] = {"id": step_id, "kind": kind, "status": "dry_run" if dry_run else "running"}
                if dry_run:
                    step_result["step"] = step
                    results.append(step_result)
                    continue
                attempts = int(step.get("retry", 0)) + 1
                on_failure = str(step.get("on_failure") or "stop")
                timeout_seconds = int(step["timeout_minutes"]) * 60 if step.get("timeout_minutes") else None
                if kind == "command":
                    command = str(step.get("run") or "").strip()
                    if not command:
                        raise ValueError(f"Command step {step_id!r} is missing run")
                    last_returncode = 1
                    for attempt in range(1, attempts + 1):
                        try:
                            completed = subprocess.run(command, cwd=self.project_path, shell=True, capture_output=True, text=True, timeout=timeout_seconds)
                            last_returncode = completed.returncode
                            (run_dir / f"{index:02d}-{step_id}.attempt{attempt}.stdout.log").write_text(completed.stdout or "", encoding="utf-8")
                            (run_dir / f"{index:02d}-{step_id}.attempt{attempt}.stderr.log").write_text(completed.stderr or "", encoding="utf-8")
                        except subprocess.TimeoutExpired as exc:
                            last_returncode = 124
                            (run_dir / f"{index:02d}-{step_id}.attempt{attempt}.stdout.log").write_text(exc.stdout or "", encoding="utf-8")
                            (run_dir / f"{index:02d}-{step_id}.attempt{attempt}.stderr.log").write_text(exc.stderr or "timeout expired", encoding="utf-8")
                        if last_returncode == 0:
                            break
                    step_result.update({"status": "done" if last_returncode == 0 else "failed", "exit_code": last_returncode, "command": command, "attempts": attempt})
                    results.append(step_result)
                    if last_returncode != 0 and on_failure == "stop":
                        break
                elif kind == "agent":
                    prompt = str(step.get("prompt") or step.get("description") or f"Run workflow step {step_id}.")
                    runner = str(step.get("runner") or spec.get("runner") or "codex_cli")
                    role = str(step.get("role") or "research")
                    dispatch: dict[str, Any] = {"status": "failed"}
                    created: dict[str, Any] = {"task": {"id": ""}}
                    for attempt in range(1, attempts + 1):
                        created = self.create_task(
                            f"{spec.get('id') or workflow_id}: {step_id}",
                            description=prompt,
                            runner=runner,
                            workflow=str(spec.get("id") or workflow_id),
                            role=role,
                        )
                        dispatch = self.dispatch_task(created["task"]["id"], runner=runner, dry_run=False)
                        if dispatch.get("status") == "done":
                            break
                    step_result.update({"status": dispatch.get("status"), "task_id": created["task"]["id"], "runner": runner, "role": role, "dispatch": dispatch})
                    results.append(step_result)
                    if dispatch.get("status") not in {"done", "dispatched"} and on_failure == "stop":
                        break
                else:
                    raise ValueError(f"Unsupported workflow step kind: {kind}")
                time.sleep(0.01)
        finally:
            if lock and lock.exists():
                lock.unlink()
        ended_at = _dt.datetime.now(_dt.UTC)
        failed_steps = [item for item in results if item.get("status") not in {"done", "dry_run"}]
        status = "dry_run" if dry_run else ("done" if not failed_steps and len(results) == len(steps) else "failed")
        payload = {
            "status": status,
            "run_id": run_id,
            "workflow": spec.get("id") or workflow_id,
            "path": str(run_dir.relative_to(self.project_path)),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
            "failed_step": failed_steps[0]["id"] if failed_steps else None,
            "steps": results,
            "validation": validation,
        }
        (run_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload

    def workflow_run(self, workflow_id: str, *, runner: str = "codex_cli", dry_run: bool = False) -> dict[str, Any]:
        try:
            self._workflow_spec_path(workflow_id)
        except FileNotFoundError:
            pass
        else:
            return self.workflow_execute(workflow_id, dry_run=dry_run)
        active = self.active_pack().get("active") or {}
        known = {item for item in (active.get("workflows") or []) if isinstance(item, str)}
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
