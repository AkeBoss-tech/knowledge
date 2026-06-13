from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from rail.modes import DEFAULT_MODES, get_mode
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
from rail.source_dependencies import (
    affected_documents,
    changed_sources,
    check_sources,
    dependency_sources,
    load_dependency_manifest,
    validate_dependency_manifest,
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
        "workflows": ["initial_company_map", "company_profile_refresh", "competitor_scan", "source_review", "weekly_exec_brief", "stale_doc_review"],
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

MODE_PACK_ALIASES: dict[str, str] = {
    "research": "research-intelligence",
    "company": "company-brain",
    "software": "software-architecture",
}

LOCAL_RUNNERS: dict[str, dict[str, str]] = {
    "codex_cli": {
        "command_env": "CODEX_CLI_COMMAND",
        "default_command": "codex",
        "description": "Codex CLI local runner",
        "supports_think_synthesis": "true",
    },
    "claude_code": {
        "command_env": "CLAUDE_CODE_COMMAND",
        "default_command": "claude",
        "description": "Claude Code local runner",
        "supports_think_synthesis": "true",
    },
    "gemini_cli": {
        "command_env": "GEMINI_CLI_COMMAND",
        "default_command": "gemini",
        "description": "Gemini CLI local runner",
        "supports_think_synthesis": "true",
    },
    "cursor_cli": {
        "command_env": "CURSOR_CLI_COMMAND",
        "default_command": "agent",
        "description": "Cursor CLI local runner",
        "supports_think_synthesis": "true",
    },
    "copilot_cli": {
        "command_env": "COPILOT_CLI_COMMAND",
        "default_command": "gh copilot suggest",
        "description": "GitHub Copilot CLI helper",
        "supports_think_synthesis": "true",
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
    "wiki": {
        "label": "KRAIL Wiki Writer",
        "purpose": "Turn source-backed topics into concise encyclopedia-style wiki pages with useful rich artifacts.",
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
    "wiki": """# KRAIL Wiki Writer Prompt

You are the KRAIL wiki writer for this local knowledge project.

Your job is to transform source-backed topic notes into polished reader pages
under `docs/wiki/`. The style should feel closer to a concise Wikipedia article
than a work log: neutral, structured, skimmable, and useful.

## Responsibilities

1. Start with `krail --local mode active`, `krail --local wiki plan`, and relevant source files under `topics/`.
2. Use `krail --local wiki build --force` for the deterministic source-linked baseline when useful.
3. Rewrite generated pages in `docs/wiki/` into clear encyclopedia-style pages while preserving frontmatter, especially `source_path`.
4. Add rich elements only when they improve understanding: tables, callouts, Mermaid diagrams, SVG explainers, self-contained HTML demos, lightweight simulations, timelines, local images, generated images, or web/Google Images references.
5. Put reusable rich assets under `docs/wiki/assets/<page-slug>/` or `artifacts/wiki/<page-slug>/` and link to them from the page.
6. Keep claims grounded in the source topic, source URLs, or integrity records. Mark gaps instead of inventing.
7. Run `krail --local wiki check`, `krail --local graph build`, and `krail --local vector build` before finishing.

## Rich Artifact Menu

- `interactive_html`: self-contained HTML files for simulations, timelines, calculators, sortable views, or concept explorers. Use inline CSS/JS only; no network scripts or trackers.
- `svg`: inline or linked SVG diagrams for taxonomies, process maps, architecture sketches, and visual summaries. Include captions or nearby alt text.
- `mermaid`: editable text diagrams for flows, sequences, state machines, and simple graphs.
- `image_asset`: local screenshots, generated images, annotated figures, or exported diagrams under `docs/wiki/assets/<page-slug>/`.
- `web_image_reference`: Google Images or web image references for real-world examples. Prefer official or permissively licensed sources, include source URL/credit/license status when known, and avoid unattributed hotlinks.
- `table`: comparison tables, timelines, glossaries, and matrices.
- `callout`: definitions, caveats, stale warnings, or key takeaways.
- `study_block`: short FAQs, quick checks, flashcard-like prompts, quizzes, or practice questions when useful.

## Rules

- Do not replace canonical topic files with generated prose; topics are the source of truth.
- Preserve source links and cite repo-relative paths for non-obvious claims.
- Prefer succinct explanation over exhaustive dumping.
- Interactive HTML must be self-contained and safe for local viewing: no network scripts, no external trackers, no hidden data exfiltration.
- Web images must have a nearby source URL and attribution/licensing note when known.
- If the source material is too thin, create a short page with explicit gaps rather than padding.
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
    "wiki": """# KRAIL Wiki Writer Checklist

- run `krail --local wiki plan`
- build or refresh deterministic baseline pages
- preserve `source_path` and source-backed claims
- add diagrams, demos, SVGs, tables, images, or web image references only where they clarify the topic
- keep interactive HTML self-contained and record image source/credit details
- run `krail --local wiki check`
- refresh graph/vector artifacts
- record gaps instead of inventing missing facts
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
    "triage_inbox": {
        "id": "triage_inbox",
        "description": "Review captured inbox notes and promote durable knowledge into stable topic pages.",
        "schedule": "",
        "steps": [
            {"id": "doctor", "kind": "command", "run": "krail --local doctor"},
            {"id": "list_inbox", "kind": "command", "run": "krail --local inbox list"},
            {
                "id": "triage",
                "kind": "agent",
                "role": "research",
                "runner": "auto",
                "prompt": "Review unhandled captures from topics/inbox. Promote useful notes into stable topic pages with `krail --local inbox promote` or `krail --local topic upsert`. Leave weak or duplicate material marked as gaps in research_plan/current_plan.md rather than creating loose files.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
        ],
    },
    "rich_wiki_generation": {
        "id": "rich_wiki_generation",
        "description": "Use a coding agent to turn source-backed topics into polished wiki pages with rich artifacts where useful.",
        "schedule": "",
        "steps": [
            {"id": "doctor", "kind": "command", "run": "krail --local doctor"},
            {"id": "plan", "kind": "command", "run": "krail --local wiki plan"},
            {"id": "baseline", "kind": "command", "run": "krail --local wiki build"},
            {
                "id": "write_wiki",
                "kind": "agent",
                "role": "wiki",
                "runner": "auto",
                "prompt": "Generate or refine docs/wiki pages from the current wiki plan. Make pages concise, source-backed, and encyclopedia-like. Use the rich_artifacts catalog from `krail --local wiki plan`: self-contained interactive HTML demos, SVG explainers, Mermaid diagrams, tables, callouts, study blocks, local image assets, generated images, and web/Google Images references are allowed when they materially improve understanding. Store reusable assets under docs/wiki/assets/<page-slug>/ or artifacts/wiki/<page-slug>/. Preserve source_path frontmatter, include image source URL/credit/license status when known, and do not invent unsupported claims.",
            },
            {"id": "check", "kind": "command", "run": "krail --local wiki check"},
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
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
    "source_refresh": {
        "id": "source_refresh",
        "description": "Check source snapshots, identify affected docs, and prepare reviewed knowledge updates.",
        "schedule": "0 8 * * 1",
        "steps": [
            {"id": "validate_dependencies", "kind": "command", "run": "krail --local sources validate"},
            {"id": "check_sources", "kind": "command", "run": "krail --local sources check"},
            {"id": "affected_docs", "kind": "command", "run": "krail --local sources affected"},
            {
                "id": "refresh_agent",
                "kind": "agent",
                "role": "research",
                "runner": "codex_cli",
                "prompt": "Review changed sources and affected documents. Propose evidence-backed updates, mark stale claims, and record gaps.",
            },
            {"id": "verify", "kind": "command", "run": "krail --local doctor && krail --local graph check && krail --local vector build"},
        ],
    },
    "initial_company_map": {
        "id": "initial_company_map",
        "description": "Create the first company map from local captures, web sources, and explicit evidence.",
        "schedule": "",
        "steps": [
            {"id": "doctor", "kind": "command", "run": "krail --local doctor"},
            {
                "id": "company_map",
                "kind": "agent",
                "role": "research",
                "runner": "auto",
                "prompt": "Map the company, key products, teams, systems, policies, datasets, metrics, and open source gaps. Record evidence-backed notes under topics/ and graph metadata in markdown frontmatter.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
        ],
    },
    "company_profile_refresh": {
        "id": "company_profile_refresh",
        "description": "Refresh company profile notes and source-backed claims.",
        "schedule": "0 8 * * 1",
        "steps": [
            {"id": "check_sources", "kind": "command", "run": "krail --local sources check"},
            {
                "id": "profile_update",
                "kind": "agent",
                "role": "research",
                "runner": "auto",
                "prompt": "Review changed company sources and update company overview, product, team, system, and policy notes with citations and gaps.",
            },
            {"id": "verify", "kind": "command", "run": "krail --local doctor && krail --local graph check && krail --local vector build"},
        ],
    },
    "competitor_scan": {
        "id": "competitor_scan",
        "description": "Scan competitor and market signals for company-brain context.",
        "schedule": "",
        "steps": [
            {
                "id": "scan",
                "kind": "agent",
                "role": "research",
                "runner": "auto",
                "prompt": "Identify competitor, market, product, and customer signals relevant to this company brain. Capture sources and separate facts from hypotheses.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
        ],
    },
    "source_review": {
        "id": "source_review",
        "description": "Review source freshness, affected documents, and unsupported company claims.",
        "schedule": "",
        "steps": [
            {"id": "validate_sources", "kind": "command", "run": "krail --local sources validate"},
            {"id": "affected_docs", "kind": "command", "run": "krail --local sources affected"},
            {
                "id": "review",
                "kind": "agent",
                "role": "doctor",
                "runner": "auto",
                "prompt": "Audit stale company-brain docs, unsupported claims, missing source metadata, graph freshness, and suggested remediation tasks.",
            },
        ],
    },
    "weekly_exec_brief": {
        "id": "weekly_exec_brief",
        "description": "Prepare a concise weekly executive brief from reviewed company-brain evidence.",
        "schedule": "0 9 * * 1",
        "steps": [
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
            {
                "id": "brief",
                "kind": "agent",
                "role": "research",
                "runner": "auto",
                "prompt": "Draft a short executive brief from current company-brain evidence. Include citations, stale-source warnings, gaps, and next actions.",
            },
            {"id": "audit", "kind": "agent", "role": "doctor", "runner": "auto", "prompt": "Audit the executive brief for unsupported claims and missing evidence."},
        ],
    },
    "stale_doc_review": {
        "id": "stale_doc_review",
        "description": "Inspect changed sources and stale documents before refreshing company-brain notes.",
        "schedule": "",
        "steps": [
            {"id": "check_sources", "kind": "command", "run": "krail --local sources check"},
            {"id": "affected_docs", "kind": "command", "run": "krail --local sources affected"},
            {
                "id": "stale_synthesis",
                "kind": "think",
                "mode": "hybrid",
                "runner": "auto",
                "query": "Which company-brain documents may be stale and what evidence needs refresh?",
                "limit": 5,
            },
        ],
    },
}

PACK_WORKFLOW_TEMPLATE_ALIASES: dict[str, str] = {
    "add_new_paper": "paper_ingest",
    "weekly_literature_refresh": "weekly_research_review",
    "build_sota_report": "weekly_research_review",
    "register_experiment": "rag_refresh",
    "daily_refresh": "company_profile_refresh",
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

WIKI_RICH_ARTIFACTS: list[dict[str, Any]] = [
    {
        "id": "interactive_html",
        "label": "Self-contained interactive HTML demo",
        "where": "Use for simulations, sortable/comparable views, timelines, calculators, concept explorers, and small local demos.",
        "storage": "docs/wiki/assets/<page-slug>/<artifact-slug>.html or artifacts/wiki/<page-slug>/<artifact-slug>.html",
        "rules": ["inline CSS/JS only", "no external scripts", "no trackers", "link from the wiki page with a clear caption"],
    },
    {
        "id": "svg",
        "label": "Inline or linked SVG explainer",
        "where": "Use for concept maps, process flows, architecture diagrams, taxonomies, and visual summaries.",
        "storage": "inline fenced/svg block or docs/wiki/assets/<page-slug>/<artifact-slug>.svg",
        "rules": ["keep text readable", "include alt/caption text", "avoid decorative-only SVGs"],
    },
    {
        "id": "mermaid",
        "label": "Mermaid diagram",
        "where": "Use for flowcharts, sequence diagrams, state machines, timelines, and simple graphs.",
        "storage": "fenced ```mermaid block in the wiki page",
        "rules": ["prefer Mermaid for diagrams that should remain editable as text"],
    },
    {
        "id": "image_asset",
        "label": "Local image asset",
        "where": "Use for screenshots, generated images, annotated figures, or diagrams exported as images.",
        "storage": "docs/wiki/assets/<page-slug>/<image-name>.<png|jpg|webp>",
        "rules": ["include alt text", "include source/credit when not generated locally", "do not use dark/cropped images when inspection matters"],
    },
    {
        "id": "web_image_reference",
        "label": "Web or Google Images reference",
        "where": "Use for real-world entities, places, products, organisms, historical artifacts, interface screenshots, or examples where external images clarify the topic.",
        "storage": "markdown image/link plus nearby source URL, credit, and license/status when known",
        "rules": ["prefer official or permissively licensed sources", "do not hotlink copyrighted images without attribution", "record retrieval/source URL"],
    },
    {
        "id": "table",
        "label": "Comparison or summary table",
        "where": "Use for concise facts, tradeoffs, timelines, glossaries, and matrices.",
        "storage": "markdown table in the wiki page",
        "rules": ["keep columns scannable", "cite non-obvious facts"],
    },
    {
        "id": "callout",
        "label": "Short callout block",
        "where": "Use for definitions, caveats, stale warnings, or key takeaways.",
        "storage": "blockquote or short section in the wiki page",
        "rules": ["do not hide unsupported claims in callouts"],
    },
    {
        "id": "study_block",
        "label": "Study aid block",
        "where": "Use for FAQs, flashcard-like prompts, quick checks, quizzes, or practice questions when the mode is learning/research oriented.",
        "storage": "markdown section or JSON/HTML asset under artifacts/wiki/<page-slug>/",
        "rules": ["keep answers source-backed", "mark speculative prompts clearly"],
    },
]


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


@dataclass
class ThinkRequest:
    query: str
    limit: int
    mode: str
    requested_runner: str
    retrieval: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ThinkResult:
    query: str
    mode: str
    requested_runner: str
    runner: str | None
    runner_resolution: dict[str, Any] | None
    answer: str
    evidence: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    graph_context: dict[str, Any] | None
    vector_hits: list[dict[str, Any]]
    source_freshness: dict[str, Any]
    confidence: str
    gaps: list[str]
    conflicts: list[str]
    suggested_next_actions: list[str]
    verification: dict[str, Any]
    session: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    def _manifest_data(self) -> dict[str, Any]:
        path = self.project_path / "rail.yaml"
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _integrity_repo(self):
        from rail.integrity import ResearchIntegrityRepo

        return ResearchIntegrityRepo(self.project_path)

    def _iter_docs(self) -> list[Path]:
        ignored_parts = {".git", ".krail", ".rail", "__pycache__", ".pytest_cache", ".venv"}
        ignored_prefixes = ("research_plan/graph/", "research_plan/sessions/", "research_plan/state/", "docs/data/")
        suffixes = {".md", ".txt", ".yaml", ".yml", ".json", ".csv"}
        artifact_skip: set[str] = set()
        try:
            for record in self._integrity_repo().load_artifact_lineage():
                if record.promotion_state != "verified":
                    artifact_skip.add(record.artifact_path)
        except Exception:
            artifact_skip = set()
        docs: list[Path] = []
        for path in self.project_path.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_parts for part in path.parts):
                continue
            rel = path.relative_to(self.project_path).as_posix()
            if rel.startswith(ignored_prefixes):
                continue
            if rel in artifact_skip:
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

    @staticmethod
    def _ensure_list_of_strings(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            if isinstance(value, str) and value.strip():
                result.append(value.strip())
        return result

    @staticmethod
    def _split_markdown_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                raw = text[4:end]
                body = text[end + 4 :].lstrip("\n")
                try:
                    metadata = yaml.safe_load(raw) or {}
                    if isinstance(metadata, dict):
                        return metadata, body
                except Exception:
                    pass
        return {}, text

    @staticmethod
    def _dump_markdown_frontmatter(metadata: dict[str, Any], body: str) -> str:
        cleaned = {key: value for key, value in metadata.items() if value not in (None, "", [], {})}
        return "---\n" + yaml.safe_dump(cleaned, sort_keys=False).strip() + "\n---\n\n" + body.strip() + "\n"

    @staticmethod
    def _think_promotion_state(result: dict[str, Any]) -> str:
        source_freshness = result.get("source_freshness") if isinstance(result.get("source_freshness"), dict) else {}
        verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
        if source_freshness.get("stale_evidence") or source_freshness.get("changed_sources"):
            return "stale"
        if verification.get("ok"):
            return "partially_verified"
        return "needs_evidence"

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

    def _prepare_think_request(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: str = "deterministic",
        runner: str = "auto",
    ) -> ThinkRequest:
        search = self.search(query, limit=limit, explain=True, rag=True)
        hits = search["hits"]
        evidence = [
            {"path": hit["path"], "title": hit["title"], "snippet": hit["snippet"], "score": hit["score"]}
            for hit in hits
        ]
        citations = [
            {
                "ref": f"[{index}]",
                "path": hit["path"],
                "title": hit["title"],
                "score": hit["score"],
            }
            for index, hit in enumerate(evidence, start=1)
        ]
        source_validation = self.sources_validate()
        source_changes = self.sources_changed()
        affected = self.sources_affected()
        affected_paths = [item["path"] for item in affected.get("affected_documents", [])]
        stale_evidence = [
            hit for hit in evidence
            if hit["path"] in set(affected_paths)
        ]
        if hits:
            cited = ", ".join(item["ref"] for item in citations[:3])
            answer = (
                f"Found {len(hits)} relevant local evidence item(s). "
                f"Use {cited} as the strongest current references. "
                "This deterministic thinker does not fabricate synthesis; it packages evidence, source freshness, and next actions for review."
            )
            confidence = "medium" if not stale_evidence and source_validation.get("ok") else "low"
        else:
            answer = "No matching local evidence was found for this question."
            confidence = "low"
        gaps = [
            "LLM synthesis is intentionally not faked in this local skeleton.",
            "Default vector retrieval uses local hashed embeddings unless a model-backed provider is configured.",
        ]
        if not source_validation.get("ok"):
            gaps.append("Source dependency manifest is missing or invalid.")
        if source_changes.get("changed_sources"):
            gaps.append("Some dependency sources are marked changed; affected documents should be reviewed before relying on them.")
        retrieval = {
            "search": search,
            "evidence": evidence,
            "citations": citations,
            "source_freshness": {
                "dependency_manifest_ok": source_validation.get("ok"),
                "changed_sources": source_changes.get("changed_sources", []),
                "affected_documents": affected.get("affected_documents", []),
                "stale_evidence": stale_evidence,
            },
            "deterministic_answer": answer,
            "confidence": confidence,
            "gaps": gaps,
            "conflicts": [],
            "suggested_next_actions": [
                "Run `krail --local sources check` to refresh dependency snapshots.",
                "Run `krail --local sources affected` to inspect stale documents.",
                "Run `krail --local workflow execute source_refresh --dry-run` before dispatching any refresh agent.",
                "Register important claims in the integrity ledger before promotion.",
            ],
        }
        return ThinkRequest(
            query=query,
            limit=limit,
            mode=mode,
            requested_runner=runner,
            retrieval=retrieval,
        )

    def _deterministic_think_result(self, request: ThinkRequest) -> ThinkResult:
        retrieval = request.retrieval
        source_freshness = retrieval["source_freshness"]
        verification = {
            "ok": True,
            "checks": [
                {
                    "name": "citation_coverage",
                    "ok": bool(retrieval["citations"]) or not bool(retrieval["evidence"]),
                    "detail": "Deterministic think emits citations for each evidence hit.",
                },
                {
                    "name": "stale_evidence_check",
                    "ok": not bool(source_freshness.get("stale_evidence")),
                    "detail": "Confidence is reduced when affected documents overlap with retrieved evidence.",
                },
            ],
        }
        return ThinkResult(
            query=request.query,
            mode=request.mode,
            requested_runner=request.requested_runner,
            runner=None,
            runner_resolution=None,
            answer=retrieval["deterministic_answer"],
            evidence=retrieval["evidence"],
            citations=retrieval["citations"],
            graph_context=retrieval["search"].get("graph_context"),
            vector_hits=retrieval["search"].get("vector_hits", []),
            source_freshness=source_freshness,
            confidence=retrieval["confidence"],
            gaps=list(retrieval["gaps"]),
            conflicts=list(retrieval["conflicts"]),
            suggested_next_actions=list(retrieval["suggested_next_actions"]),
            verification=verification,
        )

    def _runner_prompt_for_think(self, request: ThinkRequest, session_result_path: str) -> str:
        retrieval = request.retrieval
        payload = {
            "query": request.query,
            "evidence": retrieval["evidence"],
            "citations": retrieval["citations"],
            "graph_context": retrieval["search"].get("graph_context"),
            "vector_hits": retrieval["search"].get("vector_hits", []),
            "source_freshness": retrieval["source_freshness"],
            "deterministic_answer": retrieval["deterministic_answer"],
            "gaps": retrieval["gaps"],
        }
        return (
            "You are the KRAIL think synthesis worker.\n\n"
            "Use only the provided project evidence unless the prompt explicitly says otherwise.\n"
            "Do not invent citations or claims beyond the evidence package.\n"
            "If the evidence is weak, say so explicitly.\n\n"
            f"Write a JSON result to `{session_result_path}` with keys:\n"
            "- answer: string\n"
            "- citations_used: list of citation refs like [1]\n"
            "- gaps: list of strings\n"
            "- conflicts: list of strings\n"
            "- suggested_next_actions: list of strings\n"
            "- unsupported_claims: list of strings\n\n"
            "Return the same JSON on stdout as well.\n\n"
            f"Think request:\n{json.dumps(payload, indent=2)}\n"
        )

    def _verify_think_result(self, request: ThinkRequest, result: ThinkResult, raw_result: dict[str, Any] | None = None) -> dict[str, Any]:
        allowed_refs = {item["ref"] for item in request.retrieval["citations"]}
        used_refs = set(self._ensure_list_of_strings((raw_result or {}).get("citations_used")))
        stale_paths = {item["path"] for item in result.source_freshness.get("stale_evidence", []) if isinstance(item, dict)}
        citation_ok = used_refs.issubset(allowed_refs) and (bool(used_refs) or not bool(result.evidence))
        unsupported_claims = self._ensure_list_of_strings((raw_result or {}).get("unsupported_claims"))
        checks = [
            {
                "name": "citation_coverage",
                "ok": citation_ok,
                "detail": "Runner output must cite only evidence refs present in the retrieval package.",
            },
            {
                "name": "unsupported_claims",
                "ok": not bool(unsupported_claims),
                "detail": "Runner output should not report unsupported claims.",
            },
            {
                "name": "stale_evidence_check",
                "ok": not bool(stale_paths),
                "detail": "Affected documents lower confidence and should trigger review before promotion.",
            },
        ]
        return {
            "ok": all(bool(item["ok"]) for item in checks),
            "checks": checks,
            "citations_used": sorted(used_refs),
            "unsupported_claims": unsupported_claims,
        }

    def _execute_workflow_think_step(
        self,
        workflow_id: str,
        step_id: str,
        step: dict[str, Any],
        *,
        dry_run: bool,
        default_runner: str,
    ) -> dict[str, Any]:
        query = str(step.get("query") or step.get("prompt") or step.get("description") or "").strip()
        if not query:
            raise ValueError(f"think step {step_id!r} requires query, prompt, or description")
        mode = str(step.get("mode") or "hybrid")
        runner = str(step.get("runner") or default_runner or "auto")
        limit = int(step.get("limit") or 5)
        think_result = self.think(query, limit=limit, mode=mode, runner=runner, dry_run=dry_run or mode != "deterministic")
        output_path = step.get("output_path")
        written_path: str | None = None
        integrity_registration: dict[str, Any] | None = None
        if isinstance(output_path, str) and output_path.strip() and not dry_run:
            target = (self.project_path / output_path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(think_result, indent=2) + "\n", encoding="utf-8")
            written_path = str(target.relative_to(self.project_path))
            if bool(step.get("register_integrity", True)):
                integrity_registration = self.register_think_result(
                    think_result,
                    artifact_path=written_path,
                    title=str(step.get("title") or step.get("query") or step_id),
                )
        payload = {
            "status": think_result.get("status", "done" if mode == "deterministic" else "failed"),
            "query": query,
            "mode": mode,
            "runner": think_result.get("runner"),
            "requested_runner": runner,
            "think": think_result,
            "workflow": workflow_id,
        }
        if written_path:
            payload["output_path"] = written_path
        if integrity_registration is not None:
            payload["integrity"] = integrity_registration
        return payload

    def _runner_think_result(
        self,
        request: ThinkRequest,
        *,
        runner: str = "auto",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        deterministic = self._deterministic_think_result(request)
        resolved = self.resolve_runner(runner, purpose="think")
        session_id = f"think_{self._slug(request.query, fallback='think')}_{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M%S')}"
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        work_order_id = f"wo_{session_id}"
        work_order = {
            "work_order_id": work_order_id,
            "kind": "think_synthesis",
            "query": request.query,
            "mode": request.mode,
            "runner": resolved["runner"],
            "requested_runner": runner,
            "runner_resolution": resolved,
            "trust": "candidate_until_reviewed",
            "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
        }
        session_result_path = str((session_dir / "session_result.json").relative_to(self.project_path))
        prompt = self._runner_prompt_for_think(request, session_result_path)
        command = self._runner_command(resolved["runner"], prompt)
        (session_dir / "think_request.json").write_text(json.dumps(request.to_dict(), indent=2) + "\n", encoding="utf-8")
        (session_dir / "work_order.json").write_text(json.dumps(work_order, indent=2) + "\n", encoding="utf-8")
        (session_dir / "command.json").write_text(json.dumps({"command": command}, indent=2) + "\n", encoding="utf-8")
        (session_dir / "session_result.template.json").write_text(
            json.dumps(
                {
                    "answer": "",
                    "citations_used": [],
                    "gaps": [],
                    "conflicts": [],
                    "suggested_next_actions": [],
                    "unsupported_claims": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload: dict[str, Any] = deterministic.to_dict()
        payload.update(
            {
                "mode": request.mode,
                "runner": resolved["runner"],
                "requested_runner": runner,
                "runner_resolution": resolved,
                "session": {
                    "session_id": session_id,
                    "session_path": str(session_dir.relative_to(self.project_path)),
                    "work_order_id": work_order_id,
                },
            }
        )
        if dry_run or not resolved.get("available"):
            if not resolved.get("available"):
                payload["gaps"] = list(payload["gaps"]) + [
                    resolved.get("warning") or f"Runner unavailable for synthesis: {resolved['runner']}.",
                ]
            payload["status"] = "dry_run" if dry_run else "blocked"
            payload["verification"] = self._verify_think_result(request, deterministic)
            return payload

        raw_result: dict[str, Any] | None = None
        started = _dt.datetime.now(_dt.UTC)
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
        result_path = session_dir / "session_result.json"
        if result_path.exists():
            try:
                raw_result = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                raw_result = None
        if raw_result is None and completed.stdout:
            try:
                raw_result = json.loads(completed.stdout)
            except Exception:
                raw_result = {
                    "answer": completed.stdout.strip(),
                    "citations_used": [],
                    "gaps": [],
                    "conflicts": [],
                    "suggested_next_actions": [],
                    "unsupported_claims": [],
                }
        if raw_result is None:
            raw_result = {
                "answer": deterministic.answer,
                "citations_used": [],
                "gaps": ["Runner did not produce structured synthesis output."],
                "conflicts": [],
                "suggested_next_actions": [],
                "unsupported_claims": [],
            }
        result = ThinkResult(
            query=request.query,
            mode=request.mode,
            requested_runner=runner,
            runner=resolved["runner"],
            runner_resolution=resolved,
            answer=str(raw_result.get("answer") or deterministic.answer),
            evidence=deterministic.evidence,
            citations=deterministic.citations,
            graph_context=deterministic.graph_context,
            vector_hits=deterministic.vector_hits,
            source_freshness=deterministic.source_freshness,
            confidence="medium" if completed.returncode == 0 else "low",
            gaps=list(deterministic.gaps) + self._ensure_list_of_strings(raw_result.get("gaps")),
            conflicts=self._ensure_list_of_strings(raw_result.get("conflicts")),
            suggested_next_actions=list(dict.fromkeys(deterministic.suggested_next_actions + self._ensure_list_of_strings(raw_result.get("suggested_next_actions")))),
            verification={},
            session={
                "session_id": session_id,
                "session_path": str(session_dir.relative_to(self.project_path)),
                "work_order_id": work_order_id,
                "started_at": started.isoformat(),
                "ended_at": _dt.datetime.now(_dt.UTC).isoformat(),
                "exit_code": completed.returncode,
            },
        )
        result.verification = self._verify_think_result(request, result, raw_result=raw_result)
        payload = result.to_dict()
        payload["status"] = "done" if completed.returncode == 0 else "failed"
        return payload

    def think(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: str = "deterministic",
        runner: str = "auto",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if mode not in {"deterministic", "runner", "hybrid"}:
            raise ValueError(f"Unknown think mode: {mode}")
        request = self._prepare_think_request(query, limit=limit, mode=mode, runner=runner)
        if mode == "deterministic":
            return self._deterministic_think_result(request).to_dict()
        result = self._runner_think_result(request, runner=runner, dry_run=dry_run)
        if mode == "hybrid" and result.get("status") in {"blocked", "failed"}:
            result["answer"] = request.retrieval["deterministic_answer"]
            result["confidence"] = "low"
        return result

    def register_think_result(
        self,
        result: dict[str, Any],
        *,
        artifact_path: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        repo = self._integrity_repo()
        artifact_candidate = Path(artifact_path)
        if artifact_candidate.is_absolute():
            try:
                artifact_rel = artifact_candidate.relative_to(self.project_path).as_posix()
            except ValueError:
                artifact_rel = artifact_candidate.as_posix()
        else:
            artifact_rel = artifact_candidate.as_posix()
        evidence_paths = [
            str(item.get("path")).strip()
            for item in (result.get("evidence") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
        verification_checks = verification.get("checks") if isinstance(verification.get("checks"), list) else []
        run_id = f"verify_think_{self._slug(title or artifact_rel, fallback='think')}_{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M%S')}"
        verification_run = repo.upsert_verification_run(
            {
                "run_id": run_id,
                "scope": str(result.get("query") or artifact_rel),
                "loop_type": "claim_evidence",
                "status": "passed" if verification.get("ok") else "failed",
                "checks": verification_checks,
                "artifacts_checked": [artifact_rel],
                "artifact_paths": [artifact_rel],
                "blockers": [
                    str(item.get("name"))
                    for item in verification_checks
                    if isinstance(item, dict) and not bool(item.get("ok"))
                ],
            }
        )
        artifact = repo.upsert_artifact_lineage(
            {
                "artifact_path": artifact_rel,
                "artifact_type": "analysis",
                "title": title or Path(artifact_rel).name,
                "promotion_state": self._think_promotion_state(result),
                "reproducibility_mode": "deterministic" if result.get("mode") == "deterministic" else "manual",
                "inputs": evidence_paths,
                "scripts": ["krail --local think"],
                "verification_commands": ["krail --local integrity status"],
                "verification_runs": [verification_run.run_id],
                "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            }
        )
        extractor = getattr(repo, "_extract_claim_candidate_texts")
        candidate_pairs = extractor(str(result.get("answer") or ""))
        if not candidate_pairs:
            answer = str(result.get("answer") or "").strip()
            if len(answer) >= 35 and "no matching local evidence" not in answer.lower():
                candidate_pairs = [(answer[:400], answer[:400])]
        registered_candidates: list[dict[str, Any]] = []
        for claim_text, snippet in candidate_pairs:
            candidate_key = f"think:{self._slug(Path(artifact_rel).stem, fallback='artifact')}:{hashlib.sha1(claim_text.encode('utf-8')).hexdigest()[:10]}"
            candidate = repo.upsert_claim_candidate(
                {
                    "candidate_key": candidate_key,
                    "claim_text": claim_text,
                    "status": "candidate",
                    "discovered_in_paths": [artifact_rel],
                    "evidence_paths": evidence_paths,
                    "snippet": snippet,
                }
            )
            registered_candidates.append(candidate.model_dump(mode="json"))
        return {
            "status": "registered",
            "artifact": artifact.model_dump(mode="json"),
            "verification_run": verification_run.model_dump(mode="json"),
            "claim_candidates": registered_candidates,
        }

    def list_think_sessions(self, *, limit: int = 20) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for session_dir in sorted(self.sessions_dir.glob("think_*"), reverse=True):
            if not session_dir.is_dir():
                continue
            try:
                sessions.append(self.get_think_session(session_dir.name, include_payload=False))
            except Exception:
                continue
        return {"sessions": sessions[:limit], "limit": limit}

    def get_think_session(self, session_id: str, *, include_payload: bool = True) -> dict[str, Any]:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            matches = sorted(self.sessions_dir.glob(f"{session_id}*"))
            if len(matches) == 1:
                session_dir = matches[0]
            else:
                raise FileNotFoundError(f"Think session not found: {session_id}")

        request_path = session_dir / "think_request.json"
        work_order_path = session_dir / "work_order.json"
        command_path = session_dir / "command.json"
        result_path = session_dir / "session_result.json"
        template_path = session_dir / "session_result.template.json"
        stdout_path = session_dir / "stdout.log"
        stderr_path = session_dir / "stderr.log"
        exit_code_path = session_dir / "exit_code.txt"

        request = json.loads(request_path.read_text(encoding="utf-8")) if request_path.exists() else {}
        work_order = json.loads(work_order_path.read_text(encoding="utf-8")) if work_order_path.exists() else {}
        command = json.loads(command_path.read_text(encoding="utf-8")) if command_path.exists() else {}
        raw_result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
        exit_code = None
        if exit_code_path.exists():
            try:
                exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
            except ValueError:
                exit_code = None
        if raw_result is not None:
            status = "done" if exit_code in {None, 0} else "failed"
        elif exit_code is not None:
            status = "failed" if exit_code != 0 else "done"
        elif template_path.exists():
            status = "prepared"
        else:
            status = "unknown"

        payload = {
            "session_id": session_dir.name,
            "status": status,
            "query": request.get("query"),
            "mode": request.get("mode"),
            "requested_runner": request.get("requested_runner"),
            "runner": work_order.get("runner"),
            "created_at": work_order.get("created_at"),
            "session_path": str(session_dir.relative_to(self.project_path)),
            "result_path": str(result_path.relative_to(self.project_path)) if result_path.exists() else None,
            "has_result": result_path.exists(),
            "exit_code": exit_code,
        }
        if include_payload:
            payload["request"] = request
            payload["work_order"] = work_order
            payload["command"] = command
            payload["result"] = raw_result
            payload["stdout"] = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
            payload["stderr"] = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        return payload

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
            f"captured_at: {_yaml_scalar(_dt.datetime.now(_dt.UTC).isoformat())}",
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

    def list_modes(self) -> dict[str, Any]:
        return {"modes": list(DEFAULT_MODES.values())}

    def show_mode(self, mode_id: str | None = None) -> dict[str, Any]:
        return get_mode(mode_id or self.active_mode()["mode"]["id"])

    def active_mode(self) -> dict[str, Any]:
        manifest = self._manifest_data()
        project = manifest.get("project") if isinstance(manifest.get("project"), dict) else {}
        configured = project.get("knowledge_mode") or project.get("brain_mode")
        if isinstance(configured, str) and configured in DEFAULT_MODES:
            return {"mode": get_mode(configured), "source": "rail.yaml"}
        active_pack = self.active_pack().get("active") or {}
        pack_id = active_pack.get("id")
        for mode_id, alias_pack in MODE_PACK_ALIASES.items():
            if pack_id == alias_pack:
                return {"mode": get_mode(mode_id), "source": ".krail/pack.yaml"}
        return {"mode": get_mode("research"), "source": "default"}

    def topic_list(self, *, include_inbox: bool = False) -> dict[str, Any]:
        topics_root = self.project_path / "topics"
        topics: list[dict[str, Any]] = []
        if not topics_root.exists():
            return {"topics": topics}
        for path in sorted(topics_root.rglob("*.md")):
            rel = path.relative_to(self.project_path).as_posix()
            if not include_inbox and rel.startswith("topics/inbox/"):
                continue
            try:
                metadata, body = self._split_markdown_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                metadata, body = {}, ""
            topics.append(
                {
                    "path": rel,
                    "title": metadata.get("title") or self._title_for(path, body),
                    "kind": metadata.get("kind") or metadata.get("type") or "document",
                    "topics": self._ensure_list_of_strings(metadata.get("topics")),
                    "entities": self._ensure_list_of_strings(metadata.get("entities")),
                    "triage_status": metadata.get("triage_status"),
                }
            )
        return {"topics": topics}

    def inbox_list(self, *, include_handled: bool = False) -> dict[str, Any]:
        inbox = self.project_path / "topics" / "inbox"
        captures: list[dict[str, Any]] = []
        if not inbox.exists():
            return {"captures": captures, "unhandled": 0, "handled": 0}
        handled_count = 0
        for path in sorted(inbox.glob("*.md")):
            try:
                metadata, body = self._split_markdown_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                metadata, body = {}, ""
            status = str(metadata.get("triage_status") or "new")
            handled = status in {"handled", "promoted", "archived"}
            if handled:
                handled_count += 1
            if handled and not include_handled:
                continue
            captures.append(
                {
                    "path": path.relative_to(self.project_path).as_posix(),
                    "title": metadata.get("title") or path.stem,
                    "kind": metadata.get("kind") or metadata.get("type") or "note",
                    "captured_at": metadata.get("captured_at"),
                    "topics": self._ensure_list_of_strings(metadata.get("topics")),
                    "entities": self._ensure_list_of_strings(metadata.get("entities")),
                    "triage_status": status,
                    "promoted_to": metadata.get("promoted_to"),
                    "snippet": self._snippet(body, []),
                }
            )
        return {"captures": captures, "unhandled": len(captures), "handled": handled_count}

    def topic_upsert(
        self,
        topic: str,
        *,
        title: str | None = None,
        kind: str = "topic",
        content: str = "",
        source_path: str | None = None,
        sources: list[str] | None = None,
        entities: list[str] | None = None,
        entity_type: str | None = None,
    ) -> dict[str, Any]:
        topic_slug = self._slug(topic, fallback="topic")
        target = self.project_path / "topics" / f"{topic_slug}.md"
        now = _dt.datetime.now(_dt.UTC).isoformat()
        existing_metadata: dict[str, Any] = {}
        existing_body = ""
        created = not target.exists()
        if target.exists():
            existing_metadata, existing_body = self._split_markdown_frontmatter(target.read_text(encoding="utf-8"))

        metadata = {
            **existing_metadata,
            "title": title or existing_metadata.get("title") or topic.replace("-", " ").replace("_", " ").title(),
            "kind": existing_metadata.get("kind") or kind,
            "topics": sorted(set([*self._ensure_list_of_strings(existing_metadata.get("topics")), topic_slug])),
            "updated_at": now,
        }
        if created:
            metadata["created_at"] = now
        merged_entities = sorted(set([*self._ensure_list_of_strings(existing_metadata.get("entities")), *(entities or [])]))
        if merged_entities:
            metadata["entities"] = merged_entities
        if entity_type and entities:
            existing_entity_meta = existing_metadata.get("entity_metadata") if isinstance(existing_metadata.get("entity_metadata"), list) else []
            seen = {str(item.get("name")) for item in existing_entity_meta if isinstance(item, dict)}
            metadata["entity_metadata"] = [
                *existing_entity_meta,
                *[
                    {"name": entity, "entity_type": entity_type}
                    for entity in entities
                    if entity not in seen
                ],
            ]

        body = existing_body.strip()
        if not body:
            mode = self.active_mode()["mode"]
            sections = mode.get("topic_types", {}).get(kind) or mode.get("topic_types", {}).get("topic") or ["summary", "key_facts", "evidence", "open_questions", "notes"]
            lines = [f"# {metadata['title']}", ""]
            for section in sections:
                heading = str(section).replace("_", " ").title()
                lines.extend([f"## {heading}", "", ""])
            body = "\n".join(lines).strip()

        entry_parts: list[str] = []
        if content.strip():
            entry_parts.append(content.strip())
        if source_path:
            entry_parts.append(f"Source capture: `{source_path}`")
        for source in sources or []:
            entry_parts.append(f"Source: {source}")
        if entry_parts:
            body = body.rstrip() + f"\n\n## Update {now[:10]}\n\n" + "\n\n".join(entry_parts).strip()

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._dump_markdown_frontmatter(metadata, body), encoding="utf-8")
        return {
            "status": "created" if created else "updated",
            "path": target.relative_to(self.project_path).as_posix(),
            "topic": topic_slug,
        }

    def inbox_promote(
        self,
        capture_path: str,
        *,
        topic: str,
        title: str | None = None,
        kind: str = "topic",
        entities: list[str] | None = None,
        entity_type: str | None = None,
    ) -> dict[str, Any]:
        source = (self.project_path / capture_path).resolve()
        try:
            source.relative_to(self.project_path.resolve())
        except ValueError as exc:
            raise ValueError("capture_path must stay inside the project") from exc
        if not source.exists():
            raise FileNotFoundError(f"capture not found: {capture_path}")
        metadata, body = self._split_markdown_frontmatter(source.read_text(encoding="utf-8"))
        rel_source = source.relative_to(self.project_path).as_posix()
        promoted = self.topic_upsert(
            topic,
            title=title,
            kind=kind,
            content=body,
            source_path=rel_source,
            sources=[metadata.get("url")] if metadata.get("url") else None,
            entities=entities or self._ensure_list_of_strings(metadata.get("entities")),
            entity_type=entity_type,
        )
        metadata["triage_status"] = "promoted"
        metadata["promoted_to"] = promoted["path"]
        metadata["triaged_at"] = _dt.datetime.now(_dt.UTC).isoformat()
        source.write_text(self._dump_markdown_frontmatter(metadata, body), encoding="utf-8")
        return {"status": "promoted", "capture": rel_source, "topic": promoted}

    @property
    def wiki_root(self) -> Path:
        return self.project_path / "docs" / "wiki"

    def _wiki_source_docs(
        self,
        *,
        source_paths: list[str] | None = None,
        include_inbox: bool = False,
    ) -> list[Path]:
        if source_paths:
            paths: list[Path] = []
            for raw in source_paths:
                path = (self.project_path / raw).resolve()
                try:
                    path.relative_to(self.project_path)
                except ValueError as exc:
                    raise ValueError("wiki source paths must stay inside the project") from exc
                if not path.exists():
                    raise FileNotFoundError(f"wiki source not found: {raw}")
                if path.suffix.lower() != ".md":
                    raise ValueError(f"wiki source must be markdown: {raw}")
                paths.append(path)
            return sorted(paths)

        topics_root = self.project_path / "topics"
        if not topics_root.exists():
            return []
        paths = []
        for path in topics_root.rglob("*.md"):
            rel = path.relative_to(self.project_path).as_posix()
            if rel.startswith("topics/inbox/") and not include_inbox:
                continue
            paths.append(path)
        return sorted(paths)

    def _wiki_target_for_source(self, source: Path) -> Path:
        rel = source.relative_to(self.project_path).as_posix()
        if rel.startswith("topics/"):
            rel = rel.removeprefix("topics/")
        else:
            rel = self._slug(rel.removesuffix(".md"), fallback=source.stem) + ".md"
        return self.wiki_root / rel

    @staticmethod
    def _markdown_headings(body: str) -> list[dict[str, Any]]:
        headings: list[dict[str, Any]] = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            marker, _, title = stripped.partition(" ")
            if 1 <= len(marker) <= 6 and set(marker) == {"#"} and title.strip():
                headings.append({"level": len(marker), "title": title.strip()})
        return headings

    @staticmethod
    def _shift_markdown_headings(body: str, *, min_level: int = 2) -> str:
        shifted: list[str] = []
        for line in body.splitlines():
            stripped = line.lstrip()
            prefix_len = len(line) - len(stripped)
            if stripped.startswith("#"):
                marker, sep, title = stripped.partition(" ")
                if sep and set(marker) == {"#"}:
                    level = max(len(marker), min_level) + 1
                    shifted.append(" " * prefix_len + "#" * min(level, 6) + " " + title)
                    continue
            shifted.append(line)
        return "\n".join(shifted).strip()

    def wiki_plan(
        self,
        *,
        source_paths: list[str] | None = None,
        include_inbox: bool = False,
    ) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        for source in self._wiki_source_docs(source_paths=source_paths, include_inbox=include_inbox):
            metadata, body = self._split_markdown_frontmatter(source.read_text(encoding="utf-8"))
            target = self._wiki_target_for_source(source)
            rel_source = source.relative_to(self.project_path).as_posix()
            rel_target = target.relative_to(self.project_path).as_posix()
            pages.append(
                {
                    "source_path": rel_source,
                    "target_path": rel_target,
                    "title": metadata.get("title") or self._title_for(source, body),
                    "kind": metadata.get("kind") or metadata.get("type") or "topic",
                    "topics": self._ensure_list_of_strings(metadata.get("topics")),
                    "entities": self._ensure_list_of_strings(metadata.get("entities")),
                    "headings": self._markdown_headings(body),
                    "will_overwrite": target.exists(),
                }
            )
        return {
            "status": "planned",
            "root": "docs/wiki",
            "pages": pages,
            "count": len(pages),
            "rich_artifacts": WIKI_RICH_ARTIFACTS,
        }

    def _render_wiki_page(
        self,
        *,
        source: Path,
        metadata: dict[str, Any],
        body: str,
    ) -> tuple[dict[str, Any], str]:
        mode = self.active_mode()["mode"]
        rel_source = source.relative_to(self.project_path).as_posix()
        title = metadata.get("title") or self._title_for(source, body)
        now = _dt.datetime.now(_dt.UTC).isoformat()
        topics = self._ensure_list_of_strings(metadata.get("topics"))
        entities = self._ensure_list_of_strings(metadata.get("entities"))
        page_metadata = {
            "title": title,
            "kind": "wiki_page",
            "source_path": rel_source,
            "source_kind": metadata.get("kind") or metadata.get("type") or "topic",
            "knowledge_mode": mode["id"],
            "generated_at": now,
            "topics": topics,
            "entities": entities,
        }
        sections = [
            f"# {title}",
            "",
            f"Generated from `{rel_source}`.",
            "",
            "## Source Notes",
            "",
            self._shift_markdown_headings(body, min_level=2) or "_No source body found._",
        ]
        related: list[str] = []
        if topics:
            related.append("Topics: " + ", ".join(f"`{topic}`" for topic in topics))
        if entities:
            related.append("Entities: " + ", ".join(f"`{entity}`" for entity in entities))
        source_refs = self._ensure_list_of_strings(metadata.get("sources"))
        if metadata.get("url"):
            source_refs.append(str(metadata["url"]))
        if source_refs:
            related.append("Sources: " + ", ".join(source_refs))
        if related:
            sections.extend(["", "## Related", "", *[f"- {item}" for item in related]])
        sections.extend(["", "## Source", "", f"- `{rel_source}`"])
        return page_metadata, "\n".join(sections).strip()

    def wiki_build(
        self,
        *,
        source_paths: list[str] | None = None,
        include_inbox: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        written: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for source in self._wiki_source_docs(source_paths=source_paths, include_inbox=include_inbox):
            target = self._wiki_target_for_source(source)
            rel_source = source.relative_to(self.project_path).as_posix()
            rel_target = target.relative_to(self.project_path).as_posix()
            if target.exists() and not force:
                skipped.append({"source_path": rel_source, "target_path": rel_target, "reason": "exists"})
                continue
            metadata, body = self._split_markdown_frontmatter(source.read_text(encoding="utf-8"))
            page_metadata, page_body = self._render_wiki_page(source=source, metadata=metadata, body=body)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self._dump_markdown_frontmatter(page_metadata, page_body), encoding="utf-8")
            written.append({"source_path": rel_source, "target_path": rel_target, "title": page_metadata["title"]})
        return {"status": "built", "root": "docs/wiki", "written": written, "skipped": skipped}

    def wiki_list(self) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        if not self.wiki_root.exists():
            return {"root": "docs/wiki", "pages": pages}
        for path in sorted(self.wiki_root.rglob("*.md")):
            metadata, body = self._split_markdown_frontmatter(path.read_text(encoding="utf-8"))
            pages.append(
                {
                    "path": path.relative_to(self.project_path).as_posix(),
                    "title": metadata.get("title") or self._title_for(path, body),
                    "source_path": metadata.get("source_path"),
                    "knowledge_mode": metadata.get("knowledge_mode"),
                    "generated_at": metadata.get("generated_at"),
                }
            )
        return {"root": "docs/wiki", "pages": pages}

    def wiki_check(self) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        pages = self.wiki_list()["pages"]
        token_re = re.compile(r"\[(AI_[A-Z_]+|WEB_IMAGE_COLLAGE|TEXTBOOK_PAGE:[^\]]+)\]")
        image_re = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
        for page in pages:
            rel = str(page["path"])
            path = self.project_path / rel
            metadata, body = self._split_markdown_frontmatter(path.read_text(encoding="utf-8"))
            source_path = metadata.get("source_path")
            if not source_path:
                errors.append(f"{rel}: missing source_path frontmatter")
            elif not (self.project_path / str(source_path)).exists():
                errors.append(f"{rel}: source_path does not exist: {source_path}")
            if not body.strip():
                errors.append(f"{rel}: empty wiki body")
            if len(body.strip()) < 120:
                warnings.append(f"{rel}: body is very short")
            leftover_tokens = sorted(set(token_re.findall(body)))
            if leftover_tokens:
                errors.append(f"{rel}: unresolved artifact tokens: {', '.join(leftover_tokens)}")
            for image_target in image_re.findall(body):
                target = image_target.strip().split()[0].strip("<>")
                if target.startswith(("http://", "https://", "#", "data:")):
                    continue
                candidate = (path.parent / target).resolve()
                try:
                    candidate.relative_to(self.project_path)
                except ValueError:
                    errors.append(f"{rel}: image target escapes project: {target}")
                    continue
                if not candidate.exists():
                    errors.append(f"{rel}: image target does not exist: {target}")
        return {"ok": not errors, "root": "docs/wiki", "pages": len(pages), "errors": errors, "warnings": warnings}

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
        active_mode = self.active_mode()
        check("knowledge_mode", bool(active_mode.get("mode")), f"active mode: {active_mode['mode']['id']} ({active_mode['source']})")
        dependency_validation = self.sources_validate()
        check(
            "source_dependencies",
            dependency_validation["ok"],
            f"{dependency_validation['sources']} sources across {dependency_validation['documents']} documents"
            if dependency_validation["ok"]
            else "; ".join(dependency_validation["errors"]),
        )
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
        inbox_state = self.inbox_list()
        warn(
            "untriaged_inbox",
            inbox_state["unhandled"] == 0,
            f"{inbox_state['unhandled']} unhandled capture(s) in topics/inbox; run `krail --local inbox list` and promote useful notes with `krail --local inbox promote`.",
        )
        try:
            graph_check = self.graph_check()
            warn(
                "markdown_graph_freshness",
                bool(graph_check.get("ok")),
                graph_check.get("message") or "markdown graph artifact is stale; run `krail --local graph build`.",
            )
        except Exception as exc:
            warn("markdown_graph_freshness", False, f"could not check markdown graph freshness: {exc}")
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
        return {
            "ok": ok,
            "checks": checks,
            "warnings": warnings,
            "workflow_validation": workflow_validation,
            "source_dependency_validation": dependency_validation,
        }

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

    def sources_validate(self) -> dict[str, Any]:
        return validate_dependency_manifest(self.project_path)

    def sources_list(self) -> dict[str, Any]:
        manifest = load_dependency_manifest(self.project_path)
        return {
            "manifest_path": manifest.get("path"),
            "sources": dependency_sources(manifest),
        }

    def sources_check(self, *, write: bool = True) -> dict[str, Any]:
        return check_sources(self.project_path, write=write)

    def sources_changed(self) -> dict[str, Any]:
        return changed_sources(self.project_path)

    def sources_affected(self, *, source_ids: list[str] | None = None) -> dict[str, Any]:
        return affected_documents(self.project_path, source_ids=source_ids)

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
      - name: Validate source dependencies
        run: krail --local sources validate
      - name: Check source snapshots
        run: krail --local sources check
      - name: Show affected documents
        run: krail --local sources affected
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

    @property
    def schedules_dir(self) -> Path:
        return self.krail_dir / "schedules"

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
        resolved = self.resolve_runner()
        return {"agents": agents, "default": resolved["runner"], "fallback_order": list(LOCAL_RUNNERS)}

    def resolve_runner(self, preferred: str | None = None, *, purpose: str | None = None) -> dict[str, Any]:
        manifest_agents = self._manifest_data().get("agents", {})
        policy = manifest_agents.get("runner_policy") if isinstance(manifest_agents.get("runner_policy"), dict) else {}
        candidates = [preferred] if preferred and preferred != "auto" else []
        if preferred in {None, "", "auto"} and purpose == "think":
            for candidate in policy.get("think_preferred") or []:
                if isinstance(candidate, str) and candidate not in candidates:
                    candidates.append(candidate)
        for candidate in policy.get("preferred") or []:
            if isinstance(candidate, str) and candidate not in candidates:
                candidates.append(candidate)
        default_runner = manifest_agents.get("default_runner")
        if isinstance(default_runner, str) and default_runner and default_runner not in candidates:
            candidates.append(default_runner)
        candidates.extend(name for name in LOCAL_RUNNERS if name not in candidates)

        checked: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate:
                continue
            if candidate not in LOCAL_RUNNERS:
                checked.append({"runner": candidate, "available": False, "reason": "unknown runner"})
                continue
            meta = LOCAL_RUNNERS[candidate]
            command = os.environ.get(meta["command_env"], meta["default_command"])
            executable = shlex.split(command)[0] if command else ""
            available = bool(executable and shutil_which(executable))
            checked.append({"runner": candidate, "command": command, "available": available})
            if available:
                return {"runner": candidate, "command": command, "available": True, "checked": checked}

        fallback = preferred if preferred and preferred in LOCAL_RUNNERS else (default_runner if isinstance(default_runner, str) and default_runner in LOCAL_RUNNERS else "codex_cli")
        meta = LOCAL_RUNNERS.get(fallback, LOCAL_RUNNERS["codex_cli"])
        command = os.environ.get(meta["command_env"], meta["default_command"])
        return {
            "runner": fallback,
            "command": command,
            "available": False,
            "checked": checked,
            "warning": "no configured local runner executable was found; dry-run work orders can still be reviewed",
        }

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
                    "read": ["rail.yaml", ".krail/pack.yaml", "research_plan", "topics", "docs", "artifacts", "agents", "skills", "specs"],
                    "write": ["research_plan", "docs/wiki", "artifacts/wiki", "agents", "skills", "specs"],
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
        runner: str = "auto",
        workflow: str | None = None,
        role: str = "research",
    ) -> dict[str, Any]:
        now = _dt.datetime.now(_dt.UTC)
        digest = hashlib.sha1(f"{title}:{description}:{now.isoformat()}".encode("utf-8")).hexdigest()[:8]
        task_id = f"task_{self._slug(title)}_{digest}"
        resolved = self.resolve_runner(runner)
        payload = {
            "id": task_id,
            "title": title,
            "description": description or title,
            "status": "ready",
            "runner": resolved["runner"],
            "requested_runner": runner,
            "runner_resolution": resolved,
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
        resolved = self.resolve_runner(str(task.get("runner") or task.get("requested_runner") or "auto"))
        return {
            "work_order_id": wo_id,
            "task_id": task["id"],
            "title": task["title"],
            "description": task.get("description") or task["title"],
            "runner": resolved["runner"],
            "requested_runner": task.get("requested_runner") or task.get("runner") or "auto",
            "runner_resolution": resolved,
            "role": role,
            "workflow": task.get("workflow"),
            "allowed_paths": ["topics", "docs", "research_plan", "artifacts", "agents", "skills", "specs"],
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
            "- Start by running or mentally following `krail --local doctor`, `krail --local mode active`, and relevant `krail --local search` commands.\n"
            "- Use `krail --local capture` for raw notes, `krail --local inbox promote` for triage, and `krail --local topic upsert` for durable knowledge updates.\n"
            "- Prefer evidence files, captures, and integrity records over unsupported claims.\n"
            "- Prefer updating existing topic pages under topics/ over creating loose daily files.\n"
            "- Keep research_plan/ for tasks, workflow state, decisions, and session summaries rather than durable domain knowledge.\n"
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

        if not work_order.get("runner_resolution", {}).get("available"):
            task["status"] = "blocked"
            task["session_id"] = session_id
            task["blocker"] = work_order.get("runner_resolution", {}).get("warning") or f"runner unavailable: {work_order['runner']}"
            task["runner_resolution"] = work_order.get("runner_resolution")
            self._write_task(task_path, task)
            return {
                "status": "blocked",
                "session_id": session_id,
                "task": task,
                "session_path": str(session_dir.relative_to(self.project_path)),
                "runner_resolution": work_order.get("runner_resolution"),
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

    def _workflow_template_for(self, workflow_id: str) -> tuple[str, dict[str, Any]] | None:
        template_id = workflow_id if workflow_id in WORKFLOW_TEMPLATES else PACK_WORKFLOW_TEMPLATE_ALIASES.get(workflow_id)
        if not template_id:
            return None
        template = json.loads(json.dumps(WORKFLOW_TEMPLATES[template_id]))
        template["id"] = workflow_id
        return template_id, template

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
            if kind not in {"command", "agent", "think"}:
                errors.append(f"step {step_id} kind must be command, agent, or think")
            if kind == "command":
                run = step.get("run")
                if not isinstance(run, str) or not run.strip():
                    errors.append(f"command step {step_id} requires run")
            if kind == "agent":
                runner = str(step.get("runner") or spec.get("runner") or "auto")
                if runner != "auto" and runner not in LOCAL_RUNNERS:
                    errors.append(f"agent step {step_id} has unknown runner: {runner}")
                role = step.get("role")
                if role is not None and not isinstance(role, str):
                    errors.append(f"agent step {step_id} role must be a string")
                prompt = step.get("prompt") or step.get("description")
                if not isinstance(prompt, str) or not prompt.strip():
                    warnings.append(f"agent step {step_id} has no prompt; generic prompt will be used")
            if kind == "think":
                mode = str(step.get("mode") or "hybrid")
                if mode not in {"deterministic", "runner", "hybrid"}:
                    errors.append(f"think step {step_id} has unknown mode: {mode}")
                runner = str(step.get("runner") or spec.get("runner") or "auto")
                if runner != "auto" and runner not in LOCAL_RUNNERS:
                    errors.append(f"think step {step_id} has unknown runner: {runner}")
                query = step.get("query") or step.get("prompt") or step.get("description")
                if not isinstance(query, str) or not query.strip():
                    errors.append(f"think step {step_id} requires query, prompt, or description")
                limit = step.get("limit")
                if limit is not None and (not isinstance(limit, int) or limit <= 0):
                    errors.append(f"think step {step_id} limit must be a positive integer")
                output_path = step.get("output_path")
                if output_path is not None and (not isinstance(output_path, str) or not output_path.strip()):
                    errors.append(f"think step {step_id} output_path must be a non-empty string when present")
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

    def schedule_install(
        self,
        workflow_id: str,
        *,
        system: str = "cron",
        schedule: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if system not in {"cron", "launchd"}:
            raise ValueError("system must be cron or launchd")
        shown = self.workflow_show(workflow_id)
        if not shown.get("materialized", True):
            return {
                "status": "not_materialized",
                "workflow": workflow_id,
                "message": "Workflow is available from the active pack or built-in templates but has not been written as a local spec.",
                "next_action": shown.get("next_action") or f"krail --local workflow init {workflow_id}",
                "template": shown.get("template"),
            }
        validation = shown.get("validation") or {}
        if not validation.get("ok"):
            return {"status": "invalid", "workflow": workflow_id, "validation": validation}
        spec = shown["workflow"]
        workflow_name = str(spec.get("id") or workflow_id)
        slug = self._slug(workflow_name)
        cron_schedule = schedule or str(spec.get("schedule") or "0 8 * * 1")
        self.schedules_dir.mkdir(parents=True, exist_ok=True)
        (self.project_path / "scripts").mkdir(parents=True, exist_ok=True)
        (self.krail_dir / "logs").mkdir(parents=True, exist_ok=True)
        wrapper = self.project_path / "scripts" / f"krail-run-{slug}.sh"
        dry_flag = " --dry-run" if dry_run else ""
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"cd {shlex.quote(str(self.project_path))}\n"
            f"exec krail --local workflow execute {shlex.quote(workflow_name)}{dry_flag} >> {shlex.quote(str(self.krail_dir / 'logs' / f'{slug}.log'))} 2>&1\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        descriptor = {
            "workflow": workflow_name,
            "system": system,
            "schedule": cron_schedule,
            "wrapper": str(wrapper.relative_to(self.project_path)),
            "dry_run": dry_run,
            "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
        }
        if system == "cron":
            cron_line = f"{cron_schedule} {shlex.quote(str(wrapper))}"
            descriptor["install_hint"] = f"(crontab -l 2>/dev/null; echo {shlex.quote(cron_line)}) | crontab -"
            (self.schedules_dir / f"{slug}.cron").write_text(cron_line + "\n", encoding="utf-8")
        else:
            label = f"local.krail.{slug}"
            plist = (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
                "<plist version=\"1.0\">\n"
                "<dict>\n"
                f"  <key>Label</key><string>{label}</string>\n"
                "  <key>ProgramArguments</key>\n"
                "  <array>\n"
                f"    <string>{wrapper}</string>\n"
                "  </array>\n"
                "  <key>StartCalendarInterval</key>\n"
                "  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>\n"
                "  <key>RunAtLoad</key><false/>\n"
                "</dict>\n"
                "</plist>\n"
            )
            plist_path = self.schedules_dir / f"{label}.plist"
            plist_path.write_text(plist, encoding="utf-8")
            descriptor["plist"] = str(plist_path.relative_to(self.project_path))
            descriptor["install_hint"] = f"launchctl load {plist_path}"
        descriptor_path = self.schedules_dir / f"{slug}.json"
        descriptor_path.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
        return {"status": "written", "path": str(descriptor_path.relative_to(self.project_path)), "schedule": descriptor}

    def schedule_list(self) -> dict[str, Any]:
        schedules = []
        for path in sorted(self.schedules_dir.glob("*.json")):
            try:
                schedules.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {"schedules": schedules}

    def schedule_remove(self, workflow_id: str) -> dict[str, Any]:
        slug = self._slug(workflow_id)
        removed: list[str] = []
        for path in [
            self.schedules_dir / f"{slug}.json",
            self.schedules_dir / f"{slug}.cron",
            self.project_path / "scripts" / f"krail-run-{slug}.sh",
        ]:
            if path.exists():
                path.unlink()
                removed.append(str(path.relative_to(self.project_path)))
        for path in self.schedules_dir.glob(f"*{slug}*.plist"):
            path.unlink()
            removed.append(str(path.relative_to(self.project_path)))
        return {"status": "removed", "workflow": workflow_id, "removed": removed}

    def workflow_list(self) -> dict[str, Any]:
        active = self.active_pack().get("active") or {}
        raw_pack_workflows = active.get("workflows") or []
        pack_workflows = [item for item in raw_pack_workflows if isinstance(item, str)]
        mode_state = self.active_mode()
        mode_id = mode_state["mode"]["id"]
        mode_workflows = [item for item in mode_state["mode"].get("workflows", []) if isinstance(item, str)]
        ignored_pack_items = [item for item in raw_pack_workflows if not isinstance(item, str)]
        spec_workflows = []
        materialized_ids: set[str] = set()
        for path in sorted(self.workflow_specs_dir.glob("*.yaml")) + sorted(self.workflow_specs_dir.glob("*.yml")):
            try:
                data = self._load_workflow_spec_file(path)
                validation = self._validate_workflow_spec(data, path=str(path.relative_to(self.project_path)))
            except Exception as exc:
                spec_workflows.append({"id": path.stem, "path": str(path.relative_to(self.project_path)), "valid": False, "error": str(exc)})
                continue
            materialized_ids.add(str(data.get("id") or path.stem))
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
        available: list[dict[str, Any]] = []
        available_ids = list(dict.fromkeys([*pack_workflows, *mode_workflows]))
        for workflow_id in available_ids:
            template = self._workflow_template_for(workflow_id)
            sources = []
            if workflow_id in pack_workflows:
                sources.append("pack")
            if workflow_id in mode_workflows:
                sources.append("mode")
            entry = {"id": workflow_id, "source": "+".join(sources) or "local", "materialized": workflow_id in materialized_ids}
            if template:
                entry["template"] = template[0]
                entry["status"] = "materialized" if entry["materialized"] else "template_available"
                entry["next_action"] = None if entry["materialized"] else f"krail --local workflow init {workflow_id}"
            else:
                entry["status"] = "materialized" if entry["materialized"] else "pack_stub"
                entry["next_action"] = None if entry["materialized"] else f"krail --local workflow init {workflow_id}"
            available.append(entry)
        result: dict[str, Any] = {
            "workflows": pack_workflows,
            "mode_workflows": mode_workflows,
            "available": available,
            "specs": spec_workflows,
            "pack": active.get("id"),
            "mode": mode_id,
        }
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
        elif inferred := self._workflow_template_for(workflow_id):
            template, spec = inferred
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
                        "runner": "auto",
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
        try:
            path = self._workflow_spec_path(workflow_id)
        except FileNotFoundError:
            template = self._workflow_template_for(workflow_id)
            active = self.active_pack().get("active") or {}
            pack_workflows = {item for item in (active.get("workflows") or []) if isinstance(item, str)}
            if template:
                template_id, spec = template
                result = {
                    "status": "template",
                    "materialized": False,
                    "path": None,
                    "workflow": spec,
                    "template": template_id,
                    "next_action": f"krail --local workflow init {workflow_id}",
                }
            elif workflow_id in pack_workflows:
                spec = {"id": workflow_id, "steps": []}
                result = {
                    "status": "pack_stub",
                    "materialized": False,
                    "path": None,
                    "workflow": spec,
                    "next_action": f"krail --local workflow init {workflow_id}",
                    "message": "This pack advertises the workflow, but no local spec or built-in template exists yet.",
                }
            else:
                raise
        else:
            spec = self._load_workflow_spec_file(path)
            result = {"status": "materialized", "materialized": True, "path": str(path.relative_to(self.project_path)), "workflow": spec}
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
        if not shown.get("materialized", True):
            return {
                "status": "not_materialized",
                "workflow": workflow_id,
                "message": "Run `krail --local workflow init` first so the workflow spec is repo-backed and reviewable.",
                "next_action": shown.get("next_action") or f"krail --local workflow init {workflow_id}",
                "template": shown.get("template"),
            }
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
                    runner = str(step.get("runner") or spec.get("runner") or "auto")
                    resolved_runner = self.resolve_runner(runner)["runner"]
                    role = str(step.get("role") or "research")
                    dispatch: dict[str, Any] = {"status": "failed"}
                    created: dict[str, Any] = {"task": {"id": ""}}
                    for attempt in range(1, attempts + 1):
                        created = self.create_task(
                            f"{spec.get('id') or workflow_id}: {step_id}",
                            description=prompt,
                            runner=resolved_runner,
                            workflow=str(spec.get("id") or workflow_id),
                            role=role,
                        )
                        dispatch = self.dispatch_task(created["task"]["id"], runner=resolved_runner, dry_run=False)
                        if dispatch.get("status") == "done":
                            break
                    step_result.update({"status": dispatch.get("status"), "task_id": created["task"]["id"], "runner": resolved_runner, "requested_runner": runner, "role": role, "dispatch": dispatch})
                    results.append(step_result)
                    if dispatch.get("status") not in {"done", "dispatched"} and on_failure == "stop":
                        break
                elif kind == "think":
                    runner = str(step.get("runner") or spec.get("runner") or "auto")
                    think_result = self._execute_workflow_think_step(
                        str(spec.get("id") or workflow_id),
                        step_id,
                        step,
                        dry_run=dry_run,
                        default_runner=runner,
                    )
                    step_result.update(think_result)
                    results.append(step_result)
                    if think_result.get("status") not in {"done", "dry_run"} and on_failure == "stop":
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

    def workflow_run(self, workflow_id: str, *, runner: str = "auto", dry_run: bool = False) -> dict[str, Any]:
        try:
            self._workflow_spec_path(workflow_id)
        except FileNotFoundError:
            pass
        else:
            return self.workflow_execute(workflow_id, dry_run=dry_run)
        active = self.active_pack().get("active") or {}
        mode_workflows = self.active_mode()["mode"].get("workflows", [])
        known = {
            *{item for item in (active.get("workflows") or []) if isinstance(item, str)},
            *{item for item in mode_workflows if isinstance(item, str)},
        }
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
