from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        manifest_path = self.project_path / "rail.yaml"
        check("manifest", manifest_path.exists(), "rail.yaml exists" if manifest_path.exists() else "rail.yaml missing")
        for rel in [".ontology", "topics", "research_plan", "agents", "artifacts"]:
            path = self.project_path / rel
            check(f"path:{rel}", path.exists(), f"{rel} exists" if path.exists() else f"{rel} missing")
        pack_state = self.active_pack().get("active")
        check("pack", bool(pack_state), f"active pack: {pack_state.get('id')}" if pack_state else "no active .krail/pack.yaml")
        inbox = self.project_path / "topics" / "inbox"
        check("capture_inbox", inbox.exists(), "topics/inbox exists" if inbox.exists() else "topics/inbox will be created on first capture")
        ok = all(item["ok"] for item in checks if not item["name"].startswith("capture_inbox"))
        return {"ok": ok, "checks": checks}
