from __future__ import annotations

import datetime as _dt
import ast
import copy
import fnmatch
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from rail.listeners import ListenerEngine
from rail.manifest import load_manifest
from rail.mounts import MountRegistry
from rail.permissions import PermissionPolicy
from rail.queues import QueueEngine
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
        "workflows": ["map_codebase", "sync_recent_changes", "capture_architecture_decision", "dependency_review"],
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
6. When a topic needs a bespoke app-like reader page, write self-contained HTML under `docs/wiki/custom/` with a `krail-wiki` metadata comment so the static app can list it.
7. Keep claims grounded in the source topic, source URLs, or integrity records. Mark gaps instead of inventing.
8. Run `krail --local wiki check`, `krail --local wiki site build --force`, `krail --local wiki site check`, `krail --local graph build`, and `krail --local vector build` before finishing.

## Rich Artifact Menu

- `interactive_html`: self-contained HTML files for simulations, timelines, calculators, sortable views, or concept explorers. Use inline CSS/JS only; no network scripts or trackers.
- `custom_html_page`: self-contained HTML files under `docs/wiki/custom/` for bespoke first-class pages in the static wiki app. Include an opening `krail-wiki` metadata comment.
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
- run `krail --local wiki site build --force` and `krail --local wiki site check`
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
                "prompt": "Generate or refine docs/wiki pages from the current wiki plan. Make pages concise, source-backed, and encyclopedia-like. Use the rich_artifacts catalog from `krail --local wiki plan`: self-contained interactive HTML demos, SVG explainers, Mermaid diagrams, tables, callouts, study blocks, local image assets, generated images, and web/Google Images references are allowed when they materially improve understanding. Store reusable assets under docs/wiki/assets/<page-slug>/ or artifacts/wiki/<page-slug>/. For bespoke app-like pages, write self-contained HTML under docs/wiki/custom/ with a krail-wiki metadata comment so `krail --local wiki site build` lists it as a first-class page. Preserve source_path frontmatter, include image source URL/credit/license status when known, and do not invent unsupported claims.",
            },
            {"id": "check", "kind": "command", "run": "krail --local wiki check"},
            {"id": "site", "kind": "command", "run": "krail --local wiki site build --force && krail --local wiki site check"},
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
    "map_codebase": {
        "id": "map_codebase",
        "description": "Build a deterministic local software map before asking an agent to synthesize architecture notes.",
        "schedule": "",
        "steps": [
            {"id": "doctor", "kind": "command", "run": "krail --local doctor"},
            {"id": "repo_snapshot", "kind": "command", "run": "krail --local repo snapshot ."},
            {"id": "repo_inventory", "kind": "command", "run": "krail --local repo inventory ."},
            {"id": "repo_owners", "kind": "command", "run": "krail --local repo owners ."},
            {"id": "repo_dependencies", "kind": "command", "run": "krail --local repo dependencies ."},
            {"id": "repo_symbols", "kind": "command", "run": "krail --local repo symbols ."},
            {
                "id": "map_agent",
                "kind": "agent",
                "role": "platform",
                "runner": "auto",
                "prompt": "Use the repo inventory, ownership, dependency, symbol, and snapshot artifacts under research_plan/state/ to update service, module, API, dependency, risk, and decision topics. Record explicit gaps where ownership, interfaces, or boundaries remain unclear.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
        ],
    },
    "sync_recent_changes": {
        "id": "sync_recent_changes",
        "description": "Inspect recent repository changes and update software-map knowledge with explicit stale/gap notes.",
        "schedule": "",
        "steps": [
            {"id": "repo_changed", "kind": "command", "run": "krail --local repo changed ."},
            {"id": "repo_inventory", "kind": "command", "run": "krail --local repo inventory ."},
            {"id": "repo_symbols", "kind": "command", "run": "krail --local repo symbols ."},
            {
                "id": "change_sync",
                "kind": "agent",
                "role": "platform",
                "runner": "auto",
                "prompt": "Review recent repository changes and refresh affected software topics, decisions, risks, and blockers. Prefer updating existing topics over creating loose notes, and record what is still ambiguous.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
        ],
    },
    "capture_architecture_decision": {
        "id": "capture_architecture_decision",
        "description": "Capture an architecture decision with current repo context, dependencies, and affected modules.",
        "schedule": "",
        "steps": [
            {"id": "repo_snapshot", "kind": "command", "run": "krail --local repo snapshot ."},
            {"id": "repo_dependencies", "kind": "command", "run": "krail --local repo dependencies ."},
            {"id": "repo_symbols", "kind": "command", "run": "krail --local repo symbols ."},
            {
                "id": "decision_capture",
                "kind": "agent",
                "role": "platform",
                "runner": "auto",
                "prompt": "Capture or update an architecture decision topic. Include context, affected services/modules, evidence from source files or docs, consequences, and follow-up actions.",
            },
            {"id": "refresh_retrieval", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
        ],
    },
    "dependency_review": {
        "id": "dependency_review",
        "description": "Review dependency manifests, ownership, and recent changes for software risk and maintenance gaps.",
        "schedule": "",
        "steps": [
            {"id": "repo_dependencies", "kind": "command", "run": "krail --local repo dependencies ."},
            {"id": "repo_owners", "kind": "command", "run": "krail --local repo owners ."},
            {"id": "repo_changed", "kind": "command", "run": "krail --local repo changed ."},
            {"id": "repo_symbols", "kind": "command", "run": "krail --local repo symbols ."},
            {
                "id": "dependency_audit",
                "kind": "agent",
                "role": "doctor",
                "runner": "auto",
                "prompt": "Audit dependency manifests, ownership coverage, and recent changes. Record notable risks, stale dependencies, missing ownership, and follow-up tasks under software topics or research_plan/ as appropriate.",
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
    "map_codebase": "map_codebase",
    "sync_recent_changes": "sync_recent_changes",
    "capture_architecture_decision": "capture_architecture_decision",
    "dependency_review": "dependency_review",
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

_REPO_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "shell",
    ".sql": "sql",
}

_JS_TS_SYMBOL_RE = re.compile(
    r"(?m)^(?:export\s+)?(?:(async)\s+)?(function|class|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_JS_TS_IMPORT_RE = re.compile(
    r'(?m)^\s*import\s+(?:[^"\n]+?\s+from\s+)?["\']([^"\']+)["\'];?'
)

WIKI_RICH_ARTIFACTS: list[dict[str, Any]] = [
    {
        "id": "interactive_html",
        "label": "Self-contained interactive HTML demo",
        "where": "Use for simulations, sortable/comparable views, timelines, calculators, concept explorers, and small local demos.",
        "storage": "docs/wiki/assets/<page-slug>/<artifact-slug>.html or artifacts/wiki/<page-slug>/<artifact-slug>.html",
        "rules": ["inline CSS/JS only", "no external scripts", "no trackers", "link from the wiki page with a clear caption"],
    },
    {
        "id": "custom_html_page",
        "label": "Custom HTML wiki page",
        "where": "Use for bespoke reader pages, interactive explorers, simulators, dashboards, timelines, or layouts that should appear as first-class pages in the static wiki app.",
        "storage": "docs/wiki/custom/<page-slug>.html",
        "rules": ["self-contained HTML", "include an opening <!-- krail-wiki: {...} --> metadata comment", "preserve source_path when derived from a topic", "no trackers"],
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

    def _permission_policy(self) -> PermissionPolicy:
        return PermissionPolicy(self.project_path)

    def _mount_registry(self) -> MountRegistry:
        try:
            manifest = load_manifest(self.project_path)
        except Exception:
            return MountRegistry(self.project_path, [])
        return MountRegistry(self.project_path, manifest.mounts)

    def _resolve_mount_project(self, mount_id: str):
        if not mount_id or mount_id == "local":
            import rail

            return None, rail.local(str(self.project_path))
        registry = self._mount_registry()
        status_map = {item["id"]: item for item in registry.list_mounts().get("mounts", [])}
        status = status_map.get(mount_id)
        if not status:
            raise ValueError(f"Unknown mount: {mount_id}")
        if not status.get("ok"):
            raise RuntimeError(f"Mount unavailable: {mount_id}: {status.get('error') or 'unavailable'}")
        resolved = registry.resolve_projects([mount_id])
        if not resolved:
            raise RuntimeError(f"Mount unavailable: {mount_id}")
        return resolved[0]

    @staticmethod
    def _mount_proxy_result(result: dict[str, Any], *, mount: str | None, project_slug: str | None) -> dict[str, Any]:
        if not mount:
            return result
        payload = dict(result)
        payload["mount"] = mount
        payload["project"] = project_slug
        return payload

    def _permission_blocked_result(
        self,
        *,
        action: str,
        target: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._permission_policy().audit(action, target, "denied", reason, metadata=metadata)
        return {
            "status": "blocked",
            "permission": "denied",
            "action": action,
            "target": target,
            "reason": reason,
            "message": f"{action.replace('_', ' ')} denied for {target}: {reason}",
        }

    def _authorize_write_target(
        self,
        rel_path: str,
        *,
        metadata: dict[str, Any] | None = None,
        action: str = "write",
    ) -> dict[str, Any] | None:
        policy = self._permission_policy()
        permission_metadata = policy.metadata_for_path(rel_path, metadata)
        allowed, reason = policy.can_write(rel_path, permission_metadata)
        if not allowed:
            return self._permission_blocked_result(
                action=action,
                target=rel_path,
                reason=reason,
                metadata=permission_metadata,
            )
        if policy.requires_audit(action, True, permission_metadata):
            policy.audit(action, rel_path, "allowed", reason, metadata=permission_metadata)
        return None

    def _authorize_workflow_action(
        self,
        workflow_id: str,
        *,
        spec: dict[str, Any] | None = None,
        validation_path: str | None = None,
        action: str = "execute",
    ) -> dict[str, Any] | None:
        policy = self._permission_policy()
        permission_metadata = spec.get("permissions") if isinstance(spec, dict) and isinstance(spec.get("permissions"), dict) else {}
        if validation_path:
            permission_metadata = policy.metadata_for_path(validation_path, permission_metadata)
        workflow_target = str(spec.get("id") or workflow_id) if isinstance(spec, dict) else workflow_id
        if action == "dispatch_agent":
            allowed, reason = policy.can_dispatch_agent(workflow_target, permission_metadata)
        else:
            allowed, reason = policy.can_execute(workflow_target, permission_metadata)
        if not allowed:
            blocked = self._permission_blocked_result(
                action=action,
                target=workflow_target,
                reason=reason,
                metadata=permission_metadata,
            )
            blocked["workflow"] = workflow_id
            return blocked
        if policy.requires_audit(action, True, permission_metadata):
            policy.audit(action, workflow_target, "allowed", reason, metadata=permission_metadata)
        return None

    def _workflow_authorization_context(self, workflow_id: str) -> tuple[dict[str, Any] | None, str | None]:
        try:
            shown = self.workflow_show(workflow_id, validate=False)
        except Exception:
            return None, None
        workflow = shown.get("workflow")
        if not isinstance(workflow, dict):
            return None, None
        path = shown.get("path")
        return workflow, path if isinstance(path, str) else None

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

    def _resolve_repo_path(self, raw_path: str | Path) -> Path:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.project_path / candidate).resolve()
        try:
            resolved.relative_to(self.project_path)
        except ValueError as exc:
            raise ValueError(f"path escapes project root: {raw_path}") from exc
        return resolved

    def _path_metadata(self, path: Path) -> tuple[str, dict[str, Any]]:
        rel = path.relative_to(self.project_path).as_posix()
        metadata: dict[str, Any] = {}
        if path.suffix.lower() == ".md":
            try:
                metadata, _body = self._split_markdown_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                metadata = {}
        permission_metadata = self._permission_policy().metadata_for_path(rel, metadata)
        return rel, permission_metadata

    def _authorize_repo_path(self, path: Path) -> tuple[bool, str, str, dict[str, Any]]:
        rel, metadata = self._path_metadata(path)
        allowed, reason = self._permission_policy().can_read(rel, metadata)
        if allowed:
            if self._permission_policy().requires_audit("read", True, metadata):
                self._permission_policy().audit("read", rel, "allowed", reason, metadata=metadata)
        else:
            self._permission_policy().audit("read", rel, "denied", reason, metadata=metadata)
        return allowed, reason, rel, metadata

    def _iter_repo_files(
        self,
        roots: list[str] | None = None,
        *,
        recursive: bool = True,
        globs: list[str] | None = None,
        include_hidden: bool = False,
        text_only: bool = False,
    ) -> list[Path]:
        suffixes = {".md", ".txt", ".yaml", ".yml", ".json", ".csv"}
        raw_roots = roots or ["."]
        collected: dict[str, Path] = {}
        for raw_root in raw_roots:
            root = self._resolve_repo_path(raw_root)
            if root.is_file():
                candidates = [root]
            elif root.exists():
                iterator = root.rglob("*") if recursive else root.glob("*")
                candidates = [path for path in iterator if path.is_file()]
            else:
                continue
            for path in candidates:
                rel = path.relative_to(self.project_path).as_posix()
                if not include_hidden and any(part.startswith(".") for part in Path(rel).parts):
                    continue
                if text_only and path.suffix.lower() not in suffixes:
                    continue
                if globs and not any(fnmatch.fnmatch(rel, pattern) for pattern in globs):
                    continue
                collected[rel] = path
        return [collected[key] for key in sorted(collected)]

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
            metadata, _body = self._split_markdown_frontmatter(text) if path.suffix.lower() == ".md" else ({}, text)
            rel = str(path.relative_to(self.project_path))
            permission_metadata = self._permission_policy().metadata_for_path(rel, metadata)
            allowed, reason = self._permission_policy().can_read(rel, permission_metadata)
            if not allowed:
                self._permission_policy().audit("read", rel, "denied", reason, metadata=permission_metadata)
                continue
            if self._permission_policy().requires_audit("read", True, permission_metadata):
                self._permission_policy().audit("read", rel, "allowed", reason, metadata=permission_metadata)
            lower = text.lower()
            title = self._title_for(path, text)
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
        graph = self._filter_graph_context_for_permissions(self._graph_context(query, limit=5))
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
    def _federated_hit(hit: dict[str, Any], *, mount: str, project: str, search_weight: float) -> dict[str, Any]:
        child_path = str(hit.get("path") or hit.get("id") or "")
        mounted_path = f"{mount}:{child_path}" if child_path else mount
        score = float(hit.get("score") or 0) * float(search_weight or 1.0)
        enriched = dict(hit)
        enriched["mount"] = mount
        enriched["project"] = project
        enriched["child_path"] = child_path or None
        enriched["path"] = mounted_path
        enriched["score"] = round(score, 3)
        return enriched

    @staticmethod
    def _mount_access_result(item: dict[str, Any], access_mode: str) -> dict[str, Any]:
        if access_mode in {"delegated", "full", "chunks_with_citations"}:
            return item
        if access_mode in {"metadata_only", "summary_only"}:
            stripped = dict(item)
            for key in ("snippet", "line", "content", "record", "graph_context", "vector_hits"):
                stripped.pop(key, None)
            return stripped
        return item

    def mount_list(self) -> dict[str, Any]:
        return self._mount_registry().list_mounts()

    def federated_search(
        self,
        query: str,
        *,
        limit: int = 10,
        mounts: list[str] | None = None,
        explain: bool = False,
        rag: bool = False,
    ) -> dict[str, Any]:
        registry = self._mount_registry()
        consulted: list[str] = ["local"]
        warnings: list[dict[str, Any]] = []
        local_hits = [
            self._federated_hit(hit, mount="local", project=self.project_path.name, search_weight=1.0)
            for hit in self.search(query, limit=limit, explain=False, rag=rag).get("hits", [])
        ]
        hits = list(local_hits)
        mount_status = {item["id"]: item for item in registry.list_mounts().get("mounts", [])}
        for mount, project in registry.resolve_projects(mounts):
            if mount.access_mode == "none":
                continue
            consulted.append(mount.id)
            child_result = project._backend.knowledge.search(query, limit=limit, explain=False, rag=rag)
            for hit in child_result.get("hits", []):
                mounted = self._federated_hit(
                    hit,
                    mount=mount.id,
                    project=project.slug,
                    search_weight=mount.search_weight,
                )
                hits.append(self._mount_access_result(mounted, mount.access_mode))
        if mounts:
            requested = set(mounts)
            for mount_id in sorted(requested):
                if mount_id == "local":
                    continue
                status = mount_status.get(mount_id)
                if not status:
                    warnings.append({"mount": mount_id, "error": "unknown_mount"})
                elif not status.get("ok"):
                    warnings.append({"mount": mount_id, "error": status.get("error") or "mount_unavailable"})
        hits = sorted(hits, key=lambda item: (-float(item.get("score") or 0), item.get("path") or ""))[:limit]
        result: dict[str, Any] = {
            "query": query,
            "hits": hits,
            "summary": {"returned": len(hits), "consulted_mounts": consulted},
        }
        if warnings:
            result["warnings"] = warnings
        if explain:
            result["explain"] = {
                "mode": "federated_search",
                "consulted_mounts": consulted,
                "ranking": ["child_search_score", "mount_search_weight"],
            }
        return result

    def _filter_graph_context_for_permissions(self, graph: dict[str, Any] | None) -> dict[str, Any] | None:
        if not graph:
            return graph
        policy = self._permission_policy()
        readable_docs: list[dict[str, Any]] = []
        readable_paths: set[str] = set()
        for doc in graph.get("documents", []):
            if not isinstance(doc, dict):
                continue
            rel = str(doc.get("path") or "")
            metadata = policy.metadata_for_path(rel, doc)
            allowed, reason = policy.can_read(rel, metadata)
            if allowed:
                if policy.requires_audit("read", True, metadata):
                    policy.audit("read", rel, "allowed", reason, metadata=metadata)
                readable_docs.append(doc)
                readable_paths.add(rel)
            else:
                policy.audit("read", rel, "denied", reason, metadata=metadata)
        readable_edges = [
            edge for edge in graph.get("edges", [])
            if not isinstance(edge, dict) or not edge.get("source") or str(edge.get("source")) in readable_paths
        ]
        visible_entity_labels: set[str] = set()
        for doc in readable_docs:
            for entity in doc.get("entities", []) if isinstance(doc.get("entities"), list) else []:
                visible_entity_labels.add(str(entity))
        readable_entities = [
            entity for entity in graph.get("entities", [])
            if not isinstance(entity, dict) or not visible_entity_labels or str(entity.get("label") or "") in visible_entity_labels
        ]
        filtered = {**graph, "documents": readable_docs, "edges": readable_edges, "entities": readable_entities}
        if not filtered["documents"] and not filtered["edges"] and not filtered["entities"]:
            return None
        return filtered

    def find(
        self,
        query: str,
        *,
        limit: int = 10,
        types: list[str] | None = None,
        topic: str | None = None,
        entity: str | None = None,
        status: str | None = None,
        freshness: str | None = None,
        workflow: str | None = None,
        explain: bool = False,
        rag: bool = True,
    ) -> dict[str, Any]:
        terms = self._terms(query)
        type_filter = {item.strip().lower() for item in (types or []) if item.strip()}
        filters = {
            "types": sorted(type_filter),
            "topic": topic,
            "entity": entity,
            "status": status,
            "freshness": freshness,
            "workflow": workflow,
        }
        if not terms:
            return {
                "query": query,
                "results": [],
                "summary": {"total": 0, "by_type": {}},
                "facets": filters,
                "explain": "No searchable terms found." if explain else None,
            }

        results: list[dict[str, Any]] = []
        search_result = self.search(query, limit=max(limit, 20), explain=explain, rag=rag)
        for hit in search_result.get("hits", []):
            results.append(
                self._find_result(
                    "document",
                    hit.get("path") or hit.get("id") or "",
                    title=hit.get("title") or hit.get("path") or "Document",
                    path=hit.get("path"),
                    score=float(hit.get("score") or 0),
                    snippet=hit.get("snippet") or "",
                    matched_terms=hit.get("matched_terms") or terms,
                    record={key: value for key, value in hit.items() if key not in {"snippet"}},
                )
            )

        graph_context = search_result.get("graph_context") if isinstance(search_result.get("graph_context"), dict) else {}
        for node in graph_context.get("entities", []) if isinstance(graph_context, dict) else []:
            label = str(node.get("label") or node.get("id") or "Entity")
            results.append(
                self._find_result(
                    "entity",
                    str(node.get("id") or label),
                    title=label,
                    score=self._record_score(node, terms, base=6.0),
                    snippet=self._record_snippet(node, terms),
                    matched_terms=self._matched_terms(node, terms),
                    record=node,
                )
            )
        for edge in graph_context.get("edges", []) if isinstance(graph_context, dict) else []:
            title = f"{edge.get('from', '?')} {edge.get('type') or edge.get('label') or 'RELATED_TO'} {edge.get('to', '?')}"
            results.append(
                self._find_result(
                    "graph_edge",
                    str(edge.get("id") or title),
                    title=title,
                    path=edge.get("source"),
                    score=self._record_score(edge, terms, base=4.0),
                    snippet=self._record_snippet(edge, terms),
                    matched_terms=self._matched_terms(edge, terms),
                    record=edge,
                )
            )

        results.extend(self._find_integrity_records(terms))
        results.extend(self._find_file_records(terms))
        results = self._dedupe_find_results(results)
        results = [item for item in results if self._find_result_matches(item, type_filter=type_filter, topic=topic, entity=entity, status=status, freshness=freshness, workflow=workflow)]
        results = self._permission_policy().filter_readable(results)
        results.sort(key=lambda item: (-float(item.get("score") or 0), item.get("type") or "", item.get("path") or item.get("id") or ""))
        limited = results[:limit]
        by_type: dict[str, int] = {}
        for item in results:
            item_type = str(item.get("type") or "unknown")
            by_type[item_type] = by_type.get(item_type, 0) + 1
        envelope: dict[str, Any] = {
            "query": query,
            "results": limited,
            "summary": {"total": len(results), "returned": len(limited), "by_type": by_type},
            "facets": filters,
            "suggested_actions": self._find_suggested_actions(results),
        }
        if explain:
            envelope["explain"] = {
                "mode": "unified_local_find",
                "searched": ["documents", "markdown_graph", "integrity_records", "artifacts", "workflow_sessions", "queue_items"],
                "ranking": ["existing_search_score", "term_frequency", "title_path_status_boosts"],
                "underlying_search": search_result.get("explain"),
            }
        return envelope

    def federated_find(
        self,
        query: str,
        *,
        limit: int = 10,
        mounts: list[str] | None = None,
        types: list[str] | None = None,
        topic: str | None = None,
        entity: str | None = None,
        status: str | None = None,
        freshness: str | None = None,
        workflow: str | None = None,
        explain: bool = False,
        rag: bool = True,
    ) -> dict[str, Any]:
        registry = self._mount_registry()
        results: list[dict[str, Any]] = []
        consulted: list[str] = ["local"]
        warnings: list[dict[str, Any]] = []
        local_result = self.find(
            query,
            limit=limit,
            types=types,
            topic=topic,
            entity=entity,
            status=status,
            freshness=freshness,
            workflow=workflow,
            explain=False,
            rag=rag,
        )
        for item in local_result.get("results", []):
            results.append(self._federated_hit(item, mount="local", project=self.project_path.name, search_weight=1.0))
        mount_status = {item["id"]: item for item in registry.list_mounts().get("mounts", [])}
        for mount, project in registry.resolve_projects(mounts):
            if mount.access_mode == "none":
                continue
            consulted.append(mount.id)
            child_result = project._backend.knowledge.find(
                query,
                limit=limit,
                types=types,
                topic=topic,
                entity=entity,
                status=status,
                freshness=freshness,
                workflow=workflow,
                explain=False,
                rag=rag,
            )
            for item in child_result.get("results", []):
                mounted = self._federated_hit(
                    item,
                    mount=mount.id,
                    project=project.slug,
                    search_weight=mount.search_weight,
                )
                results.append(self._mount_access_result(mounted, mount.access_mode))
        if mounts:
            requested = set(mounts)
            for mount_id in sorted(requested):
                if mount_id == "local":
                    continue
                status_info = mount_status.get(mount_id)
                if not status_info:
                    warnings.append({"mount": mount_id, "error": "unknown_mount"})
                elif not status_info.get("ok"):
                    warnings.append({"mount": mount_id, "error": status_info.get("error") or "mount_unavailable"})
        results = sorted(results, key=lambda item: (-float(item.get("score") or 0), item.get("path") or ""))[:limit]
        by_type: dict[str, int] = {}
        for item in results:
            item_type = str(item.get("type") or "unknown")
            by_type[item_type] = by_type.get(item_type, 0) + 1
        envelope: dict[str, Any] = {
            "query": query,
            "results": results,
            "summary": {"total": len(results), "returned": len(results), "by_type": by_type, "consulted_mounts": consulted},
            "facets": {"types": types or [], "topic": topic, "entity": entity, "status": status, "freshness": freshness, "workflow": workflow},
            "suggested_actions": [],
        }
        if warnings:
            envelope["warnings"] = warnings
        if explain:
            envelope["explain"] = {
                "mode": "federated_find",
                "consulted_mounts": consulted,
                "ranking": ["child_find_score", "mount_search_weight"],
            }
        return envelope

    def grep(
        self,
        pattern: str,
        *,
        paths: list[str] | None = None,
        glob: list[str] | None = None,
        ignore_case: bool = False,
        fixed_strings: bool = False,
        word_regexp: bool = False,
        max_count: int | None = None,
    ) -> dict[str, Any]:
        flags = re.IGNORECASE if ignore_case else 0
        expression = re.escape(pattern) if fixed_strings else pattern
        if word_regexp:
            expression = rf"\b(?:{expression})\b"
        matcher = re.compile(expression, flags)
        matches: list[dict[str, Any]] = []
        searched_files = 0
        readable_files = 0
        denied_files = 0
        for path in self._iter_repo_files(paths, recursive=True, globs=glob, include_hidden=False, text_only=True):
            searched_files += 1
            allowed, _reason, rel, _metadata = self._authorize_repo_path(path)
            if not allowed:
                denied_files += 1
                continue
            readable_files += 1
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue
            for line_number, line in enumerate(lines, start=1):
                line_matches = list(matcher.finditer(line))
                if not line_matches:
                    continue
                matches.append(
                    {
                        "path": rel,
                        "line_number": line_number,
                        "line": line,
                        "match_count": len(line_matches),
                    }
                )
                if max_count is not None and len(matches) >= max_count:
                    return {
                        "pattern": pattern,
                        "matches": matches,
                        "summary": {
                            "searched_files": searched_files,
                            "readable_files": readable_files,
                            "denied_files": denied_files,
                            "returned": len(matches),
                            "truncated": True,
                        },
                    }
        return {
            "pattern": pattern,
            "matches": matches,
            "summary": {
                "searched_files": searched_files,
                "readable_files": readable_files,
                "denied_files": denied_files,
                "returned": len(matches),
                "truncated": False,
            },
        }

    def files_list(
        self,
        *,
        paths: list[str] | None = None,
        glob: list[str] | None = None,
        recursive: bool = False,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        raw_roots = paths or ["."]
        items: list[dict[str, Any]] = []
        denied = 0
        seen: set[str] = set()
        for raw_root in raw_roots:
            root = self._resolve_repo_path(raw_root)
            candidates: list[Path]
            if root.is_file():
                candidates = [root]
            elif root.exists():
                iterator = root.rglob("*") if recursive else root.glob("*")
                candidates = ([root] if root != self.project_path else []) + sorted(iterator)
            else:
                continue
            for path in candidates:
                rel = path.relative_to(self.project_path).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                if not include_hidden and any(part.startswith(".") for part in Path(rel).parts):
                    continue
                if glob and not any(fnmatch.fnmatch(rel, pattern) for pattern in glob):
                    continue
                allowed, _reason, rel, metadata = self._authorize_repo_path(path)
                if not allowed:
                    denied += 1
                    continue
                items.append(
                    {
                        "path": rel,
                        "type": "directory" if path.is_dir() else "file",
                        "size": path.stat().st_size if path.is_file() else None,
                        "visibility": metadata.get("visibility") or "public",
                    }
                )
        items.sort(key=lambda item: (item["type"] != "directory", item["path"]))
        return {"items": items, "summary": {"returned": len(items), "denied": denied}}

    def files_read(self, path: str, *, start_line: int = 1, lines: int | None = None) -> dict[str, Any]:
        target = self._resolve_repo_path(path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        allowed, reason, rel, metadata = self._authorize_repo_path(target)
        if not allowed:
            return {
                "status": "blocked",
                "permission": "denied",
                "action": "read",
                "target": rel,
                "reason": reason,
                "message": f"read denied for {rel}: {reason}",
            }
        text = target.read_text(encoding="utf-8", errors="ignore")
        all_lines = text.splitlines()
        start = max(start_line, 1)
        end = len(all_lines) if lines is None else min(len(all_lines), start + max(lines, 0) - 1)
        selected = all_lines[start - 1 : end]
        return {
            "path": rel,
            "content": "\n".join(selected) + ("\n" if selected else ""),
            "start_line": start,
            "end_line": end if selected else start - 1,
            "total_lines": len(all_lines),
            "truncated": end < len(all_lines),
            "visibility": metadata.get("visibility") or "public",
        }

    def files_stat(self, path: str) -> dict[str, Any]:
        target = self._resolve_repo_path(path)
        if not target.exists():
            raise FileNotFoundError(f"path not found: {path}")
        allowed, reason, rel, metadata = self._authorize_repo_path(target)
        if not allowed:
            return {
                "status": "blocked",
                "permission": "denied",
                "action": "read",
                "target": rel,
                "reason": reason,
                "message": f"read denied for {rel}: {reason}",
            }
        stat = target.stat()
        return {
            "path": rel,
            "type": "directory" if target.is_dir() else "file",
            "size": stat.st_size if target.is_file() else None,
            "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime, _dt.UTC).isoformat(),
            "visibility": metadata.get("visibility") or "public",
            "sensitivity": self._ensure_list_of_strings(metadata.get("sensitivity")),
        }

    def _find_integrity_records(self, terms: list[str]) -> list[dict[str, Any]]:
        loaders = [
            ("source", "load_sources", "source_key", ["title", "url_or_path", "source_key"]),
            ("claim", "load_claims", "claim_key", ["claim_text", "claim_key", "status"]),
            ("source_candidate", "load_source_candidates", "candidate_key", ["title", "url_or_path", "candidate_key", "status"]),
            ("claim_candidate", "load_claim_candidates", "candidate_key", ["claim_text", "candidate_key", "status"]),
            ("entity_candidate", "load_entity_candidates", "candidate_key", ["label", "entity_type", "candidate_key", "status"]),
            ("artifact", "load_artifact_lineage", "artifact_path", ["title", "artifact_path", "promotion_state"]),
            ("verification_run", "load_verification_runs", "run_id", ["run_id", "status", "artifact_path"]),
        ]
        results: list[dict[str, Any]] = []
        try:
            repo = self._integrity_repo()
        except Exception:
            return results
        for result_type, loader_name, id_field, title_fields in loaders:
            loader = getattr(repo, loader_name, None)
            if loader is None:
                continue
            try:
                records = loader()
            except Exception:
                continue
            for record in records:
                data = self._record_to_dict(record)
                matched = self._matched_terms(data, terms)
                if not matched:
                    continue
                record_id = str(data.get(id_field) or data.get("id") or data.get("key") or "")
                title = next((str(data.get(field)) for field in title_fields if data.get(field)), record_id or result_type)
                path = str(data.get("artifact_path") or data.get("source_path") or data.get("url_or_path") or "")
                status = data.get("status") or data.get("promotion_state") or data.get("freshness")
                freshness = data.get("freshness") or data.get("source_freshness")
                results.append(
                    self._find_result(
                        result_type,
                        record_id or f"{result_type}:{title}",
                        title=title,
                        path=path or None,
                        score=self._record_score(data, terms, base=5.0),
                        snippet=self._record_snippet(data, terms),
                        matched_terms=matched,
                        status=str(status) if status else None,
                        freshness=str(freshness) if freshness else None,
                        record=data,
                    )
                )
        return results

    def _find_file_records(self, terms: list[str]) -> list[dict[str, Any]]:
        specs = [
            ("artifact", self.project_path / "artifacts", ["*.md", "*.txt", "*.json", "*.yaml", "*.yml"]),
            ("workflow_run", self.project_path / "research_plan" / "sessions", ["result.json", "state.json", "work_order.json", "session_result.json"]),
            ("queue_item", self.project_path / "research_plan" / "queues", ["items.jsonl", "claims/*.json"]),
        ]
        results: list[dict[str, Any]] = []
        for result_type, root, patterns in specs:
            if not root.exists():
                continue
            for pattern in patterns:
                for path in sorted(root.rglob(pattern)):
                    if not path.is_file():
                        continue
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    if path.name == "items.jsonl":
                        results.extend(self._find_jsonl_records(path, terms))
                        continue
                    matched = [term for term in terms if term in text.lower()]
                    if not matched:
                        continue
                    rel = path.relative_to(self.project_path).as_posix()
                    data = self._load_json_or_text(text)
                    workflow_value = data.get("workflow") if isinstance(data, dict) else None
                    status_value = data.get("status") if isinstance(data, dict) else None
                    title = str(workflow_value or (data.get("title") if isinstance(data, dict) else None) or path.parent.name)
                    results.append(
                        self._find_result(
                            result_type,
                            rel,
                            title=title,
                            path=rel,
                            score=self._record_score(data if isinstance(data, dict) else {"text": text, "path": rel}, terms, base=3.0),
                            snippet=self._snippet(text, terms),
                            matched_terms=matched,
                            status=str(status_value) if status_value else None,
                            workflow=str(workflow_value) if workflow_value else None,
                            record=data if isinstance(data, dict) else {"path": rel},
                        )
                    )
        return results

    def _find_jsonl_records(self, path: Path, terms: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        rel = path.relative_to(self.project_path).as_posix()
        queue_id = path.parent.name
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            if not line.strip():
                continue
            data = self._load_json_or_text(line)
            matched = self._matched_terms(data, terms)
            if not matched:
                continue
            item_id = str(data.get("id") if isinstance(data, dict) else line_number)
            payload = data.get("payload") if isinstance(data, dict) and isinstance(data.get("payload"), dict) else {}
            title = str(payload.get("name") or payload.get("title") or payload.get("repo") or item_id)
            status = data.get("status") if isinstance(data, dict) else None
            results.append(
                self._find_result(
                    "queue_item",
                    f"{rel}:{line_number}",
                    title=title,
                    path=rel,
                    score=self._record_score(data, terms, base=3.5),
                    snippet=self._record_snippet(data, terms),
                    matched_terms=matched,
                    status=str(status) if status else None,
                    record={**data, "queue": queue_id} if isinstance(data, dict) else {"line": line, "queue": queue_id},
                )
            )
        return results

    @staticmethod
    def _record_to_dict(record: Any) -> dict[str, Any]:
        if hasattr(record, "model_dump"):
            dumped = record.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        if isinstance(record, dict):
            return record
        if hasattr(record, "__dict__"):
            return dict(record.__dict__)
        return {"value": record}

    @staticmethod
    def _load_json_or_text(text: str) -> Any:
        try:
            return json.loads(text)
        except Exception:
            return {"text": text}

    def _matched_terms(self, record: Any, terms: list[str]) -> list[str]:
        text = json.dumps(record, sort_keys=True, default=str).lower()
        return sorted({term for term in terms if term in text})

    def _record_score(self, record: Any, terms: list[str], *, base: float = 0.0) -> float:
        text = json.dumps(record, sort_keys=True, default=str).lower()
        matched = self._matched_terms(record, terms)
        if not matched:
            return 0.0
        score = base + len(matched) * 2
        score += sum(text.count(term) for term in matched)
        for key in ("title", "label", "claim_text", "url_or_path", "artifact_path", "path", "status", "promotion_state"):
            value = record.get(key) if isinstance(record, dict) else None
            if value and any(term in str(value).lower() for term in matched):
                score += 2
        return round(score, 3)

    def _record_snippet(self, record: Any, terms: list[str]) -> str:
        text = json.dumps(record, sort_keys=True, default=str)
        return self._snippet(text, terms)

    @staticmethod
    def _find_result(
        result_type: str,
        result_id: str,
        *,
        title: str,
        path: str | None = None,
        score: float = 0.0,
        snippet: str = "",
        matched_terms: list[str] | None = None,
        status: str | None = None,
        freshness: str | None = None,
        workflow: str | None = None,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": result_id,
            "type": result_type,
            "title": title,
            "score": round(float(score), 3),
            "snippet": snippet,
            "matched_terms": matched_terms or [],
        }
        if path:
            result["path"] = path
        if status:
            result["status"] = status
        if freshness:
            result["freshness"] = freshness
        if workflow:
            result["workflow"] = workflow
        if record is not None:
            result["record"] = record
        return result

    @staticmethod
    def _dedupe_find_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in results:
            key = (str(item.get("type") or ""), str(item.get("id") or item.get("path") or item.get("title") or ""))
            current = deduped.get(key)
            if current is None or float(item.get("score") or 0) > float(current.get("score") or 0):
                deduped[key] = item
        return list(deduped.values())

    def _find_result_matches(
        self,
        item: dict[str, Any],
        *,
        type_filter: set[str],
        topic: str | None,
        entity: str | None,
        status: str | None,
        freshness: str | None,
        workflow: str | None,
    ) -> bool:
        if type_filter and str(item.get("type") or "").lower() not in type_filter:
            return False
        haystack = json.dumps(item, sort_keys=True, default=str).lower()
        for needle in (topic, entity, status, freshness, workflow):
            if needle and needle.lower() not in haystack:
                return False
        return True

    @staticmethod
    def _find_suggested_actions(results: list[dict[str, Any]]) -> list[str]:
        actions: list[str] = []
        if any(item.get("type") in {"claim_candidate", "source_candidate", "entity_candidate"} for item in results):
            actions.append("Review candidate evidence and promote only supported records into trusted state.")
        if any(item.get("type") == "workflow_run" and item.get("status") in {"failed", "blocked", "running_or_incomplete"} for item in results):
            actions.append("Inspect matching workflow sessions with `krail --local workflow dashboard`.")
        if any(item.get("type") == "queue_item" and item.get("status") in {"failed", "reserved", "running"} for item in results):
            actions.append("Inspect matching inventory queues with `krail --local queue status <queue>`.")
        return actions

    def permissions_doctor(self) -> dict[str, Any]:
        return self._permission_policy().doctor()

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
        return self._build_think_request_from_search(query, search=search, limit=limit, mode=mode, runner=runner)

    def _build_think_request_from_search(
        self,
        query: str,
        *,
        search: dict[str, Any],
        limit: int,
        mode: str,
        runner: str,
    ) -> ThinkRequest:
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
        resolved = self._resolve_think_runner_for_session(runner, dry_run=dry_run)
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

    def _resolve_think_runner_for_session(self, runner: str, *, dry_run: bool) -> dict[str, Any]:
        if dry_run and runner not in {"", "auto"} and runner in LOCAL_RUNNERS:
            meta = LOCAL_RUNNERS[runner]
            command = os.environ.get(meta["command_env"], meta["default_command"])
            executable = shlex.split(command)[0] if command else ""
            available = bool(executable and shutil_which(executable))
            resolved: dict[str, Any] = {
                "runner": runner,
                "command": command,
                "available": available,
                "checked": [{"runner": runner, "command": command, "available": available}],
                "explicit_dry_run": True,
            }
            if not available:
                resolved["warning"] = "explicit runner executable was not found; dry-run session files were still materialized"
            return resolved
        return self.resolve_runner(runner, purpose="think")

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

    def federated_think(
        self,
        query: str,
        *,
        limit: int = 5,
        mounts: list[str] | None = None,
        mode: str = "deterministic",
        runner: str = "auto",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if mode not in {"deterministic", "runner", "hybrid"}:
            raise ValueError(f"Unknown think mode: {mode}")
        search = self.federated_search(query, limit=limit, mounts=mounts, explain=True, rag=True)
        request = self._build_think_request_from_search(query, search=search, limit=limit, mode=mode, runner=runner)
        consulted = search.get("summary", {}).get("consulted_mounts", [])
        if mode == "deterministic":
            payload = self._deterministic_think_result(request).to_dict()
        else:
            payload = self._runner_think_result(request, runner=runner, dry_run=dry_run)
            if mode == "hybrid" and payload.get("status") in {"blocked", "failed"}:
                payload["answer"] = request.retrieval["deterministic_answer"]
                payload["confidence"] = "low"
        payload["federated"] = True
        payload["consulted_mounts"] = consulted
        return payload

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
        rel_path = path.relative_to(self.project_path).as_posix()
        denied = self._authorize_write_target(rel_path)
        if denied:
            denied["type"] = kind
            return denied
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
        _skip_authorization: bool = False,
    ) -> dict[str, Any]:
        topic_slug = self._slug(topic, fallback="topic")
        target = self.project_path / "topics" / f"{topic_slug}.md"
        now = _dt.datetime.now(_dt.UTC).isoformat()
        existing_metadata: dict[str, Any] = {}
        existing_body = ""
        created = not target.exists()
        if target.exists():
            existing_metadata, existing_body = self._split_markdown_frontmatter(target.read_text(encoding="utf-8"))
        rel_target = target.relative_to(self.project_path).as_posix()
        if not _skip_authorization:
            denied = self._authorize_write_target(rel_target, metadata=existing_metadata)
            if denied:
                denied["topic"] = topic_slug
                return denied

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
        denied = self._authorize_write_target(rel_source, metadata=metadata, action="promote")
        if denied:
            denied["capture"] = rel_source
            return denied
        topic_slug = self._slug(topic, fallback="topic")
        target = self.project_path / "topics" / f"{topic_slug}.md"
        target_metadata: dict[str, Any] = {}
        if target.exists():
            target_metadata, _ = self._split_markdown_frontmatter(target.read_text(encoding="utf-8"))
        target_denied = self._authorize_write_target(
            target.relative_to(self.project_path).as_posix(),
            metadata=target_metadata,
        )
        if target_denied:
            target_denied["capture"] = rel_source
            target_denied["topic"] = topic_slug
            return target_denied
        promoted = self.topic_upsert(
            topic,
            title=title,
            kind=kind,
            content=body,
            source_path=rel_source,
            sources=[metadata.get("url")] if metadata.get("url") else None,
            entities=entities or self._ensure_list_of_strings(metadata.get("entities")),
            entity_type=entity_type,
            _skip_authorization=True,
        )
        metadata["triage_status"] = "promoted"
        metadata["promoted_to"] = promoted["path"]
        metadata["triaged_at"] = _dt.datetime.now(_dt.UTC).isoformat()
        source.write_text(self._dump_markdown_frontmatter(metadata, body), encoding="utf-8")
        return {"status": "promoted", "capture": rel_source, "topic": promoted}

    @property
    def wiki_root(self) -> Path:
        return self.project_path / "docs" / "wiki"

    @property
    def wiki_site_root(self) -> Path:
        return self.project_path / "docs" / "wiki-site"

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

    def _wiki_site_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        if self.wiki_root.exists():
            for path in sorted(self.wiki_root.rglob("*.md")):
                rel = path.relative_to(self.project_path).as_posix()
                metadata, body = self._split_markdown_frontmatter(path.read_text(encoding="utf-8"))
                title = metadata.get("title") or self._title_for(path, body)
                pages.append(
                    {
                        "id": self._slug(rel.removesuffix(".md"), fallback=path.stem),
                        "type": "markdown",
                        "path": rel,
                        "title": title,
                        "source_path": metadata.get("source_path"),
                        "knowledge_mode": metadata.get("knowledge_mode"),
                        "topics": self._ensure_list_of_strings(metadata.get("topics")),
                        "entities": self._ensure_list_of_strings(metadata.get("entities")),
                        "metadata": metadata,
                        "body": body.strip(),
                    }
                )
        custom_root = self.wiki_root / "custom"
        if custom_root.exists():
            for path in sorted(custom_root.rglob("*.html")):
                rel = path.relative_to(self.project_path).as_posix()
                text = path.read_text(encoding="utf-8")
                meta_match = re.search(r"<!--\s*krail-wiki:\s*(\{.*?\})\s*-->", text, re.DOTALL)
                metadata: dict[str, Any] = {}
                if meta_match:
                    try:
                        loaded = json.loads(meta_match.group(1))
                        if isinstance(loaded, dict):
                            metadata = loaded
                    except json.JSONDecodeError:
                        metadata = {}
                title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
                title = metadata.get("title") or (html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else path.stem.replace("-", " ").title())
                pages.append(
                    {
                        "id": self._slug(rel.removesuffix(".html"), fallback=path.stem),
                        "type": "html",
                        "path": rel,
                        "title": str(title),
                        "source_path": metadata.get("source_path"),
                        "knowledge_mode": metadata.get("knowledge_mode"),
                        "topics": self._ensure_list_of_strings(metadata.get("topics")),
                        "entities": self._ensure_list_of_strings(metadata.get("entities")),
                        "metadata": metadata,
                        "url": "../wiki/custom/" + path.relative_to(custom_root).as_posix(),
                    }
                )
        return pages

    @staticmethod
    def _wiki_site_search_index(*, pages: list[dict[str, Any]], graph: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for page in pages:
            records.append(
                {
                    "id": page["id"],
                    "type": f"page:{page['type']}",
                    "title": page["title"],
                    "path": page["path"],
                    "text": " ".join(
                        [
                            str(page.get("title") or ""),
                            str(page.get("path") or ""),
                            str(page.get("source_path") or ""),
                            " ".join(page.get("topics") or []),
                            " ".join(page.get("entities") or []),
                            str(page.get("body") or ""),
                        ]
                    ).strip(),
                }
            )
        for node in graph.get("nodes") or []:
            records.append(
                {
                    "id": node.get("id"),
                    "type": f"graph:{node.get('nodeType') or 'node'}",
                    "title": node.get("label") or node.get("id"),
                    "path": node.get("path"),
                    "text": " ".join(str(value) for value in node.values() if isinstance(value, (str, int, float))).strip(),
                }
            )
        return records

    def _write_wiki_site_app(self, site_root: Path, *, title: str) -> None:
        index = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
  <link rel="stylesheet" href="assets/krail-wiki.css">
</head>
<body>
  <div id="app">
    <aside class="sidebar">
      <div class="brand">
        <strong>{html.escape(title)}</strong>
        <span id="site-meta"></span>
      </div>
      <input id="search" type="search" placeholder="Search pages, entities, topics">
      <div id="filters" class="filters"></div>
      <nav id="page-list"></nav>
    </aside>
    <main class="content">
      <article id="page"></article>
    </main>
    <aside class="context">
      <section>
        <h2>Graph</h2>
        <canvas id="graph" width="520" height="360"></canvas>
      </section>
      <section>
        <h2>Metadata</h2>
        <dl id="metadata"></dl>
      </section>
    </aside>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
  <script src="assets/krail-wiki.js"></script>
</body>
</html>
"""
        css = """* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1d2430; background: #f7f8fb; }
#app { display: grid; grid-template-columns: 280px minmax(0, 1fr) 360px; min-height: 100vh; }
.sidebar, .context { background: #fff; border-color: #d9deea; border-style: solid; }
.sidebar { border-width: 0 1px 0 0; padding: 16px; overflow: auto; }
.context { border-width: 0 0 0 1px; padding: 16px; overflow: auto; }
.brand { display: grid; gap: 4px; margin-bottom: 14px; }
.brand span { color: #667085; font-size: 12px; }
#search { width: 100%; padding: 9px 10px; border: 1px solid #cfd6e4; border-radius: 6px; margin-bottom: 12px; }
.filters { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.filters button, #page-list button { border: 1px solid #d8deeb; background: #fff; color: #263244; border-radius: 6px; cursor: pointer; }
.filters button { padding: 4px 8px; font-size: 12px; }
.filters button.active { background: #263244; color: #fff; border-color: #263244; }
#page-list { display: grid; gap: 6px; }
#page-list button { text-align: left; padding: 8px; }
#page-list button.active { border-color: #3762d8; background: #eef3ff; }
.content { padding: 28px min(6vw, 72px); overflow: auto; }
#page { max-width: 920px; margin: 0 auto; }
#page h1 { font-size: 2rem; line-height: 1.15; margin: 0 0 18px; }
#page h2 { margin-top: 30px; border-bottom: 1px solid #e4e8f0; padding-bottom: 5px; }
#page pre { overflow: auto; padding: 14px; background: #111827; color: #f8fafc; border-radius: 6px; }
#page code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
#page table { width: 100%; border-collapse: collapse; }
#page th, #page td { border: 1px solid #dfe4ee; padding: 6px 8px; }
.custom-frame { width: 100%; min-height: 72vh; border: 1px solid #d9deea; border-radius: 6px; background: #fff; }
.node-doc { color: #3762d8; }
.node-entity { color: #047857; }
.node-topic { color: #b45309; }
.node-source { color: #7c3aed; }
canvas { width: 100%; height: auto; border: 1px solid #e1e5ee; border-radius: 6px; background: #fbfcff; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 10px; }
dt { font-weight: 650; color: #4b5563; }
dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
@media (max-width: 980px) {
  #app { grid-template-columns: 1fr; }
  .sidebar, .context { border-width: 0 0 1px 0; }
  .content { padding: 22px 16px; }
}
"""
        js = """const state = { site: {}, pages: [], graph: { nodes: [], edges: [] }, searchIndex: [], selectedType: "all", query: "", graphPositions: new Map(), selectedGraphNode: null };
const $ = (id) => document.getElementById(id);

async function boot() {
  const [siteData, pagesData, graphData, searchData] = await Promise.all([
    fetch("data/site.json").then((r) => r.json()).catch(() => ({})),
    fetch("data/pages.json").then((r) => r.json()),
    fetch("data/graph.json").then((r) => r.json()).catch(() => ({ nodes: [], edges: [] })),
    fetch("data/search-index.json").then((r) => r.json()).catch(() => ({ records: [] })),
  ]);
  state.site = siteData || {};
  state.pages = pagesData.pages || [];
  state.graph = graphData || { nodes: [], edges: [] };
  state.searchIndex = searchData.records || [];
  $("site-meta").textContent = `${state.pages.length} pages · ${(state.graph.nodes || []).length} graph nodes`;
  buildFilters();
  $("search").addEventListener("input", (event) => { state.query = event.target.value.toLowerCase(); renderList(); });
  $("graph").addEventListener("click", selectGraphNodeAt);
  renderList();
  drawGraph();
  const first = location.hash ? state.pages.find((page) => page.id === location.hash.slice(1)) : state.pages[0];
  if (first) selectPage(first.id);
}

function buildFilters() {
  const types = ["all", ...Array.from(new Set(state.pages.map((page) => page.type)))];
  $("filters").innerHTML = "";
  for (const type of types) {
    const button = document.createElement("button");
    button.textContent = type;
    button.className = type === state.selectedType ? "active" : "";
    button.addEventListener("click", () => { state.selectedType = type; buildFilters(); renderList(); });
    $("filters").appendChild(button);
  }
}

function pageMatches(page) {
  if (state.selectedType !== "all" && page.type !== state.selectedType) return false;
  if (!state.query) return true;
  const record = state.searchIndex.find((item) => item.id === page.id);
  const haystack = record ? record.text.toLowerCase() : [page.title, page.path, page.source_path, ...(page.topics || []), ...(page.entities || []), page.body || ""].join(" ").toLowerCase();
  return haystack.includes(state.query);
}

function renderList() {
  $("page-list").innerHTML = "";
  for (const page of state.pages.filter(pageMatches)) {
    const button = document.createElement("button");
    button.textContent = page.title;
    button.dataset.id = page.id;
    button.addEventListener("click", () => selectPage(page.id));
    $("page-list").appendChild(button);
  }
}

function selectPage(id) {
  const page = state.pages.find((item) => item.id === id);
  if (!page) return;
  history.replaceState(null, "", `#${page.id}`);
  document.querySelectorAll("#page-list button").forEach((button) => button.classList.toggle("active", button.dataset.id === id));
  if (page.type === "html") {
    $("page").innerHTML = `<h1>${escapeHtml(page.title)}</h1><iframe class="custom-frame" src="${escapeHtml(page.url)}" title="${escapeHtml(page.title)}"></iframe>`;
  } else {
    $("page").innerHTML = DOMPurify.sanitize(marked.parse(page.body || ""));
    $("page").querySelectorAll("pre code.language-mermaid").forEach((code) => {
      const diagram = document.createElement("div");
      diagram.className = "mermaid";
      diagram.textContent = code.textContent;
      code.closest("pre").replaceWith(diagram);
    });
    mermaid.run({ querySelector: ".mermaid" }).catch(() => {});
    if (window.renderMathInElement) renderMathInElement($("page"), { delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\\\[", right: "\\\\]", display: true },
      { left: "$", right: "$", display: false },
      { left: "\\\\(", right: "\\\\)", display: false }
    ]});
  }
  renderMetadata(page);
}

function renderMetadata(page) {
  const entries = [["Path", page.path], ["Type", page.type], ["Source", page.source_path], ["Mode", page.knowledge_mode], ["Topics", (page.topics || []).join(", ")], ["Entities", (page.entities || []).join(", ")]].filter(([, value]) => value);
  for (const [key, value] of Object.entries(page.metadata || {})) {
    if (["title", "topics", "entities", "source_path", "knowledge_mode"].includes(key)) continue;
    entries.push([key, Array.isArray(value) ? value.join(", ") : typeof value === "object" ? JSON.stringify(value) : value]);
  }
  $("metadata").innerHTML = entries.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

function drawGraph() {
  const canvas = $("graph");
  const ctx = canvas.getContext("2d");
  const nodes = (state.graph.nodes || []).slice(0, 120);
  const edges = (state.graph.edges || []).slice(0, 260);
  const byId = new Map(nodes.map((node, index) => [node.id, { ...node, x: 260 + Math.cos(index * 2.399) * (40 + index * 1.3), y: 180 + Math.sin(index * 2.399) * (35 + index), vx: 0, vy: 0 }]));
  for (let tick = 0; tick < 120; tick++) {
    for (const a of byId.values()) for (const b of byId.values()) {
      if (a === b) continue;
      const dx = a.x - b.x, dy = a.y - b.y, d2 = Math.max(80, dx * dx + dy * dy);
      a.vx += dx / d2 * 1.4; a.vy += dy / d2 * 1.4;
    }
    for (const edge of edges) {
      const a = byId.get(edge.from), b = byId.get(edge.to);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      a.vx += dx * 0.002; a.vy += dy * 0.002; b.vx -= dx * 0.002; b.vy -= dy * 0.002;
    }
    for (const node of byId.values()) {
      node.x = Math.max(12, Math.min(508, node.x + node.vx));
      node.y = Math.max(12, Math.min(348, node.y + node.vy));
      node.vx *= 0.82; node.vy *= 0.82;
    }
  }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#ccd4e1";
  for (const edge of edges) {
    const a = byId.get(edge.from), b = byId.get(edge.to);
    if (!a || !b) continue;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }
  for (const node of byId.values()) {
    ctx.fillStyle = node.nodeType === "entity" ? "#047857" : node.nodeType === "topic" ? "#b45309" : node.nodeType === "source" ? "#7c3aed" : "#3762d8";
    ctx.beginPath(); ctx.arc(node.x, node.y, node.nodeType === "document" ? 4.5 : 3.5, 0, Math.PI * 2); ctx.fill();
  }
  state.graphPositions = byId;
  if (state.selectedGraphNode && byId.has(state.selectedGraphNode)) {
    const node = byId.get(state.selectedGraphNode);
    ctx.strokeStyle = "#111827";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(node.x, node.y, 8, 0, Math.PI * 2); ctx.stroke();
    ctx.lineWidth = 1;
  }
}

function selectGraphNodeAt(event) {
  const rect = $("graph").getBoundingClientRect();
  const x = (event.clientX - rect.left) * ($("graph").width / rect.width);
  const y = (event.clientY - rect.top) * ($("graph").height / rect.height);
  let nearest = null;
  let nearestDistance = 144;
  for (const node of state.graphPositions.values()) {
    const distance = (node.x - x) ** 2 + (node.y - y) ** 2;
    if (distance < nearestDistance) {
      nearest = node;
      nearestDistance = distance;
    }
  }
  if (!nearest) return;
  state.selectedGraphNode = nearest.id;
  drawGraph();
  renderGraphMetadata(nearest);
}

function renderGraphMetadata(node) {
  const edges = (state.graph.edges || []).filter((edge) => edge.from === node.id || edge.to === node.id).slice(0, 12);
  const entries = Object.entries(node).filter(([, value]) => value !== undefined && value !== null && value !== "");
  entries.push(["degree", edges.length]);
  if (edges.length) entries.push(["links", edges.map((edge) => `${edge.type}: ${edge.from === node.id ? edge.to : edge.from}`).join("\\n")]);
  $("metadata").innerHTML = entries.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(Array.isArray(value) ? value.join(", ") : value)}</dd>`).join("");
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
boot();
"""
        (site_root / "index.html").write_text(index, encoding="utf-8")
        (site_root / "assets" / "krail-wiki.css").write_text(css, encoding="utf-8")
        (site_root / "assets" / "krail-wiki.js").write_text(js, encoding="utf-8")

    def wiki_site_build(self, *, force: bool = False, title: str | None = None) -> dict[str, Any]:
        site_root = self.wiki_site_root
        if site_root.exists() and not force:
            raise FileExistsError("docs/wiki-site already exists; pass --force to rebuild")
        if site_root.exists():
            shutil.rmtree(site_root)
        (site_root / "assets").mkdir(parents=True, exist_ok=True)
        (site_root / "data").mkdir(parents=True, exist_ok=True)
        pages = self._wiki_site_pages()
        graph = build_markdown_graph(self.project_path, write=True)
        manifest = yaml.safe_load((self.project_path / "rail.yaml").read_text(encoding="utf-8")) or {}
        project = manifest.get("project") if isinstance(manifest, dict) else {}
        site_title = title or f"{project.get('name') or 'KRAIL'} Wiki"
        mode = self.active_mode().get("mode", {}) or {}
        pack = self.active_pack().get("active") or {}
        search_index = self._wiki_site_search_index(pages=pages, graph=graph)
        site_manifest = {
            "title": site_title,
            "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "project": {
                "name": project.get("name"),
                "slug": project.get("slug"),
            },
            "knowledge_mode": {
                "id": mode.get("id"),
                "name": mode.get("name"),
            },
            "active_pack": {
                "id": pack.get("id"),
                "name": pack.get("name"),
            },
            "counts": {
                "pages": len(pages),
                "graph_nodes": len(graph.get("nodes") or []),
                "graph_edges": len(graph.get("edges") or []),
                "search_records": len(search_index),
            },
            "features": ["markdown", "mermaid", "latex", "knowledge_graph", "custom_html_pages", "metadata_browser"],
        }
        (site_root / "data" / "site.json").write_text(json.dumps(site_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (site_root / "data" / "pages.json").write_text(json.dumps({"pages": pages}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (site_root / "data" / "graph.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (site_root / "data" / "search-index.json").write_text(json.dumps({"records": search_index}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if (self.wiki_root / "assets").exists():
            shutil.copytree(self.wiki_root / "assets", site_root / "assets", dirs_exist_ok=True)
        self._write_wiki_site_app(site_root, title=site_title)
        return {
            "status": "built",
            "root": "docs/wiki-site",
            "entrypoint": "docs/wiki-site/index.html",
            "pages": len(pages),
            "graph_nodes": len(graph.get("nodes") or []),
            "graph_edges": len(graph.get("edges") or []),
            "search_records": len(search_index),
            "features": site_manifest["features"],
        }

    def wiki_site_check(self) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        site_root = self.wiki_site_root
        required = [
            "index.html",
            "assets/krail-wiki.css",
            "assets/krail-wiki.js",
            "data/site.json",
            "data/pages.json",
            "data/graph.json",
            "data/search-index.json",
        ]
        for rel in required:
            if not (site_root / rel).exists():
                errors.append(f"missing {rel}")
        pages_path = site_root / "data" / "pages.json"
        if pages_path.exists():
            try:
                payload = json.loads(pages_path.read_text(encoding="utf-8"))
                pages = payload.get("pages") if isinstance(payload, dict) else None
                if not isinstance(pages, list):
                    errors.append("data/pages.json must contain a pages list")
                elif not pages:
                    warnings.append("data/pages.json contains no pages")
                else:
                    for page in pages:
                        if not page.get("id") or not page.get("title") or not page.get("type"):
                            errors.append("data/pages.json contains a page missing id, title, or type")
                            break
            except json.JSONDecodeError as exc:
                errors.append(f"data/pages.json is invalid JSON: {exc}")
        graph_path = site_root / "data" / "graph.json"
        if graph_path.exists():
            try:
                graph = json.loads(graph_path.read_text(encoding="utf-8"))
                if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
                    errors.append("data/graph.json must contain nodes and edges lists")
            except json.JSONDecodeError as exc:
                errors.append(f"data/graph.json is invalid JSON: {exc}")
        site_path = site_root / "data" / "site.json"
        if site_path.exists():
            try:
                site = json.loads(site_path.read_text(encoding="utf-8"))
                if not site.get("title") or not isinstance(site.get("counts"), dict):
                    errors.append("data/site.json must contain title and counts")
            except json.JSONDecodeError as exc:
                errors.append(f"data/site.json is invalid JSON: {exc}")
        search_path = site_root / "data" / "search-index.json"
        if search_path.exists():
            try:
                search = json.loads(search_path.read_text(encoding="utf-8"))
                records = search.get("records") if isinstance(search, dict) else None
                if not isinstance(records, list):
                    errors.append("data/search-index.json must contain a records list")
            except json.JSONDecodeError as exc:
                errors.append(f"data/search-index.json is invalid JSON: {exc}")
        return {"ok": not errors, "root": "docs/wiki-site", "errors": errors, "warnings": warnings}

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
        listener_health = self.listener_doctor()
        check(
            "listeners",
            listener_health["ok"],
            f"{listener_health['enabled']} enabled listener(s), {len(listener_health['errors'])} error(s)",
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
            "listener_health": listener_health,
            "source_dependency_validation": dependency_validation,
        }

    def graph_build(self, *, write: bool = True) -> dict[str, Any]:
        return build_markdown_graph(self.project_path, write=write)

    def graph_summary(self) -> dict[str, Any]:
        graph = load_or_build_graph(self.project_path)
        counts = graph.get("counts") if isinstance(graph.get("counts"), dict) else {}
        return {
            "counts": counts,
            "warnings": graph.get("warnings", []),
            "exports": graph.get("written", []),
        }

    def federated_graph_summary(self, *, mounts: list[str] | None = None) -> dict[str, Any]:
        summaries: list[dict[str, Any]] = []
        local = self.graph_summary()
        summaries.append({"mount": "local", "project": self.project_path.name, **local})
        for mount, project in self._mount_registry().resolve_projects(mounts):
            child = project.graph_summary()
            summaries.append({"mount": mount.id, "project": project.slug, **child})
        return {
            "summaries": summaries,
            "summary": {
                "mounts": len(summaries),
                "documents": sum(int((item.get("counts") or {}).get("documents", 0)) for item in summaries),
                "entities": sum(int((item.get("counts") or {}).get("entities", 0)) for item in summaries),
                "edges": sum(int((item.get("counts") or {}).get("edges", 0)) for item in summaries),
            },
        }

    def graph_diff(self) -> dict[str, Any]:
        previous_path = self.project_path / "research_plan" / "graph" / "graph.json"
        previous: dict[str, Any] = {}
        if previous_path.exists():
            try:
                previous = json.loads(previous_path.read_text(encoding="utf-8"))
            except Exception:
                previous = {}
        current = build_markdown_graph(self.project_path, write=False)
        old_nodes = {str(item.get("id")) for item in previous.get("nodes", []) if isinstance(item, dict)}
        new_nodes = {str(item.get("id")) for item in current.get("nodes", []) if isinstance(item, dict)}
        old_edges = {str(item.get("id")) for item in previous.get("edges", []) if isinstance(item, dict)}
        new_edges = {str(item.get("id")) for item in current.get("edges", []) if isinstance(item, dict)}
        return {
            "nodes": {"added": sorted(new_nodes - old_nodes), "removed": sorted(old_nodes - new_nodes), "unchanged": len(old_nodes & new_nodes)},
            "edges": {"added": sorted(new_edges - old_edges), "removed": sorted(old_edges - new_edges), "unchanged": len(old_edges & new_edges)},
            "current_counts": current.get("counts", {}),
            "previous_counts": previous.get("counts", {}),
        }

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

    def _repo_state_file(self, filename: str) -> Path:
        path = self.project_path / "research_plan" / "state" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_repo_state_record(self, filename: str, repo_key: str, payload: dict[str, Any]) -> None:
        path = self._repo_state_file(filename)
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        else:
            state = {}
        if not isinstance(state, dict):
            state = {}
        repos = state.get("repos")
        if not isinstance(repos, dict):
            repos = {}
        repos[repo_key] = payload
        state["repos"] = repos
        state["updated_at"] = _dt.datetime.now(_dt.UTC).isoformat()
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def _resolve_repo_target(self, path_or_url: str) -> tuple[Path, str]:
        target = Path(path_or_url)
        if not target.is_absolute():
            target = self.project_path / target
        return target.resolve(), path_or_url

    def _repo_key(self, target: Path) -> str:
        try:
            return str(target.relative_to(self.project_path))
        except ValueError:
            return str(target)

    def _repo_git(self, target: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=target, capture_output=True, text=True)

    def _repo_walk(self, target: Path) -> list[Path]:
        ignored_dirs = {".git", ".krail", ".rail", "__pycache__", ".pytest_cache", ".venv", "node_modules", "dist", "build"}
        files: list[Path] = []
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_dirs for part in path.parts):
                continue
            files.append(path)
        return files

    def repo_snapshot(self, path_or_url: str = ".", *, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            payload = {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
            return payload
        payload: dict[str, Any] = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "exists": True,
            "is_git": False,
            "git_root": None,
            "branch": None,
            "head": None,
            "dirty": False,
            "tracked_changes": [],
            "untracked_files": [],
            "changed_files": [],
        }
        git_dir = self._repo_git(target, "rev-parse", "--git-dir")
        if git_dir.returncode == 0:
            payload["is_git"] = True
            git_root = self._repo_git(target, "rev-parse", "--show-toplevel")
            branch = self._repo_git(target, "rev-parse", "--abbrev-ref", "HEAD")
            head = self._repo_git(target, "rev-parse", "HEAD")
            status = self._repo_git(target, "status", "--porcelain=v1", "--branch", "--", ".")
            if git_root.returncode == 0:
                payload["git_root"] = git_root.stdout.strip() or None
            if branch.returncode == 0:
                payload["branch"] = branch.stdout.strip() or None
            if head.returncode == 0:
                payload["head"] = head.stdout.strip() or None
            tracked_changes: list[dict[str, str]] = []
            untracked_files: list[str] = []
            changed_files: list[str] = []
            if status.returncode == 0:
                for line in status.stdout.splitlines():
                    if line.startswith("##"):
                        continue
                    if len(line) < 4:
                        continue
                    code = line[:2]
                    rel = line[3:]
                    if " -> " in rel:
                        rel = rel.split(" -> ", 1)[1]
                    changed_files.append(rel)
                    if code == "??":
                        untracked_files.append(rel)
                    else:
                        tracked_changes.append({"path": rel, "status": code})
            payload["tracked_changes"] = tracked_changes
            payload["untracked_files"] = untracked_files
            payload["changed_files"] = sorted(dict.fromkeys(changed_files))
            payload["dirty"] = bool(changed_files)
        if write:
            self._write_repo_state_record("repo_snapshots.json", payload["repo_key"], payload)
        return payload

    def _repo_inspect_payload(self, target: Path) -> dict[str, Any]:
        markers = {
            "python": ["pyproject.toml", "requirements.txt", "setup.py"],
            "node": ["package.json", "pnpm-lock.yaml", "yarn.lock"],
            "go": ["go.mod"],
            "rust": ["Cargo.toml"],
            "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
            "docker": ["Dockerfile", "docker-compose.yml", "compose.yaml"],
        }
        files = {path.name for path in target.iterdir()} if target.is_dir() else set()
        marker_names = {item for values in markers.values() for item in values}
        frameworks = [name for name, names in markers.items() if any(marker in files for marker in names)]
        endpoint_files = []
        if target.is_dir():
            for path in self._repo_walk(target)[:500]:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                suffix = path.suffix.lower()
                rel = str(path.relative_to(target))
                if suffix == ".py" and ("@app." in text or "@router." in text or "APIRouter(" in text):
                    endpoint_files.append(rel)
                elif suffix in {".js", ".jsx", ".ts", ".tsx"} and ("router." in text or "app.get(" in text or "app.post(" in text or "createRouter(" in text):
                    endpoint_files.append(rel)
        return {
            "frameworks": frameworks,
            "manifests": sorted(files & marker_names),
            "endpoint_files": endpoint_files[:50],
        }

    def repo_inspect(self, path_or_url: str, *, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            payload = {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
            return payload
        payload = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            **self._repo_inspect_payload(target),
        }
        if write:
            self._write_repo_state_record("repo_inspection.json", payload["repo_key"], payload)
        return payload

    def repo_inventory(self, path_or_url: str = ".", *, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            return {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
        snapshot = self.repo_snapshot(path_or_url, write=False)
        inspect = self.repo_inspect(path_or_url, write=False)
        files = self._repo_walk(target)
        language_counts: dict[str, int] = {}
        top_level_paths = sorted(item.name for item in target.iterdir())[:50] if target.is_dir() else []
        docs_dirs = sorted(
            str(path.relative_to(target))
            for path in target.rglob("*")
            if path.is_dir() and path.name.lower() in {"docs", "doc", "adr", "adrs"}
        )[:50]
        test_dirs = sorted(
            str(path.relative_to(target))
            for path in target.rglob("*")
            if path.is_dir() and path.name.lower() in {"tests", "test", "__tests__"}
        )[:50]
        ci_files = sorted(
            str(path.relative_to(target))
            for path in target.glob(".github/workflows/*")
            if path.is_file()
        )[:50]
        for path in files:
            language = _REPO_LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
            if language:
                language_counts[language] = language_counts.get(language, 0) + 1
        payload = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "snapshot": snapshot,
            "frameworks": inspect.get("frameworks", []),
            "manifests": inspect.get("manifests", []),
            "endpoint_files": inspect.get("endpoint_files", []),
            "file_count": len(files),
            "dir_count": sum(1 for path in target.rglob("*") if path.is_dir()),
            "languages": dict(sorted(language_counts.items())),
            "top_level_paths": top_level_paths,
            "docs_dirs": docs_dirs,
            "test_dirs": test_dirs,
            "ci_files": ci_files,
        }
        if write:
            self._write_repo_state_record("repo_inventory.json", payload["repo_key"], payload)
        return payload

    def repo_owners(self, path_or_url: str = ".", *, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            return {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
        owner_paths = [target / ".github" / "CODEOWNERS", target / "CODEOWNERS", target / "docs" / "CODEOWNERS"]
        entries: list[dict[str, Any]] = []
        found_path: str | None = None
        for path in owner_paths:
            if not path.exists():
                continue
            found_path = str(path.relative_to(target))
            for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                entries.append({"pattern": parts[0], "owners": parts[1:], "line": line_number})
            break
        payload = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "codeowners_path": found_path,
            "entries": entries,
            "owner_count": len(entries),
        }
        if write:
            self._write_repo_state_record("repo_owners.json", payload["repo_key"], payload)
        return payload

    def repo_dependencies(self, path_or_url: str = ".", *, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            return {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
        ecosystems: list[dict[str, Any]] = []
        pyproject = target / "pyproject.toml"
        if pyproject.exists():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project_data = data.get("project") if isinstance(data.get("project"), dict) else {}
            deps = list(project_data.get("dependencies") or [])
            optional = project_data.get("optional-dependencies") if isinstance(project_data.get("optional-dependencies"), dict) else {}
            optional_groups = {name: list(values) for name, values in optional.items() if isinstance(values, list)}
            ecosystems.append({"ecosystem": "python", "path": "pyproject.toml", "dependencies": deps, "optional_groups": optional_groups})
        requirements = []
        for req_path in sorted(target.glob("requirements*.txt")):
            lines = []
            for raw_line in req_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith("-r "):
                    continue
                lines.append(line)
            requirements.append({"path": str(req_path.relative_to(target)), "dependencies": lines})
        if requirements:
            ecosystems.append({"ecosystem": "python_requirements", "files": requirements})
        package_json = target / "package.json"
        if package_json.exists():
            data = json.loads(package_json.read_text(encoding="utf-8"))
            node_groups = {}
            for key in ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"]:
                if isinstance(data.get(key), dict):
                    node_groups[key] = data[key]
            ecosystems.append({"ecosystem": "node", "path": "package.json", "groups": node_groups})
        go_mod = target / "go.mod"
        if go_mod.exists():
            go_dependencies: list[str] = []
            in_block = False
            for raw_line in go_mod.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if line.startswith("require ("):
                    in_block = True
                    continue
                if in_block and line == ")":
                    in_block = False
                    continue
                if line.startswith("require "):
                    parts = line.split()
                    if len(parts) >= 2:
                        go_dependencies.append(parts[1])
                elif in_block and line and not line.startswith("//"):
                    parts = line.split()
                    if parts:
                        go_dependencies.append(parts[0])
            ecosystems.append({"ecosystem": "go", "path": "go.mod", "dependencies": go_dependencies})
        cargo_toml = target / "Cargo.toml"
        if cargo_toml.exists():
            data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
            cargo_groups = {}
            for key in ["dependencies", "dev-dependencies", "build-dependencies"]:
                if isinstance(data.get(key), dict):
                    cargo_groups[key] = sorted(data[key].keys())
            ecosystems.append({"ecosystem": "rust", "path": "Cargo.toml", "groups": cargo_groups})
        payload = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "ecosystems": ecosystems,
        }
        if write:
            self._write_repo_state_record("repo_dependencies.json", payload["repo_key"], payload)
        return payload

    def _python_symbols_for_file(self, path: Path, *, target: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=str(path))
        symbols: list[dict[str, Any]] = []
        imports: list[str] = []
        route_decorators: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module:
                    imports.append(module)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = []
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                        attr = decorator.func.attr
                        base = decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else None
                        decorators.append(f"{base}.{attr}" if base else attr)
                        if base in {"app", "router"} and attr.lower() in {"get", "post", "put", "delete", "patch"}:
                            route_path = None
                            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                                route_path = decorator.args[0].value
                            route_decorators.append(
                                {
                                    "name": node.name,
                                    "method": attr.upper(),
                                    "path": route_path,
                                    "line": getattr(node, "lineno", None),
                                }
                            )
                    elif isinstance(decorator, ast.Name):
                        decorators.append(decorator.id)
                symbols.append(
                    {
                        "name": node.name,
                        "kind": "function",
                        "line": getattr(node, "lineno", None),
                        "async": isinstance(node, ast.AsyncFunctionDef),
                        "decorators": decorators,
                    }
                )
            elif isinstance(node, ast.ClassDef):
                bases = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        bases.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        bases.append(base.attr)
                symbols.append(
                    {
                        "name": node.name,
                        "kind": "class",
                        "line": getattr(node, "lineno", None),
                        "bases": bases,
                    }
                )
        return {
            "path": str(path.relative_to(target)),
            "language": "python",
            "symbol_count": len(symbols),
            "symbols": symbols,
            "imports": sorted(dict.fromkeys(imports)),
            "routes": route_decorators,
        }

    def _js_ts_symbols_for_file(self, path: Path, *, target: Path, language: str) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        symbols: list[dict[str, Any]] = []
        for match in _JS_TS_SYMBOL_RE.finditer(text):
            async_kw, kind, name = match.groups()
            actual_kind = "function" if kind in {"const", "let", "var"} else kind
            symbols.append(
                {
                    "name": name,
                    "kind": actual_kind,
                    "line": text[: match.start()].count("\n") + 1,
                    "async": bool(async_kw),
                }
            )
        imports = [match.group(1) for match in _JS_TS_IMPORT_RE.finditer(text)]
        routes: list[dict[str, Any]] = []
        for route_match in re.finditer(r'(?:app|router)\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']', text):
            routes.append(
                {
                    "method": route_match.group(1).upper(),
                    "path": route_match.group(2),
                    "line": text[: route_match.start()].count("\n") + 1,
                }
            )
        return {
            "path": str(path.relative_to(target)),
            "language": language,
            "symbol_count": len(symbols),
            "symbols": symbols,
            "imports": imports,
            "routes": routes,
        }

    def repo_symbols(self, path_or_url: str = ".", *, languages: list[str] | None = None, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            return {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
        selected_languages = {item.lower() for item in languages or []}
        files = self._repo_walk(target)
        extracted_files: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        total_symbols = 0
        for path in files:
            language = _REPO_LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
            if language not in {"python", "typescript", "javascript"}:
                continue
            if selected_languages and language not in selected_languages:
                continue
            try:
                if language == "python":
                    record = self._python_symbols_for_file(path, target=target)
                else:
                    record = self._js_ts_symbols_for_file(path, target=target, language=language)
            except Exception:
                continue
            extracted_files.append(record)
            counts[language] = counts.get(language, 0) + record["symbol_count"]
            total_symbols += record["symbol_count"]
        payload = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "languages": sorted(selected_languages) if selected_languages else ["javascript", "python", "typescript"],
            "files": extracted_files,
            "counts": {"files": len(extracted_files), "symbols": total_symbols, "by_language": counts},
        }
        if write:
            self._write_repo_state_record("repo_symbols.json", payload["repo_key"], payload)
        return payload

    def repo_changed(self, path_or_url: str = ".", *, base_ref: str | None = None, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            return {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
        snapshot = self.repo_snapshot(path_or_url, write=False)
        if not snapshot.get("is_git"):
            return {
                "status": "not_git",
                "target": str(target),
                "repo_key": self._repo_key(target),
                "message": "target is not a git repository",
                "snapshot": snapshot,
            }
        range_changes: list[dict[str, str]] = []
        if base_ref:
            diff = self._repo_git(target, "diff", "--name-status", f"{base_ref}...HEAD", "--", ".")
            if diff.returncode != 0:
                return {
                    "status": "invalid_base_ref",
                    "target": str(target),
                    "repo_key": self._repo_key(target),
                    "message": diff.stderr.strip() or f"could not diff against {base_ref}",
                    "snapshot": snapshot,
                }
            for line in diff.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                if not parts:
                    continue
                range_changes.append({"status": parts[0], "path": parts[-1]})
        payload = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "base_ref": base_ref,
            "snapshot": snapshot,
            "working_tree": {
                "dirty": snapshot.get("dirty", False),
                "tracked_changes": snapshot.get("tracked_changes", []),
                "untracked_files": snapshot.get("untracked_files", []),
                "changed_files": snapshot.get("changed_files", []),
            },
            "range_changes": range_changes,
        }
        payload["fingerprint"] = "sha256:" + hashlib.sha256(
            json.dumps(
                {
                    "head": snapshot.get("head"),
                    "branch": snapshot.get("branch"),
                    "base_ref": base_ref,
                    "changed_files": payload["working_tree"]["changed_files"],
                    "range_changes": range_changes,
                },
                sort_keys=True,
            )
            .encode("utf-8")
        ).hexdigest()
        if write:
            self._write_repo_state_record("repo_changes.json", payload["repo_key"], payload)
        return payload

    def repo_inspect(self, path_or_url: str, *, write: bool = True) -> dict[str, Any]:
        target, raw_target = self._resolve_repo_target(path_or_url)
        if not target.exists():
            return {
                "status": "remote_or_missing",
                "target": raw_target,
                "message": "clone/update is not implemented yet; pass a local repo path for inspection",
            }
        payload = {
            "status": "inspected",
            "target": str(target),
            "repo_key": self._repo_key(target),
            **self._repo_inspect_payload(target),
        }
        if write:
            self._write_repo_state_record("repo_inspection.json", payload["repo_key"], payload)
        return {
            **payload,
        }

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

    def mount_create_task(
        self,
        mount_id: str,
        title: str,
        *,
        description: str = "",
        runner: str = "auto",
        workflow: str | None = None,
        role: str = "research",
    ) -> dict[str, Any]:
        mount, project = self._resolve_mount_project(mount_id)
        result = project.create_task(title, description=description, runner=runner, workflow=workflow, role=role)
        return self._mount_proxy_result(result, mount=mount.id if mount else None, project_slug=project.slug)

    def list_tasks(self) -> dict[str, Any]:
        tasks = []
        for path in sorted(self.tasks_dir.glob("*.json")):
            try:
                tasks.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {"tasks": tasks}

    def mount_list_tasks(self, mount_id: str) -> dict[str, Any]:
        mount, project = self._resolve_mount_project(mount_id)
        return self._mount_proxy_result(project.list_tasks(), mount=mount.id if mount else None, project_slug=project.slug)

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
            f"- Before exiting, write a JSON result to `{work_order.get('session_result_path', 'session_result.json')}` with keys: summary, changed_files, evidence, blockers_or_gaps, suggested_next_actions, outcome, verification_requested.\n"
            "- Do not promote generated claims as verified without evidence.\n"
        )

    def dispatch_task(self, task_id: str, *, runner: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        task_path, task = self._load_task(task_id)
        if runner:
            task["runner"] = runner
        workflow_id = task.get("workflow")
        if isinstance(workflow_id, str) and workflow_id:
            workflow_spec, validation_path = self._workflow_authorization_context(workflow_id)
            denied = self._authorize_workflow_action(
                workflow_id,
                spec=workflow_spec,
                validation_path=validation_path,
                action="dispatch_agent",
            )
            if denied:
                task["status"] = "blocked"
                task["blocker"] = denied["message"]
                self._write_task(task_path, task)
                denied["task_id"] = task["id"]
                return denied
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
                    "outcome": "unchanged",
                    "verification_requested": False,
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

    def mount_dispatch_task(self, mount_id: str, task_id: str, *, runner: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        mount, project = self._resolve_mount_project(mount_id)
        result = project.dispatch_task(task_id, runner=runner, dry_run=dry_run)
        return self._mount_proxy_result(result, mount=mount.id if mount else None, project_slug=project.slug)

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

        def validate_step_list(step_list: Any, location: str, depth: int = 0) -> int:
            if not isinstance(step_list, list) or not step_list:
                errors.append(f"{location} must be a non-empty step list")
                return 0
            if depth > 8:
                errors.append(f"{location} exceeds maximum nesting depth of 8")
                return 0
            seen: set[str] = set()
            count = 0
            for index, step in enumerate(step_list, start=1):
                count += 1
                item_location = f"{location}[{index}]"
                if not isinstance(step, dict):
                    errors.append(f"{item_location} must be a mapping")
                    continue
                step_id = step.get("id")
                if not isinstance(step_id, str) or not step_id.strip():
                    errors.append(f"{item_location} id must be a non-empty string")
                    step_id = f"step_{index}"
                step_id = str(step_id)
                if step_id in seen:
                    errors.append(f"duplicate step id in {location}: {step_id}")
                seen.add(step_id)
                kind = step.get("kind", "command")
                if kind not in {"command", "agent", "think", "approval", "workflow", "if", "repeat", "foreach", "parallel"}:
                    errors.append(f"step {step_id} kind must be command, agent, think, approval, workflow, if, repeat, foreach, or parallel")
                when = step.get("when")
                if when is not None and (not isinstance(when, str) or not when.strip()):
                    errors.append(f"step {step_id} when must be a non-empty string when present")
                needs = step.get("needs")
                if needs is not None:
                    if isinstance(needs, str):
                        need_values = [needs]
                    elif isinstance(needs, list):
                        need_values = needs
                    else:
                        need_values = []
                        errors.append(f"step {step_id} needs must be a string or list of strings")
                    for need in need_values:
                        if not isinstance(need, str) or not need.strip():
                            errors.append(f"step {step_id} needs entries must be non-empty strings")
                        elif need == step_id:
                            errors.append(f"step {step_id} cannot need itself")
                        elif need not in seen:
                            warnings.append(f"step {step_id} needs {need}; DAG scheduler will wait for that step id")
                if kind == "command":
                    run = step.get("run")
                    args = step.get("args")
                    if (not isinstance(run, str) or not run.strip()) and not isinstance(args, list):
                        errors.append(f"command step {step_id} requires run or args")
                    capture = step.get("capture")
                    if capture is not None:
                        if not isinstance(capture, dict):
                            errors.append(f"command step {step_id} capture must be a mapping")
                        else:
                            if capture.get("from", "stdout") not in {"stdout", "stderr"}:
                                errors.append(f"command step {step_id} capture.from must be stdout or stderr")
                            if capture.get("format", "text") not in {"json", "text"}:
                                errors.append(f"command step {step_id} capture.format must be json or text")
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
                if kind == "approval":
                    title = step.get("title")
                    if title is not None and not isinstance(title, str):
                        errors.append(f"approval step {step_id} title must be a string")
                    subject = step.get("subject")
                    if subject is not None and not isinstance(subject, dict):
                        errors.append(f"approval step {step_id} subject must be a mapping")
                    reviewers = step.get("reviewers")
                    if reviewers is not None and not isinstance(reviewers, dict):
                        errors.append(f"approval step {step_id} reviewers must be a mapping")
                    minimum_approvals = step.get("minimum_approvals", 1)
                    if not isinstance(minimum_approvals, int) or minimum_approvals <= 0:
                        errors.append(f"approval step {step_id} minimum_approvals must be a positive integer")
                    expires_in_hours = step.get("expires_in_hours")
                    if expires_in_hours is not None and (not isinstance(expires_in_hours, int) or expires_in_hours <= 0):
                        errors.append(f"approval step {step_id} expires_in_hours must be a positive integer")
                    on_reject = step.get("on_reject", "fail")
                    if on_reject not in {"fail", "continue"}:
                        errors.append(f"approval step {step_id} on_reject must be fail or continue")
                    allowed = step.get("allow_decisions")
                    if allowed is not None:
                        if not isinstance(allowed, list) or any(item not in {"approved", "rejected", "changes_requested"} for item in allowed):
                            errors.append(f"approval step {step_id} allow_decisions must list approved, rejected, or changes_requested")
                if kind == "workflow":
                    child_workflow = step.get("workflow")
                    if not isinstance(child_workflow, str) or not child_workflow.strip():
                        errors.append(f"workflow step {step_id} requires workflow")
                    if "with" in step and not isinstance(step.get("with"), dict):
                        errors.append(f"workflow step {step_id} with must be a mapping")
                    if "expose" in step and not isinstance(step.get("expose"), dict):
                        errors.append(f"workflow step {step_id} expose must be a mapping")
                    timeout_minutes = step.get("timeout_minutes")
                    if timeout_minutes is not None and (not isinstance(timeout_minutes, int) or timeout_minutes <= 0):
                        errors.append(f"workflow step {step_id} timeout_minutes must be a positive integer")
                if kind == "if":
                    condition = step.get("condition")
                    if not isinstance(condition, str) or not condition.strip():
                        errors.append(f"if step {step_id} requires condition")
                    count += validate_step_list(step.get("then"), f"{item_location}.then", depth + 1)
                    if "else" in step:
                        count += validate_step_list(step.get("else"), f"{item_location}.else", depth + 1)
                if kind == "repeat":
                    max_iterations = step.get("max_iterations")
                    if not isinstance(max_iterations, int) or max_iterations <= 0:
                        errors.append(f"repeat step {step_id} max_iterations must be a positive integer")
                    until = step.get("until")
                    if not isinstance(until, str) or not until.strip():
                        errors.append(f"repeat step {step_id} requires until")
                    count += validate_step_list(step.get("steps"), f"{item_location}.steps", depth + 1)
                if kind == "foreach":
                    has_items = "items" in step
                    has_items_from = "items_from" in step
                    if has_items == has_items_from:
                        errors.append(f"foreach step {step_id} requires exactly one of items or items_from")
                    if has_items and not isinstance(step.get("items"), list):
                        errors.append(f"foreach step {step_id} items must be a list")
                    if has_items_from and (not isinstance(step.get("items_from"), str) or not str(step.get("items_from")).strip()):
                        errors.append(f"foreach step {step_id} items_from must be a non-empty string")
                    loop_var = step.get("as", "item")
                    if not isinstance(loop_var, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", loop_var):
                        errors.append(f"foreach step {step_id} as must be a valid variable name")
                    max_items = step.get("max_items")
                    if not isinstance(max_items, int) or max_items <= 0:
                        errors.append(f"foreach step {step_id} max_items must be a positive integer")
                    count += validate_step_list(step.get("steps"), f"{item_location}.steps", depth + 1)
                if kind == "parallel":
                    branches = step.get("branches")
                    if not isinstance(branches, list) or not branches:
                        errors.append(f"parallel step {step_id} branches must be a non-empty list")
                    else:
                        branch_ids: set[str] = set()
                        for branch_index, branch in enumerate(branches, start=1):
                            branch_location = f"{item_location}.branches[{branch_index}]"
                            if not isinstance(branch, dict):
                                errors.append(f"{branch_location} must be a mapping")
                                continue
                            branch_id = branch.get("id")
                            if not isinstance(branch_id, str) or not branch_id.strip():
                                errors.append(f"{branch_location} id must be a non-empty string")
                                branch_id = f"branch_{branch_index}"
                            if branch_id in branch_ids:
                                errors.append(f"duplicate parallel branch id in step {step_id}: {branch_id}")
                            branch_ids.add(str(branch_id))
                            if "read_only" in branch and not isinstance(branch.get("read_only"), bool):
                                errors.append(f"parallel branch {branch_id} read_only must be boolean")
                            workspace = branch.get("workspace")
                            if workspace is not None and not isinstance(workspace, dict):
                                errors.append(f"parallel branch {branch_id} workspace must be a mapping")
                            resources = branch.get("resources")
                            if resources is not None and not isinstance(resources, dict):
                                errors.append(f"parallel branch {branch_id} resources must be a mapping")
                            count += validate_step_list(branch.get("steps"), f"{branch_location}.steps", depth + 1)
                    max_parallel = step.get("max_parallel", len(branches) if isinstance(branches, list) else 1)
                    if not isinstance(max_parallel, int) or max_parallel <= 0 or max_parallel > 8:
                        errors.append(f"parallel step {step_id} max_parallel must be an integer from 1 to 8")
                    fail_fast = step.get("fail_fast", False)
                    if not isinstance(fail_fast, bool):
                        errors.append(f"parallel step {step_id} fail_fast must be boolean")
                    minimum_successful = step.get("minimum_successful")
                    if minimum_successful is not None and (not isinstance(minimum_successful, int) or minimum_successful <= 0):
                        errors.append(f"parallel step {step_id} minimum_successful must be a positive integer")
                on_failure = step.get("on_failure", "stop")
                if on_failure not in {"stop", "continue"}:
                    errors.append(f"step {step_id} on_failure must be stop or continue")
                retry = step.get("retry", 0)
                if isinstance(retry, dict):
                    max_attempts = retry.get("max_attempts", retry.get("attempts", 1))
                    backoff_seconds = retry.get("backoff_seconds", 0)
                    if not isinstance(max_attempts, int) or max_attempts <= 0:
                        errors.append(f"step {step_id} retry.max_attempts must be a positive integer")
                    if not isinstance(backoff_seconds, int) or backoff_seconds < 0:
                        errors.append(f"step {step_id} retry.backoff_seconds must be a non-negative integer")
                elif not isinstance(retry, int) or retry < 0:
                    errors.append(f"step {step_id} retry must be a non-negative integer or retry policy mapping")
                timeout_minutes = step.get("timeout_minutes")
                if timeout_minutes is not None and (not isinstance(timeout_minutes, int) or timeout_minutes <= 0):
                    errors.append(f"step {step_id} timeout_minutes must be a positive integer")
                timeout_seconds = step.get("timeout_seconds")
                if timeout_seconds is not None and (not isinstance(timeout_seconds, int) or timeout_seconds <= 0):
                    errors.append(f"step {step_id} timeout_seconds must be a positive integer")
            return count

        step_count = validate_step_list(steps, "steps")
        dag_errors = self._validate_workflow_needs_graph(steps)
        errors.extend(dag_errors)
        return {"ok": not errors, "id": workflow_id, "path": path, "errors": errors, "warnings": warnings, "steps": step_count}

    @staticmethod
    def _validate_workflow_needs_graph(steps: list[dict[str, Any]]) -> list[str]:
        ids = [str(step.get("id")) for step in steps if isinstance(step, dict) and isinstance(step.get("id"), str)]
        id_set = set(ids)
        graph: dict[str, list[str]] = {}
        errors: list[str] = []
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("id"), str):
                continue
            step_id = str(step["id"])
            raw_needs = step.get("needs", [])
            needs = [raw_needs] if isinstance(raw_needs, str) else raw_needs
            if not isinstance(needs, list):
                continue
            graph[step_id] = []
            for need in needs:
                if not isinstance(need, str) or not need.strip():
                    continue
                if need not in id_set:
                    errors.append(f"step {step_id} needs unknown step: {need}")
                else:
                    graph[step_id].append(need)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, stack: list[str]) -> None:
            if node in visited:
                return
            if node in visiting:
                cycle = " -> ".join([*stack, node])
                errors.append(f"workflow needs cycle detected: {cycle}")
                return
            visiting.add(node)
            for dep in graph.get(node, []):
                visit(dep, [*stack, node])
            visiting.remove(node)
            visited.add(node)

        for step_id in ids:
            visit(step_id, [])
        return errors

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

    def workflow_dashboard(self, *, limit: int = 50) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for session_dir in sorted(self.sessions_dir.glob("*"), reverse=True):
            if not session_dir.is_dir():
                continue
            result_path = session_dir / "result.json"
            state_path = session_dir / "state.json"
            work_order_path = session_dir / "work_order.json"
            row: dict[str, Any] = {"session_id": session_dir.name, "path": str(session_dir.relative_to(self.project_path))}
            if result_path.exists():
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    result = {"status": "unreadable", "error": str(exc)}
                row.update(
                    {
                        "kind": "workflow",
                        "workflow": result.get("workflow"),
                        "status": result.get("status"),
                        "started_at": result.get("started_at"),
                        "ended_at": result.get("ended_at"),
                        "failed_step": result.get("failed_step"),
                        "result_present": True,
                        "schema_errors": result.get("schema_errors") or [],
                    }
                )
            elif work_order_path.exists():
                try:
                    work_order = json.loads(work_order_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    work_order = {"status": "unreadable", "error": str(exc)}
                result_file = session_dir / "session_result.json"
                row.update(
                    {
                        "kind": "agent",
                        "workflow": work_order.get("workflow"),
                        "task_id": work_order.get("task_id"),
                        "status": "done" if result_file.exists() else "running_or_incomplete",
                        "result_present": result_file.exists(),
                        "work_order": str(work_order_path.relative_to(self.project_path)),
                    }
                )
            elif state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    state = {}
                row.update({"kind": "workflow", "workflow": state.get("workflow"), "status": state.get("status"), "result_present": False})
            else:
                continue
            last_logs = sorted([*session_dir.glob("*.stdout.log"), *session_dir.glob("*.stderr.log")], key=lambda p: p.stat().st_mtime if p.exists() else 0)
            if last_logs:
                try:
                    lines = last_logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
                    row["last_log"] = lines[-1] if lines else ""
                    row["last_log_path"] = str(last_logs[-1].relative_to(self.project_path))
                except Exception:
                    pass
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
            rows.append(row)
            if len(rows) >= limit:
                break
        return {"sessions": rows, "counts": counts, "limit": limit}

    def _workflow_run_dir(self, run_id: str) -> Path:
        candidates = [self.sessions_dir / run_id, self.sessions_dir / self._slug(run_id)]
        for path in candidates:
            if (path / "result.json").exists() or (path / "state.json").exists():
                return path
        matches = sorted(self.sessions_dir.glob(f"{run_id}*"))
        for path in matches:
            if (path / "result.json").exists() or (path / "state.json").exists():
                return path
        raise FileNotFoundError(f"Workflow run not found: {run_id}")

    def _record_saved_workflow_results(self, context: dict[str, Any], results: list[dict[str, Any]]) -> None:
        for result in results:
            self._record_workflow_result(context, result)
            for key in ("steps", "then", "else"):
                child = result.get(key)
                if isinstance(child, list):
                    self._record_saved_workflow_results(context, child)
            iterations = result.get("iteration_results")
            if isinstance(iterations, list):
                for iteration in iterations:
                    child_steps = iteration.get("steps") if isinstance(iteration, dict) else None
                    if isinstance(child_steps, list):
                        self._record_saved_workflow_results(context, child_steps)
            branch_results = result.get("branch_results")
            if isinstance(branch_results, list):
                for branch in branch_results:
                    child_steps = branch.get("steps") if isinstance(branch, dict) else None
                    if isinstance(child_steps, list):
                        self._record_saved_workflow_results(context, child_steps)

    def _write_workflow_state(
        self,
        run_dir: Path,
        *,
        run_id: str,
        workflow_id: str,
        status: str,
        results: list[dict[str, Any]],
        next_step_index: int | None = None,
        pending_approval_id: str | None = None,
        workflow_digest: str | None = None,
    ) -> dict[str, Any]:
        state = {
            "run_id": run_id,
            "workflow": workflow_id,
            "status": status,
            "next_step_index": next_step_index,
            "pending_approval_id": pending_approval_id,
            "completed_steps": [str(item.get("id")) for item in results if item.get("status") in {"done", "skipped", "dry_run"}],
            "workflow_digest": workflow_digest,
            "updated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        }
        (run_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        return state

    @property
    def approval_root(self) -> Path:
        planner = self._manifest_data().get("planner")
        rel = "research_plan/approvals"
        if isinstance(planner, dict) and isinstance(planner.get("approval_root"), str) and planner["approval_root"].strip():
            rel = planner["approval_root"].strip()
        return self.project_path / rel

    def _approval_path(self, approval_id: str) -> Path:
        return self.approval_root / f"{self._slug(approval_id, fallback='approval')}.md"

    def _approval_decisions_path(self, approval_id: str) -> Path:
        return self.approval_root / f"{self._slug(approval_id, fallback='approval')}.decisions.jsonl"

    def _approval_digest(self, payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _git_head(self) -> str | None:
        try:
            completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project_path, capture_output=True, text=True, check=True)
            return completed.stdout.strip() or None
        except Exception:
            return None

    def _git_diff_digest(self) -> str | None:
        try:
            completed = subprocess.run(["git", "diff", "--binary"], cwd=self.project_path, capture_output=True, text=True, check=True)
            return self._approval_digest(completed.stdout or "")
        except Exception:
            return None

    def _load_approval(self, approval_id: str) -> dict[str, Any] | None:
        path = self._approval_path(approval_id)
        if not path.exists():
            return None
        metadata, body = self._split_markdown_frontmatter(path.read_text(encoding="utf-8"))
        metadata["_id"] = metadata.get("approval_id") or approval_id
        metadata["body"] = body.strip()
        decisions_path = self._approval_decisions_path(str(metadata["_id"]))
        decisions: list[dict[str, Any]] = []
        if decisions_path.exists():
            for line in decisions_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        loaded = json.loads(line)
                        if isinstance(loaded, dict):
                            decisions.append(loaded)
                    except Exception:
                        pass
        metadata["decisions"] = decisions
        return metadata

    def _write_approval(self, approval: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(approval.get("approval_id") or approval.get("_id"))
        approval["approval_id"] = approval_id
        self.approval_root.mkdir(parents=True, exist_ok=True)
        body = approval.pop("body", None) or self._render_approval_body(approval)
        decisions = approval.pop("decisions", None)
        self._approval_path(approval_id).write_text(self._dump_markdown_frontmatter(approval, body), encoding="utf-8")
        if decisions is not None:
            decisions_path = self._approval_decisions_path(approval_id)
            decisions_path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in decisions), encoding="utf-8")
        approval["body"] = body
        if decisions is not None:
            approval["decisions"] = decisions
        return approval

    def _render_approval_body(self, approval: dict[str, Any]) -> str:
        evidence = approval.get("evidence")
        lines = [
            "## Request",
            "",
            str(approval.get("description") or "Review and decide whether this workflow may continue."),
            "",
            "## Subject",
            "",
            f"- Workflow run: `{approval.get('workflow_run_id')}`",
            f"- Workflow step: `{approval.get('workflow_step_id')}`",
            f"- Subject digest: `{approval.get('subject_digest')}`",
            "",
            "## Evidence",
            "",
        ]
        if isinstance(evidence, list) and evidence:
            lines.extend(f"- `{item}`" for item in evidence)
        else:
            lines.append("- No evidence paths declared.")
        lines.append("")
        return "\n".join(lines)

    def _approval_public(self, approval: dict[str, Any]) -> dict[str, Any]:
        return dict(approval)

    def approval_list(self, *, status: str | None = None) -> dict[str, Any]:
        approvals: list[dict[str, Any]] = []
        if self.approval_root.is_dir():
            for path in sorted(self.approval_root.glob("*.md")):
                approval = self._load_approval(path.stem)
                if approval and (status is None or approval.get("status") == status):
                    approvals.append(self._approval_public(approval))
        return {"approvals": approvals, "status": status}

    def approval_show(self, approval_id: str) -> dict[str, Any]:
        approval = self._load_approval(approval_id)
        if approval is None:
            raise FileNotFoundError(f"Approval not found: {approval_id}")
        return {"approval": self._approval_public(approval)}

    def _approval_actor(self) -> str:
        return os.environ.get("KRAIL_ACTOR") or os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or "local:unknown"

    def approval_decide(
        self,
        approval_id: str,
        *,
        decision: str,
        comment: str = "",
        resume: bool = False,
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected", "changes_requested"}:
            raise ValueError("decision must be approved, rejected, or changes_requested")
        approval = self._load_approval(approval_id)
        if approval is None:
            raise FileNotFoundError(f"Approval not found: {approval_id}")
        allowed = approval.get("allow_decisions")
        if isinstance(allowed, list) and allowed and decision not in allowed:
            raise ValueError(f"decision {decision!r} is not allowed for approval {approval_id}")
        actor = self._approval_actor()
        if bool(approval.get("prevent_self_approval")) and actor == approval.get("requested_by"):
            raise PermissionError("self-approval is not allowed")
        now = _dt.datetime.now(_dt.UTC).isoformat()
        decision_record = {
            "approval_id": approval_id,
            "actor": actor,
            "decision": decision,
            "comment": comment,
            "decided_at": now,
            "subject_digest": approval.get("subject_digest"),
            "authentication_source": "local_cli",
        }
        decisions = list(approval.get("decisions") or [])
        decisions.append(decision_record)
        if decision == "approved":
            approvals_received = len([item for item in decisions if item.get("decision") == "approved"])
            required = int(approval.get("minimum_approvals") or 1)
            approval["status"] = "approved" if approvals_received >= required else "pending"
            approval["approvals_received"] = approvals_received
            approval["approvals_required"] = required
        else:
            approval["status"] = decision
        approval["resolved_at"] = now if approval["status"] != "pending" else None
        approval["decisions"] = decisions
        self._write_approval(approval)
        result = {"approval": self._approval_public(approval), "decision": decision_record}
        if resume:
            run_id = approval.get("workflow_run_id")
            if isinstance(run_id, str) and run_id:
                result["workflow"] = self.workflow_resume(run_id)
        return result

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

    def _listener_engine(self) -> ListenerEngine:
        return ListenerEngine(self)

    def _queue_engine(self) -> QueueEngine:
        return QueueEngine(self.project_path)

    def queue_init(self, queue_id: str, *, source: str, key: str, force: bool = False) -> dict[str, Any]:
        return self._queue_engine().init(queue_id, source=source, key=key, force=force)

    def queue_status(self, queue_id: str) -> dict[str, Any]:
        return self._queue_engine().status(queue_id)

    def queue_claim(self, queue_id: str, *, limit: int = 10, where: list[str] | None = None, owner: str | None = None, lease_minutes: int = 120) -> dict[str, Any]:
        return self._queue_engine().claim(queue_id, limit=limit, where=where, owner=owner, lease_minutes=lease_minutes)

    def queue_update_batch(self, queue_id: str, batch_id: str, *, status: str) -> dict[str, Any]:
        return self._queue_engine().update_batch(queue_id, batch_id, status=status)

    def queue_release(self, queue_id: str, *, stale: bool = False) -> dict[str, Any]:
        return self._queue_engine().release(queue_id, stale=stale)

    def listener_templates(self) -> dict[str, Any]:
        return self._listener_engine().templates()

    def listener_init(self, template: str, *, listener_id: str | None = None, force: bool = False) -> dict[str, Any]:
        return self._listener_engine().init_spec(template, listener_id=listener_id, force=force)

    def listener_validate(self, listener_id: str | None = None) -> dict[str, Any]:
        return self._listener_engine().validate_spec(listener_id)

    def listener_doctor(self) -> dict[str, Any]:
        return self._listener_engine().doctor()

    def listener_list(self) -> dict[str, Any]:
        return self._listener_engine().list_specs()

    def listener_show(self, listener_id: str) -> dict[str, Any]:
        return self._listener_engine().show_spec(listener_id)

    def listener_test(self, listener_id: str) -> dict[str, Any]:
        return self._listener_engine().test(listener_id)

    def listener_poll(self, listener_id: str | None = None, *, dry_run: bool = False, execute: bool = True) -> dict[str, Any]:
        return self._listener_engine().poll(listener_id, dry_run=dry_run, execute=execute)

    def listener_daemon(self, *, once: bool = False, interval_seconds: int = 30) -> dict[str, Any]:
        return self._listener_engine().daemon(once=once, interval_seconds=interval_seconds)

    def listener_serve(self, *, host: str = "127.0.0.1", port: int = 8787) -> dict[str, Any]:
        return self._listener_engine().serve(host=host, port=port)

    def event_list(self, *, limit: int = 20, listener_id: str | None = None) -> dict[str, Any]:
        return self._listener_engine().list_events(limit=limit, listener_id=listener_id)

    def event_show(self, event_id: str) -> dict[str, Any]:
        return self._listener_engine().show_event(event_id)

    def event_replay(self, event_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._listener_engine().replay_event(event_id, dry_run=dry_run)

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

    def mount_workflow_list(self, mount_id: str) -> dict[str, Any]:
        mount, project = self._resolve_mount_project(mount_id)
        return self._mount_proxy_result(project.list_workflows(), mount=mount.id if mount else None, project_slug=project.slug)

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

    def mount_workflow_show(self, mount_id: str, workflow_id: str) -> dict[str, Any]:
        mount, project = self._resolve_mount_project(mount_id)
        return self._mount_proxy_result(project.show_workflow(workflow_id), mount=mount.id if mount else None, project_slug=project.slug)

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

    def _workflow_context_value(self, value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return None

    def _eval_workflow_expr(self, expression: str, context: dict[str, Any]) -> Any:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"invalid workflow expression: {expression}") from exc

        def eval_node(node: ast.AST) -> Any:
            if isinstance(node, ast.Expression):
                return eval_node(node.body)
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                if node.id in context:
                    return context[node.id]
                if node.id == "true":
                    return True
                if node.id == "false":
                    return False
                if node.id == "null":
                    return None
                raise ValueError(f"unknown workflow name: {node.id}")
            if isinstance(node, ast.Attribute):
                return self._workflow_context_value(eval_node(node.value), node.attr)
            if isinstance(node, ast.Subscript):
                target = eval_node(node.value)
                key = eval_node(node.slice)
                if isinstance(target, dict):
                    return target.get(key)
                if isinstance(target, (list, tuple)) and isinstance(key, int):
                    return target[key]
                return None
            if isinstance(node, ast.List):
                return [eval_node(item) for item in node.elts]
            if isinstance(node, ast.Tuple):
                return tuple(eval_node(item) for item in node.elts)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return not bool(eval_node(node.operand))
            if isinstance(node, ast.BoolOp):
                if isinstance(node.op, ast.And):
                    return all(bool(eval_node(item)) for item in node.values)
                if isinstance(node.op, ast.Or):
                    return any(bool(eval_node(item)) for item in node.values)
            if isinstance(node, ast.Compare):
                left = eval_node(node.left)
                for op, comparator in zip(node.ops, node.comparators):
                    right = eval_node(comparator)
                    if isinstance(op, ast.Eq):
                        ok = left == right
                    elif isinstance(op, ast.NotEq):
                        ok = left != right
                    elif isinstance(op, ast.In):
                        ok = left in right if right is not None else False
                    elif isinstance(op, ast.NotIn):
                        ok = left not in right if right is not None else True
                    elif isinstance(op, ast.Lt):
                        ok = left < right
                    elif isinstance(op, ast.LtE):
                        ok = left <= right
                    elif isinstance(op, ast.Gt):
                        ok = left > right
                    elif isinstance(op, ast.GtE):
                        ok = left >= right
                    else:
                        raise ValueError("unsupported workflow comparison operator")
                    if not ok:
                        return False
                    left = right
                return True
            raise ValueError(f"unsupported workflow expression element: {type(node).__name__}")

        return eval_node(tree)

    def _workflow_when_allows(self, step: dict[str, Any], context: dict[str, Any]) -> bool:
        when = step.get("when")
        if when is None:
            return True
        return bool(self._eval_workflow_expr(str(when), context))

    def _record_workflow_result(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        step_id = result.get("id")
        if isinstance(step_id, str) and step_id:
            context.setdefault("steps", {})[step_id] = result

    def _workflow_result_failed(self, result: dict[str, Any]) -> bool:
        status = result.get("status")
        if status is None:
            return False
        return status not in {"done", "dry_run", "skipped", "awaiting_approval"}

    def _workflow_result_paused(self, result: dict[str, Any]) -> bool:
        if result.get("status") == "awaiting_approval":
            return True
        for key in ("steps", "then", "else"):
            child = result.get(key)
            if isinstance(child, list) and any(self._workflow_result_paused(item) for item in child):
                return True
        iterations = result.get("iteration_results")
        if isinstance(iterations, list):
            for iteration in iterations:
                child_steps = iteration.get("steps") if isinstance(iteration, dict) else None
                if isinstance(child_steps, list) and any(self._workflow_result_paused(item) for item in child_steps):
                    return True
        branch_results = result.get("branch_results")
        if isinstance(branch_results, list) and any(self._workflow_result_paused(item) for item in branch_results):
            return True
        return False

    def _flatten_workflow_pauses(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pauses: list[dict[str, Any]] = []
        for result in results:
            if result.get("status") == "awaiting_approval":
                pauses.append(result)
            for key in ("steps", "then", "else"):
                child = result.get(key)
                if isinstance(child, list):
                    pauses.extend(self._flatten_workflow_pauses(child))
            iterations = result.get("iteration_results")
            if isinstance(iterations, list):
                for iteration in iterations:
                    child_steps = iteration.get("steps") if isinstance(iteration, dict) else None
                    if isinstance(child_steps, list):
                        pauses.extend(self._flatten_workflow_pauses(child_steps))
            branch_results = result.get("branch_results")
            if isinstance(branch_results, list):
                pauses.extend(self._flatten_workflow_pauses(branch_results))
        return pauses

    def _flatten_workflow_failures(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for result in results:
            if self._workflow_result_failed(result):
                failures.append(result)
            for key in ("steps", "then", "else"):
                child = result.get(key)
                if isinstance(child, list):
                    failures.extend(self._flatten_workflow_failures(child))
            iterations = result.get("iteration_results")
            if isinstance(iterations, list):
                for iteration in iterations:
                    child_steps = iteration.get("steps") if isinstance(iteration, dict) else None
                    if isinstance(child_steps, list):
                        failures.extend(self._flatten_workflow_failures(child_steps))
            branch_results = result.get("branch_results")
            if isinstance(branch_results, list):
                failures.extend(self._flatten_workflow_failures(branch_results))
        return failures

    def _workflow_subject_digest(self, step: dict[str, Any], context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        subject = step.get("subject") if isinstance(step.get("subject"), dict) else {}
        subject_step_id = subject.get("step") if isinstance(subject.get("step"), str) else None
        subject_step = context.get("steps", {}).get(subject_step_id) if subject_step_id else None
        payload = {
            "workflow_digest": context.get("workflow", {}).get("workflow_digest"),
            "target_base_commit": self._git_head(),
            "diff_digest": self._git_diff_digest(),
            "step": step,
            "subject_step": subject_step,
        }
        digest = self._approval_digest(payload)
        return digest, payload

    def _approval_id_for_step(self, workflow_id: str, step_id: str, context: dict[str, Any], path: tuple[str, ...]) -> str:
        run_id = str(context.get("workflow", {}).get("run_id") or "workflow")
        path_slug = self._slug("-".join(path), fallback="root")
        return self._slug(f"approval-{run_id}-{path_slug}-{step_id}", fallback="approval")

    def _execute_workflow_approval_step(
        self,
        workflow_id: str,
        step: dict[str, Any],
        *,
        step_id: str,
        context: dict[str, Any],
        path: tuple[str, ...],
    ) -> dict[str, Any]:
        approval_id = self._approval_id_for_step(workflow_id, step_id, context, path)
        subject_digest, subject_payload = self._workflow_subject_digest(step, context)
        approval = self._load_approval(approval_id)
        if approval is None and self.approval_root.is_dir():
            run_id = context.get("workflow", {}).get("run_id")
            for candidate in sorted(self.approval_root.glob("*.md")):
                loaded = self._load_approval(candidate.stem)
                if (
                    loaded
                    and loaded.get("workflow_run_id") == run_id
                    and loaded.get("workflow_step_id") == step_id
                    and loaded.get("workflow_id") == workflow_id
                ):
                    approval = loaded
                    approval_id = str(loaded.get("approval_id") or loaded.get("_id") or approval_id)
                    break
        now = _dt.datetime.now(_dt.UTC)
        if approval is None:
            reviewers = step.get("reviewers") if isinstance(step.get("reviewers"), dict) else {}
            expires_in_hours = step.get("expires_in_hours")
            approval = {
                "approval_id": approval_id,
                "project_id": self.project_path.name,
                "workflow_id": workflow_id,
                "workflow_run_id": context.get("workflow", {}).get("run_id"),
                "workflow_step_id": step_id,
                "execution_path": list(path),
                "approval_type": step.get("approval_type") or "workflow_step",
                "status": "pending",
                "title": step.get("title") or f"Approve {step_id}",
                "description": step.get("description") or "",
                "requested_by": self._approval_actor(),
                "requested_by_role": step.get("requested_by_role") or "workflow",
                "requested_at": now.isoformat(),
                "expires_at": (now + _dt.timedelta(hours=int(expires_in_hours))).isoformat() if isinstance(expires_in_hours, int) else None,
                "minimum_approvals": int(step.get("minimum_approvals") or 1),
                "approvals_received": 0,
                "prevent_self_approval": bool(step.get("prevent_self_approval", True)),
                "allowed_users": reviewers.get("users") if isinstance(reviewers.get("users"), list) else [],
                "allowed_teams": reviewers.get("teams") if isinstance(reviewers.get("teams"), list) else [],
                "allow_decisions": step.get("allow_decisions") if isinstance(step.get("allow_decisions"), list) else ["approved", "rejected", "changes_requested"],
                "subject": step.get("subject") if isinstance(step.get("subject"), dict) else {},
                "subject_digest": subject_digest,
                "subject_payload": subject_payload,
                "evidence": step.get("evidence") if isinstance(step.get("evidence"), list) else [],
            }
            self._write_approval(approval)
            return {"id": step_id, "kind": "approval", "status": "awaiting_approval", "decision": "pending", "approval_id": approval_id, "subject_digest": subject_digest}
        expires_at = approval.get("expires_at")
        if approval.get("status") == "pending" and isinstance(expires_at, str):
            try:
                if _dt.datetime.fromisoformat(expires_at) <= now:
                    approval["status"] = "expired"
                    approval["resolved_at"] = now.isoformat()
                    self._write_approval(approval)
            except Exception:
                pass
        if approval.get("subject_digest") != subject_digest and approval.get("status") == "approved":
            approval["status"] = "invalidated"
            approval["invalidated_at"] = now.isoformat()
            approval["invalidation_reason"] = "subject_changed"
            self._write_approval(approval)
            return {"id": step_id, "kind": "approval", "status": "awaiting_approval", "decision": "invalidated", "approval_id": approval_id, "reason": "approval_invalidated", "subject_digest": subject_digest}
        status = str(approval.get("status") or "pending")
        if status == "approved":
            return {"id": step_id, "kind": "approval", "status": "done", "decision": "approved", "approval_id": approval_id, "subject_digest": subject_digest}
        if status == "changes_requested":
            return {"id": step_id, "kind": "approval", "status": "done", "decision": "changes_requested", "approval_id": approval_id, "subject_digest": subject_digest}
        if status in {"rejected", "expired", "revoked", "invalidated"}:
            return {"id": step_id, "kind": "approval", "status": "failed", "decision": status, "approval_id": approval_id, "subject_digest": subject_digest}
        return {"id": step_id, "kind": "approval", "status": "awaiting_approval", "decision": "pending", "approval_id": approval_id, "subject_digest": subject_digest}

    def _interpolate_workflow_value(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            loop = context.get("loop") if isinstance(context.get("loop"), dict) else {}
            item = loop.get("item")
            loop_var = loop.get("var")
            rendered = value.replace("${{ loop.item }}", "" if item is None else str(item))
            if isinstance(loop_var, str) and loop_var:
                rendered = rendered.replace("${{ " + loop_var + " }}", "" if item is None else str(item))
            pattern = re.compile(r"\${{\s*([^}]+?)\s*}}")

            def replace_expr(match: re.Match[str]) -> str:
                expression = match.group(1).strip()
                if expression in {"loop.item", loop_var}:
                    return "" if item is None else str(item)
                try:
                    resolved = self._eval_workflow_expr(expression, context)
                except Exception:
                    return match.group(0)
                return "" if resolved is None else str(resolved)

            rendered = pattern.sub(replace_expr, rendered)
            return rendered
        if isinstance(value, list):
            return [self._interpolate_workflow_value(item, context) for item in value]
        if isinstance(value, dict):
            return {str(k): self._interpolate_workflow_value(v, context) for k, v in value.items()}
        return value

    def _workflow_outputs(self, spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        outputs = spec.get("outputs")
        if not isinstance(outputs, dict):
            return {}
        result: dict[str, Any] = {}
        for name, descriptor in outputs.items():
            if not isinstance(name, str) or not name:
                continue
            expression = None
            if isinstance(descriptor, dict):
                expression = descriptor.get("from")
            elif isinstance(descriptor, str):
                expression = descriptor
            if not isinstance(expression, str) or not expression.strip():
                continue
            try:
                result[name] = self._eval_workflow_expr(expression.strip(), context)
            except Exception as exc:
                result[name] = {"error": str(exc)}
        return result

    def _validate_simple_schema(self, value: Any, schema: Any, *, path: str = "output") -> list[str]:
        if not isinstance(schema, dict):
            return []
        errors: list[str] = []
        expected_type = schema.get("type")
        if expected_type:
            type_map = {
                "object": dict,
                "array": list,
                "string": str,
                "number": (int, float),
                "integer": int,
                "boolean": bool,
            }
            expected = type_map.get(str(expected_type))
            if expected and not isinstance(value, expected):
                errors.append(f"{path} must be {expected_type}")
                return errors
        if isinstance(value, dict):
            for key in schema.get("required") or []:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key} is required")
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            for key, child_schema in properties.items():
                if key in value:
                    errors.extend(self._validate_simple_schema(value[key], child_schema, path=f"{path}.{key}"))
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(self._validate_simple_schema(item, schema["items"], path=f"{path}[{index}]"))
        return errors

    def _workflow_exposed_outputs(self, output: dict[str, Any], expose: Any) -> dict[str, Any]:
        if not isinstance(expose, dict) or not expose:
            return output
        exposed: dict[str, Any] = {}
        for parent_name, child_name in expose.items():
            if isinstance(parent_name, str) and isinstance(child_name, str) and child_name in output:
                exposed[parent_name] = output[child_name]
        return exposed

    def _execute_workflow_child_step(
        self,
        step: dict[str, Any],
        *,
        step_id: str,
        dry_run: bool,
        context: dict[str, Any],
        path: tuple[str, ...],
    ) -> dict[str, Any]:
        child_workflow = str(step.get("workflow") or "").strip()
        if not child_workflow:
            return {"id": step_id, "kind": "workflow", "status": "failed", "error": "workflow is required"}
        parent_workflow = str(context.get("workflow", {}).get("id") or "")
        call_stack = [str(item) for item in (context.get("workflow", {}).get("call_stack") or [parent_workflow]) if item]
        call_depth = int(context.get("workflow", {}).get("call_depth") or 0)
        if child_workflow == parent_workflow or child_workflow in call_stack:
            return {"id": step_id, "kind": "workflow", "status": "failed", "workflow": child_workflow, "error": "recursive workflow call rejected"}
        if call_depth >= 8:
            return {"id": step_id, "kind": "workflow", "status": "failed", "workflow": child_workflow, "error": "maximum workflow call depth exceeded"}
        try:
            shown = self.workflow_show(child_workflow)
        except Exception as exc:
            return {"id": step_id, "kind": "workflow", "status": "failed", "workflow": child_workflow, "error": str(exc)}
        if not shown.get("materialized", True):
            return {"id": step_id, "kind": "workflow", "status": "failed", "workflow": child_workflow, "error": "child workflow is not materialized"}
        validation = shown.get("validation") or self._validate_workflow_spec(shown["workflow"], path=shown.get("path"))
        if not validation.get("ok"):
            return {"id": step_id, "kind": "workflow", "status": "failed", "workflow": child_workflow, "validation": validation}
        child_inputs = self._interpolate_workflow_value(step.get("with") if isinstance(step.get("with"), dict) else {}, context)
        if dry_run:
            return {
                "id": step_id,
                "kind": "workflow",
                "status": "dry_run",
                "workflow": child_workflow,
                "with": child_inputs,
                "validation": validation,
                "steps": shown["workflow"].get("steps") or [],
            }
        child = self._execute_workflow_spec(
            child_workflow,
            shown["workflow"],
            validation_path=shown.get("path"),
            dry_run=False,
            force=False,
            inputs=child_inputs if isinstance(child_inputs, dict) else {},
            parent_run_id=str(context.get("workflow", {}).get("run_id") or ""),
            parent_step_path=".".join([*path, step_id]),
            call_depth=call_depth + 1,
            call_stack=[*call_stack, child_workflow],
        )
        status = child.get("status")
        result_status = "done" if status == "done" else ("awaiting_approval" if status == "awaiting_approval" else "failed")
        output = self._workflow_exposed_outputs(child.get("output") if isinstance(child.get("output"), dict) else {}, step.get("expose"))
        result = {
            "id": step_id,
            "kind": "workflow",
            "status": result_status,
            "workflow": child_workflow,
            "child_run_id": child.get("run_id"),
            "child_status": status,
            "output": output,
            "child": {
                "path": child.get("path"),
                "pending_approval_id": child.get("pending_approval_id"),
                "failed_step": child.get("failed_step"),
                "input_digest": child.get("input_digest"),
                "workflow_digest": child.get("workflow_digest"),
            },
        }
        if status == "awaiting_approval" and child.get("pending_approval_id"):
            result["approval_id"] = child["pending_approval_id"]
        return result

    def _parallel_branch_resources(self, branch: dict[str, Any]) -> tuple[set[str], set[str]]:
        resources = branch.get("resources") if isinstance(branch.get("resources"), dict) else {}
        read = {str(item) for item in resources.get("read", []) if isinstance(item, str)}
        write = {str(item) for item in resources.get("write", []) if isinstance(item, str)}
        return read, write

    def _parallel_has_resource_conflict(self, branches: list[dict[str, Any]]) -> bool:
        claims: list[tuple[set[str], set[str]]] = [self._parallel_branch_resources(branch) for branch in branches]
        for left_index, (left_read, left_write) in enumerate(claims):
            for right_read, right_write in claims[left_index + 1 :]:
                if left_write & (right_read | right_write):
                    return True
                if right_write & (left_read | left_write):
                    return True
        return False

    def _parallel_branch_output(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for result in steps:
            step_id = result.get("id")
            if isinstance(step_id, str) and "output" in result:
                output[step_id] = result["output"]
        return output

    def _execute_parallel_branch(
        self,
        workflow_id: str,
        branch: dict[str, Any],
        *,
        branch_index: int,
        spec: dict[str, Any],
        dry_run: bool,
        run_dir: Path,
        context: dict[str, Any],
        path: tuple[str, ...],
    ) -> dict[str, Any]:
        branch_id = str(branch.get("id") or f"branch_{branch_index}")
        branch_context = copy.deepcopy(context)
        branch_context["branch"] = {"id": branch_id, "index": branch_index}
        steps = branch.get("steps") if isinstance(branch.get("steps"), list) else []
        child_results = self._execute_workflow_steps(
            workflow_id,
            steps,
            spec=spec,
            dry_run=dry_run,
            run_dir=run_dir,
            context=branch_context,
            path=(*path, branch_id),
        )
        failures = self._flatten_workflow_failures(child_results)
        pauses = self._flatten_workflow_pauses(child_results)
        required = bool(branch.get("required", True))
        status = "awaiting_approval" if pauses else ("failed" if failures and required else ("warning" if failures else ("dry_run" if dry_run else "done")))
        return {
            "id": branch_id,
            "status": status,
            "required": required,
            "steps": child_results,
            "output": self._parallel_branch_output(child_results),
            "failed_step": failures[0].get("id") if failures else None,
            "pending_approval_id": pauses[0].get("approval_id") if pauses else None,
        }

    def _execute_workflow_parallel_step(
        self,
        workflow_id: str,
        step: dict[str, Any],
        *,
        step_id: str,
        spec: dict[str, Any],
        dry_run: bool,
        run_dir: Path,
        context: dict[str, Any],
        path: tuple[str, ...],
    ) -> dict[str, Any]:
        branches = step.get("branches") if isinstance(step.get("branches"), list) else []
        max_parallel = min(int(step.get("max_parallel") or len(branches) or 1), 8)
        fail_fast = bool(step.get("fail_fast", False))
        conflict_policy = str(step.get("conflict_policy") or step.get("join", {}).get("conflict_policy") if isinstance(step.get("join"), dict) else step.get("conflict_policy") or "reject")
        if self._parallel_has_resource_conflict(branches):
            if conflict_policy == "reject":
                return {"id": step_id, "kind": "parallel", "status": "failed", "error": "parallel branch resource conflict"}
            max_parallel = 1
        result = {"id": step_id, "kind": "parallel", "status": "running", "max_parallel": max_parallel, "branches": {}, "branch_results": []}
        if dry_run:
            for index, branch in enumerate(branches):
                branch_result = self._execute_parallel_branch(workflow_id, branch, branch_index=index, spec=spec, dry_run=True, run_dir=run_dir, context=context, path=(*path, step_id))
                result["branch_results"].append(branch_result)
                result["branches"][branch_result["id"]] = branch_result
            result["status"] = "dry_run"
            return result
        branch_results: list[dict[str, Any]] = []
        if max_parallel == 1:
            for index, branch in enumerate(branches):
                branch_result = self._execute_parallel_branch(workflow_id, branch, branch_index=index, spec=spec, dry_run=False, run_dir=run_dir, context=context, path=(*path, step_id))
                branch_results.append(branch_result)
                if fail_fast and branch_result.get("status") == "failed":
                    break
        else:
            with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                futures = {
                    executor.submit(
                        self._execute_parallel_branch,
                        workflow_id,
                        branch,
                        branch_index=index,
                        spec=spec,
                        dry_run=False,
                        run_dir=run_dir,
                        context=context,
                        path=(*path, step_id),
                    ): branch
                    for index, branch in enumerate(branches)
                }
                for future in as_completed(futures):
                    branch_result = future.result()
                    branch_results.append(branch_result)
                    if fail_fast and branch_result.get("status") == "failed":
                        break
        branch_results.sort(key=lambda item: str(item.get("id") or ""))
        result["branch_results"] = branch_results
        result["branches"] = {str(item["id"]): item for item in branch_results}
        required_results = [item for item in branch_results if item.get("required", True)]
        success_count = len([item for item in required_results if item.get("status") in {"done", "warning"}])
        minimum_successful = int(step.get("minimum_successful") or len(required_results))
        if any(item.get("status") == "awaiting_approval" for item in branch_results):
            result["status"] = "awaiting_approval"
        elif any(item.get("status") == "failed" for item in required_results):
            result["status"] = "failed"
        elif success_count < minimum_successful:
            result["status"] = "failed"
            result["error"] = f"minimum_successful not reached: {success_count}/{minimum_successful}"
        else:
            result["status"] = "done"
        result["output"] = {str(item["id"]): item.get("output", {}) for item in branch_results}
        return result

    def _execute_workflow_command_step(
        self,
        step: dict[str, Any],
        *,
        step_id: str,
        run_dir: Path,
        log_prefix: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        retry = self._workflow_retry_policy(step)
        attempts = retry["max_attempts"]
        timeout_seconds = int(step.get("timeout_seconds") or 0) or (int(step["timeout_minutes"]) * 60 if step.get("timeout_minutes") else None)
        env = os.environ.copy()
        if isinstance(step.get("env"), dict):
            env.update({str(k): str(v) for k, v in self._interpolate_workflow_value(step["env"], context).items()})
        command = self._interpolate_workflow_value(str(step.get("run") or "").strip(), context)
        args = self._interpolate_workflow_value(step.get("args"), context) if isinstance(step.get("args"), list) else None
        last_returncode = 1
        stdout = ""
        stderr = ""
        attempt = 0
        for attempt in range(1, attempts + 1):
            try:
                if args is not None:
                    completed = subprocess.run([str(item) for item in args], cwd=self.project_path, shell=False, capture_output=True, text=True, timeout=timeout_seconds, env=env)
                else:
                    completed = subprocess.run(command, cwd=self.project_path, shell=True, capture_output=True, text=True, timeout=timeout_seconds, env=env)
                last_returncode = completed.returncode
                stdout = completed.stdout or ""
                stderr = completed.stderr or ""
            except subprocess.TimeoutExpired as exc:
                last_returncode = 124
                stdout = exc.stdout or ""
                stderr = exc.stderr or "timeout expired"
            (run_dir / f"{log_prefix}.attempt{attempt}.stdout.log").write_text(stdout, encoding="utf-8")
            (run_dir / f"{log_prefix}.attempt{attempt}.stderr.log").write_text(stderr, encoding="utf-8")
            if last_returncode == 0:
                break
            if retry["backoff_seconds"] and attempt < attempts:
                time.sleep(float(retry["backoff_seconds"]))
        result: dict[str, Any] = {
            "id": step_id,
            "kind": "command",
            "status": "done" if last_returncode == 0 else "failed",
            "exit_code": last_returncode,
            "attempts": attempt,
        }
        if args is not None:
            result["args"] = args
        else:
            result["command"] = command
        capture = step.get("capture")
        if isinstance(capture, dict):
            source = stderr if capture.get("from", "stdout") == "stderr" else stdout
            if capture.get("format", "text") == "json":
                try:
                    result["output"] = json.loads(source or "null")
                except Exception as exc:
                    result.update({"status": "failed", "capture_error": str(exc)})
            else:
                result["output"] = source
        return result

    @staticmethod
    def _workflow_retry_policy(step: dict[str, Any]) -> dict[str, int]:
        retry = step.get("retry", 0)
        if isinstance(retry, dict):
            max_attempts = int(retry.get("max_attempts") or retry.get("attempts") or 1)
            backoff_seconds = int(retry.get("backoff_seconds") or 0)
        else:
            max_attempts = int(retry) + 1
            backoff_seconds = 0
        return {"max_attempts": max(1, max_attempts), "backoff_seconds": max(0, backoff_seconds)}

    def _execute_workflow_leaf_step(
        self,
        workflow_id: str,
        step: dict[str, Any],
        *,
        spec: dict[str, Any],
        dry_run: bool,
        run_dir: Path,
        log_prefix: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        step_id = str(step.get("id") or "step")
        kind = str(step.get("kind") or "command")
        if dry_run:
            return {"id": step_id, "kind": kind, "status": "dry_run", "step": step}
        if kind == "command":
            return self._execute_workflow_command_step(step, step_id=step_id, run_dir=run_dir, log_prefix=log_prefix, context=context)
        if kind == "approval":
            return self._execute_workflow_approval_step(workflow_id, step, step_id=step_id, context=context, path=tuple(log_prefix.split("-")))
        if kind == "workflow":
            return self._execute_workflow_child_step(step, step_id=step_id, dry_run=dry_run, context=context, path=tuple(log_prefix.split("-")))
        if kind == "agent":
            attempts = int(step.get("retry", 0)) + 1
            prompt = str(step.get("prompt") or step.get("description") or f"Run workflow step {step_id}.")
            runner = str(step.get("runner") or spec.get("runner") or "auto")
            resolved_runner = self.resolve_runner(runner)["runner"]
            role = str(step.get("role") or "research")
            dispatch: dict[str, Any] = {"status": "failed"}
            created: dict[str, Any] = {"task": {"id": ""}}
            for _attempt in range(1, attempts + 1):
                created = self.create_task(
                    f"{workflow_id}: {step_id}",
                    description=prompt,
                    runner=resolved_runner,
                    workflow=workflow_id,
                    role=role,
                )
                dispatch = self.dispatch_task(created["task"]["id"], runner=resolved_runner, dry_run=False)
                if dispatch.get("status") == "done":
                    break
            result = {"id": step_id, "kind": "agent", "status": dispatch.get("status"), "task_id": created["task"]["id"], "runner": resolved_runner, "requested_runner": runner, "role": role, "dispatch": dispatch}
            task = dispatch.get("task") if isinstance(dispatch, dict) else None
            if isinstance(task, dict) and isinstance(task.get("session_result"), dict):
                result["output"] = task["session_result"]
            schema_errors = self._validate_simple_schema(result.get("output", {}), step.get("output_schema"), path=f"steps.{step_id}.output")
            if schema_errors:
                result["status"] = "failed"
                result["schema_errors"] = schema_errors
            return result
        if kind == "think":
            runner = str(step.get("runner") or spec.get("runner") or "auto")
            result = {"id": step_id, "kind": "think"}
            result.update(
                self._execute_workflow_think_step(
                    workflow_id,
                    step_id,
                    step,
                    dry_run=dry_run,
                    default_runner=runner,
                )
            )
            return result
        raise ValueError(f"Unsupported workflow step kind: {kind}")

    @staticmethod
    def _workflow_step_needs(step: dict[str, Any]) -> list[str]:
        raw = step.get("needs", [])
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [str(item) for item in raw if isinstance(item, str) and item.strip()]
        return []

    @staticmethod
    def _workflow_uses_needs(steps: list[dict[str, Any]]) -> bool:
        return any(isinstance(step, dict) and bool(step.get("needs")) for step in steps)

    def _execute_workflow_steps_dag(
        self,
        workflow_id: str,
        steps: list[dict[str, Any]],
        *,
        spec: dict[str, Any],
        dry_run: bool,
        run_dir: Path,
        context: dict[str, Any],
        path: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        indexed = [(index, step) for index, step in enumerate(steps, start=1) if isinstance(step, dict)]
        pending: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, step in indexed:
            step_id = str(step.get("id") or f"step_{index}")
            pending[step_id] = (index, step)
        completed: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        dag_cfg = spec.get("dag") if isinstance(spec.get("dag"), dict) else {}
        parallel_cfg = spec.get("parallel") if isinstance(spec.get("parallel"), dict) else {}
        max_concurrency = int(dag_cfg.get("max_concurrency") or parallel_cfg.get("max_concurrency") or 1)
        max_concurrency = max(1, min(max_concurrency, 8))
        fail_fast = bool(dag_cfg.get("fail_fast", False))

        while pending:
            blocked: list[str] = []
            ready: list[tuple[int, str, dict[str, Any]]] = []
            for step_id, (index, step) in sorted(pending.items(), key=lambda item: item[1][0]):
                needs = self._workflow_step_needs(step)
                failed_deps = [dep for dep in needs if dep in completed and self._workflow_result_failed(completed[dep])]
                paused_deps = [dep for dep in needs if dep in completed and self._workflow_result_paused(completed[dep])]
                if failed_deps or paused_deps:
                    blocked.append(step_id)
                    continue
                if all(dep in completed for dep in needs):
                    ready.append((index, step_id, step))
            for step_id in blocked:
                index, step = pending.pop(step_id)
                result = {
                    "id": step_id,
                    "kind": str(step.get("kind") or "command"),
                    "status": "blocked",
                    "reason": "dependency_not_successful",
                    "needs": self._workflow_step_needs(step),
                }
                completed[step_id] = result
                results.append(result)
                self._record_workflow_result(context, result)
            if not ready:
                if pending:
                    for step_id, (_index, step) in list(pending.items()):
                        result = {"id": step_id, "kind": str(step.get("kind") or "command"), "status": "blocked", "reason": "no_ready_dependencies", "needs": self._workflow_step_needs(step)}
                        pending.pop(step_id)
                        completed[step_id] = result
                        results.append(result)
                        self._record_workflow_result(context, result)
                break
            batch = ready[:max_concurrency]

            def run_one(item: tuple[int, str, dict[str, Any]]) -> tuple[int, str, dict[str, Any]]:
                index, step_id, step = item
                step_copy = {key: value for key, value in step.items() if key != "needs"}
                child_context = copy.deepcopy(context)
                child_results = self._execute_workflow_steps(
                    workflow_id,
                    [step_copy],
                    spec=spec,
                    dry_run=dry_run,
                    run_dir=run_dir,
                    context=child_context,
                    path=(*path, f"{index:02d}-{self._slug(step_id)}"),
                    dag_enabled=False,
                )
                result = child_results[0] if child_results else {"id": step_id, "kind": str(step.get("kind") or "command"), "status": "failed", "error": "step produced no result"}
                result["needs"] = self._workflow_step_needs(step)
                return index, step_id, result

            if len(batch) == 1 or max_concurrency == 1:
                batch_results = [run_one(batch[0])]
            else:
                with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
                    batch_results = list(executor.map(run_one, batch))
            for _index, step_id, result in sorted(batch_results, key=lambda item: item[0]):
                pending.pop(step_id, None)
                completed[step_id] = result
                results.append(result)
                self._record_workflow_result(context, result)
            if any(self._workflow_result_paused(result) for _index, _step_id, result in batch_results):
                break
            if fail_fast and any(self._workflow_result_failed(result) for _index, _step_id, result in batch_results):
                for step_id, (_index, step) in list(pending.items()):
                    result = {"id": step_id, "kind": str(step.get("kind") or "command"), "status": "blocked", "reason": "dag_fail_fast", "needs": self._workflow_step_needs(step)}
                    pending.pop(step_id)
                    completed[step_id] = result
                    results.append(result)
                    self._record_workflow_result(context, result)
                break
        return results

    def _execute_workflow_steps(
        self,
        workflow_id: str,
        steps: list[dict[str, Any]],
        *,
        spec: dict[str, Any],
        dry_run: bool,
        run_dir: Path,
        context: dict[str, Any],
        path: tuple[str, ...] = (),
        dag_enabled: bool = True,
    ) -> list[dict[str, Any]]:
        if dag_enabled and self._workflow_uses_needs(steps):
            return self._execute_workflow_steps_dag(
                workflow_id,
                steps,
                spec=spec,
                dry_run=dry_run,
                run_dir=run_dir,
                context=context,
                path=path,
            )
        results: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Workflow step {'.'.join(path + (str(index),))} must be a mapping")
            step_id = str(step.get("id") or f"step_{index}")
            kind = str(step.get("kind") or "command")
            log_prefix = "-".join([*path, f"{index:02d}-{self._slug(step_id)}"])
            try:
                allowed = self._workflow_when_allows(step, context)
            except Exception as exc:
                result = {"id": step_id, "kind": kind, "status": "failed", "error": f"when evaluation failed: {exc}"}
                results.append(result)
                self._record_workflow_result(context, result)
                break
            if not allowed:
                result = {"id": step_id, "kind": kind, "status": "skipped", "reason": "condition_false"}
                results.append(result)
                self._record_workflow_result(context, result)
                continue
            if kind == "if":
                condition = str(step.get("condition") or "")
                try:
                    branch_name = "then" if bool(self._eval_workflow_expr(condition, context)) else "else"
                except Exception as exc:
                    result = {"id": step_id, "kind": "if", "status": "failed", "condition": condition, "error": f"condition evaluation failed: {exc}"}
                    results.append(result)
                    self._record_workflow_result(context, result)
                    break
                branch_steps = step.get(branch_name) or []
                result = {"id": step_id, "kind": "if", "status": "running", "condition": condition, "branch": branch_name}
                child_results = self._execute_workflow_steps(workflow_id, branch_steps, spec=spec, dry_run=dry_run, run_dir=run_dir, context=context, path=(*path, f"{index:02d}-{step_id}", branch_name))
                result[branch_name] = child_results
                failures = self._flatten_workflow_failures(child_results)
                result["status"] = "awaiting_approval" if self._flatten_workflow_pauses(child_results) else ("failed" if failures else ("dry_run" if dry_run else "done"))
            elif kind == "repeat":
                max_iterations = int(step["max_iterations"])
                iteration_results: list[dict[str, Any]] = []
                result = {"id": step_id, "kind": "repeat", "status": "running", "max_iterations": max_iterations, "iteration_results": iteration_results}
                stop_reason = "max_iterations_reached"
                for iteration in range(1, max_iterations + 1):
                    previous_loop = context.get("loop")
                    context["loop"] = {"iteration": iteration, "index": iteration - 1}
                    child_results = self._execute_workflow_steps(workflow_id, step["steps"], spec=spec, dry_run=dry_run, run_dir=run_dir, context=context, path=(*path, f"{index:02d}-{step_id}", f"iter{iteration}"))
                    if previous_loop is None:
                        context.pop("loop", None)
                    else:
                        context["loop"] = previous_loop
                    iteration_results.append({"iteration": iteration, "steps": child_results})
                    if self._flatten_workflow_pauses(child_results):
                        stop_reason = "awaiting_approval"
                        break
                    if dry_run:
                        stop_reason = "dry_run"
                        break
                    try:
                        stop = bool(self._eval_workflow_expr(str(step["until"]), context))
                    except Exception as exc:
                        result["error"] = f"until evaluation failed: {exc}"
                        stop_reason = "condition_error"
                        break
                    if stop:
                        stop_reason = "condition_met"
                        break
                result["iterations"] = len(iteration_results)
                result["stop_reason"] = stop_reason
                result["status"] = "awaiting_approval" if stop_reason == "awaiting_approval" else ("dry_run" if dry_run else ("done" if stop_reason == "condition_met" else "failed"))
            elif kind == "foreach":
                if "items" in step:
                    items = step["items"]
                else:
                    try:
                        items = self._eval_workflow_expr(str(step["items_from"]), context)
                    except Exception as exc:
                        result = {"id": step_id, "kind": "foreach", "status": "failed", "error": f"items_from evaluation failed: {exc}"}
                        results.append(result)
                        self._record_workflow_result(context, result)
                        break
                if not isinstance(items, list):
                    result = {"id": step_id, "kind": "foreach", "status": "failed", "error": f"items did not evaluate to a list"}
                    results.append(result)
                    self._record_workflow_result(context, result)
                    break
                max_items = int(step["max_items"])
                if len(items) > max_items:
                    result = {"id": step_id, "kind": "foreach", "status": "failed", "error": f"expanded to {len(items)} items, above max_items {max_items}"}
                    results.append(result)
                    self._record_workflow_result(context, result)
                    break
                iteration_results = []
                result = {"id": step_id, "kind": "foreach", "status": "running", "count": len(items), "iteration_results": iteration_results}
                loop_var = str(step.get("as") or "item")
                for item_index, item in enumerate(items):
                    previous_loop = context.get("loop")
                    context["loop"] = {"index": item_index, "iteration": item_index + 1, "item": item, "var": loop_var, loop_var: item}
                    child_results = self._execute_workflow_steps(workflow_id, step["steps"], spec=spec, dry_run=dry_run, run_dir=run_dir, context=context, path=(*path, f"{index:02d}-{step_id}", f"item{item_index}"))
                    if previous_loop is None:
                        context.pop("loop", None)
                    else:
                        context["loop"] = previous_loop
                    iteration_results.append({"index": item_index, "item": item, "steps": child_results})
                    if self._flatten_workflow_pauses(child_results) or self._flatten_workflow_failures(child_results):
                        break
                failures = self._flatten_workflow_failures([{"iteration_results": iteration_results}])
                result["status"] = "awaiting_approval" if self._flatten_workflow_pauses([{"iteration_results": iteration_results}]) else ("failed" if failures else ("dry_run" if dry_run else "done"))
            elif kind == "parallel":
                result = self._execute_workflow_parallel_step(
                    workflow_id,
                    step,
                    step_id=step_id,
                    spec=spec,
                    dry_run=dry_run,
                    run_dir=run_dir,
                    context=context,
                    path=path,
                )
            else:
                result = self._execute_workflow_leaf_step(workflow_id, step, spec=spec, dry_run=dry_run, run_dir=run_dir, log_prefix=log_prefix, context=context)
            results.append(result)
            self._record_workflow_result(context, result)
            if self._workflow_result_paused(result):
                break
            if self._workflow_result_failed(result) and step.get("on_failure", "stop") == "stop":
                break
            time.sleep(0.01)
        return results

    def _execute_workflow_spec(
        self,
        workflow_id: str,
        spec: dict[str, Any],
        *,
        validation_path: str | None,
        dry_run: bool = False,
        force: bool = False,
        inputs: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        parent_step_path: str | None = None,
        call_depth: int = 0,
        call_stack: list[str] | None = None,
    ) -> dict[str, Any]:
        validation = self._validate_workflow_spec(spec, path=validation_path)
        if not validation["ok"]:
            return {"status": "invalid", "workflow": workflow_id, "validation": validation}
        denied = self._authorize_workflow_action(
            workflow_id,
            spec=spec,
            validation_path=validation_path,
            action="execute",
        )
        if denied:
            return denied
        steps = spec.get("steps") or []
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"Workflow {workflow_id!r} must define a non-empty steps list")
        run_id = f"workflow_{self._slug(str(spec.get('id') or workflow_id))}_{_dt.datetime.now(_dt.UTC).strftime('%Y%m%d%H%M%S')}"
        run_dir = self.sessions_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "workflow.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
        workflow_digest = self._approval_digest(spec)
        input_payload = inputs if isinstance(inputs, dict) else (spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {})
        input_digest = self._approval_digest(input_payload)
        started_at = _dt.datetime.now(_dt.UTC)
        results: list[dict[str, Any]] = []
        lock: Path | None = None
        if not dry_run and not force:
            lock = self._acquire_workflow_lock(str(spec.get("id") or workflow_id))
        try:
            workflow_name = str(spec.get("id") or workflow_id)
            context: dict[str, Any] = {
                "inputs": input_payload,
                "vars": spec.get("vars") if isinstance(spec.get("vars"), dict) else {},
                "steps": {},
                "workflow": {
                    "run_id": run_id,
                    "id": workflow_name,
                    "elapsed_seconds": 0,
                    "workflow_digest": workflow_digest,
                    "parent_run_id": parent_run_id,
                    "parent_step_path": parent_step_path,
                    "call_depth": call_depth,
                    "call_stack": call_stack or [workflow_name],
                },
            }
            results = self._execute_workflow_steps(
                workflow_name,
                steps,
                spec=spec,
                dry_run=dry_run,
                run_dir=run_dir,
                context=context,
            )
        finally:
            if lock and lock.exists():
                lock.unlink()
        ended_at = _dt.datetime.now(_dt.UTC)
        failed_steps = self._flatten_workflow_failures(results)
        paused_steps = self._flatten_workflow_pauses(results)
        status = "dry_run" if dry_run else ("awaiting_approval" if paused_steps else ("done" if not failed_steps and len(results) == len(steps) else "failed"))
        next_step_index = None
        if paused_steps:
            paused_id = paused_steps[0].get("id")
            for index, result in enumerate(results):
                if result.get("id") == paused_id or self._workflow_result_paused(result):
                    next_step_index = index
                    break
        state = self._write_workflow_state(
            run_dir,
            run_id=run_id,
            workflow_id=str(spec.get("id") or workflow_id),
            status=status,
            results=results,
            next_step_index=next_step_index,
            pending_approval_id=str(paused_steps[0].get("approval_id")) if paused_steps and paused_steps[0].get("approval_id") else None,
            workflow_digest=workflow_digest,
        )
        context_for_outputs: dict[str, Any] = {
            "inputs": input_payload,
            "vars": spec.get("vars") if isinstance(spec.get("vars"), dict) else {},
            "steps": {},
            "workflow": {"run_id": run_id, "id": spec.get("id") or workflow_id, "workflow_digest": workflow_digest},
        }
        self._record_saved_workflow_results(context_for_outputs, results)
        output = self._workflow_outputs(spec, context_for_outputs)
        schema_errors = self._validate_simple_schema(output, spec.get("outputs_schema"), path="output")
        if schema_errors and status not in {"dry_run", "awaiting_approval"}:
            status = "failed"
        payload = {
            "status": status,
            "run_id": run_id,
            "workflow": spec.get("id") or workflow_id,
            "parent_run_id": parent_run_id,
            "parent_step_path": parent_step_path,
            "call_depth": call_depth,
            "input_digest": input_digest,
            "workflow_digest": workflow_digest,
            "path": str(run_dir.relative_to(self.project_path)),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
            "failed_step": failed_steps[0]["id"] if failed_steps else None,
            "pending_approval_id": state.get("pending_approval_id"),
            "output": output,
            "schema_errors": schema_errors,
            "steps": results,
            "validation": validation,
        }
        (run_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload

    def workflow_execute(self, workflow_id: str, *, dry_run: bool = False, force: bool = False, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        shown = self.workflow_show(workflow_id)
        if not shown.get("materialized", True):
            return {
                "status": "not_materialized",
                "workflow": workflow_id,
                "message": "Run `krail --local workflow init` first so the workflow spec is repo-backed and reviewable.",
                "next_action": shown.get("next_action") or f"krail --local workflow init {workflow_id}",
                "template": shown.get("template"),
            }
        return self._execute_workflow_spec(
            workflow_id,
            shown["workflow"],
            validation_path=shown["path"],
            dry_run=dry_run,
            force=force,
            inputs=inputs,
        )

    def mount_workflow_execute(
        self,
        mount_id: str,
        workflow_id: str,
        *,
        dry_run: bool = False,
        force: bool = False,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mount, project = self._resolve_mount_project(mount_id)
        result = project.execute_workflow(workflow_id, dry_run=dry_run, force=force, inputs=inputs)
        return self._mount_proxy_result(result, mount=mount.id if mount else None, project_slug=project.slug)

    def workflow_resume(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        run_dir = self._workflow_run_dir(run_id)
        state_path = run_dir / "state.json"
        if not state_path.exists():
            raise FileNotFoundError(f"Workflow state not found for run: {run_id}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") != "awaiting_approval":
            return {"status": "not_awaiting_approval", "run_id": run_id, "state": state}
        spec = yaml.safe_load((run_dir / "workflow.yaml").read_text(encoding="utf-8")) or {}
        if not isinstance(spec, dict):
            raise ValueError("workflow snapshot must be a mapping")
        workflow_name = str(spec.get("id") or state.get("workflow") or run_id)
        validation_path = str((run_dir / "workflow.yaml").relative_to(self.project_path))
        validation = self._validate_workflow_spec(spec, path=validation_path)
        if not validation["ok"]:
            return {"status": "invalid", "run_id": run_id, "validation": validation}
        denied = self._authorize_workflow_action(
            workflow_name,
            spec=spec,
            validation_path=validation_path,
            action="execute",
        )
        if denied:
            denied["run_id"] = run_id
            return denied
        result_path = run_dir / "result.json"
        previous_payload = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        previous_results = previous_payload.get("steps") if isinstance(previous_payload.get("steps"), list) else []
        start_index = state.get("next_step_index")
        if not isinstance(start_index, int) or start_index < 0:
            start_index = len(previous_results)
        preserved_results = previous_results[:start_index]
        steps = spec.get("steps") or []
        lock: Path | None = None
        if not force:
            lock = self._acquire_workflow_lock(workflow_name)
        try:
            workflow_digest = state.get("workflow_digest") or self._approval_digest(spec)
            context: dict[str, Any] = {
                "inputs": spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {},
                "vars": spec.get("vars") if isinstance(spec.get("vars"), dict) else {},
                "steps": {},
                "workflow": {"run_id": state.get("run_id") or run_id, "id": workflow_name, "elapsed_seconds": 0, "workflow_digest": workflow_digest},
            }
            self._record_saved_workflow_results(context, preserved_results)
            resumed_results = self._execute_workflow_steps(
                workflow_name,
                steps[start_index:],
                spec=spec,
                dry_run=False,
                run_dir=run_dir,
                context=context,
                path=(f"resume{start_index}",),
            )
            results = [*preserved_results, *resumed_results]
        finally:
            if lock and lock.exists():
                lock.unlink()
        ended_at = _dt.datetime.now(_dt.UTC)
        failed_steps = self._flatten_workflow_failures(results)
        paused_steps = self._flatten_workflow_pauses(results)
        status = "awaiting_approval" if paused_steps else ("done" if not failed_steps and len(results) == len(steps) else "failed")
        next_step_index = None
        if paused_steps:
            paused_id = paused_steps[0].get("id")
            for index, result in enumerate(results):
                if result.get("id") == paused_id or self._workflow_result_paused(result):
                    next_step_index = index
                    break
        self._write_workflow_state(
            run_dir,
            run_id=str(state.get("run_id") or run_id),
            workflow_id=workflow_name,
            status=status,
            results=results,
            next_step_index=next_step_index,
            pending_approval_id=str(paused_steps[0].get("approval_id")) if paused_steps and paused_steps[0].get("approval_id") else None,
            workflow_digest=str(state.get("workflow_digest") or self._approval_digest(spec)),
        )
        payload = {
            "status": status,
            "run_id": state.get("run_id") or run_id,
            "workflow": workflow_name,
            "path": str(run_dir.relative_to(self.project_path)),
            "started_at": previous_payload.get("started_at"),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": previous_payload.get("duration_seconds"),
            "failed_step": failed_steps[0]["id"] if failed_steps else None,
            "pending_approval_id": paused_steps[0].get("approval_id") if paused_steps else None,
            "steps": results,
            "validation": validation,
        }
        result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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
        created = self.create_task(title, description=description, runner=runner, workflow=workflow_id, role="research")
        task = created["task"]
        workflow_spec, validation_path = self._workflow_authorization_context(workflow_id)
        denied = self._authorize_workflow_action(
            workflow_id,
            spec=workflow_spec,
            validation_path=validation_path,
            action="dispatch_agent",
        )
        if denied:
            task["status"] = "blocked"
            task["blocker"] = denied["message"]
            self._write_task(self.project_path / str(created["path"]), task)
            denied["task_id"] = task["id"]
            return denied
        if dry_run:
            return {"status": "created", "task": task, "dry_run": True}
        dispatch = self.dispatch_task(task["id"], runner=runner)
        return {"status": "dispatched", "task": task, "dispatch": dispatch}

    def mount_workflow_run(self, mount_id: str, workflow_id: str, *, runner: str = "auto", dry_run: bool = False) -> dict[str, Any]:
        mount, project = self._resolve_mount_project(mount_id)
        result = project.run_workflow(workflow_id, runner=runner, dry_run=dry_run)
        return self._mount_proxy_result(result, mount=mount.id if mount else None, project_slug=project.slug)


def shutil_which(executable: str) -> str | None:
    from shutil import which

    return which(executable)
