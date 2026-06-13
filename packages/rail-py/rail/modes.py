from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_MODES: dict[str, dict[str, Any]] = {
    "research": {
        "id": "research",
        "name": "Research Knowledge Base",
        "description": "Strict evidence-backed research memory for papers, methods, datasets, experiments, claims, and open questions.",
        "default_pack": "research-intelligence",
        "topic_types": {
            "paper": ["summary", "claims", "evidence", "limitations", "open_questions"],
            "method": ["summary", "uses", "evidence", "limitations", "related_methods"],
            "question": ["current_answer", "evidence", "gaps", "next_actions"],
            "topic": ["summary", "key_facts", "evidence", "open_questions", "notes"],
        },
        "workflows": ["triage_inbox", "add_new_paper", "weekly_literature_refresh", "source_refresh"],
        "integrity": {
            "require_sources_for_claims": True,
            "stale_sources_block_promotion": True,
            "promotion_default": "needs_evidence",
        },
        "agent_rules": [
            "Use `krail --local search` before answering substantive research questions.",
            "Promote captures into stable topic pages with citations before treating them as durable knowledge.",
            "Register important synthesized statements as claim candidates before promotion.",
        ],
    },
    "company": {
        "id": "company",
        "name": "Company Brain",
        "description": "Operational memory for teams, systems, policies, workflows, metrics, decisions, owners, and stale docs.",
        "default_pack": "company-brain",
        "topic_types": {
            "team": ["summary", "owners", "systems", "workflows", "open_questions"],
            "system": ["summary", "owner", "users", "datasets", "risks", "sources"],
            "policy": ["summary", "applies_to", "owner", "evidence", "stale_warnings"],
            "topic": ["summary", "key_facts", "owners", "evidence", "open_questions", "notes"],
        },
        "workflows": ["triage_inbox", "initial_company_map", "company_profile_refresh", "source_review", "weekly_exec_brief"],
        "integrity": {
            "require_sources_for_claims": True,
            "stale_sources_block_promotion": True,
            "promotion_default": "needs_evidence",
        },
        "agent_rules": [
            "Separate observed facts from assumptions, owner guesses, and stale operational notes.",
            "Prefer updating existing team, system, policy, or workflow topics over creating loose files.",
            "Record source freshness and ownership gaps explicitly.",
        ],
    },
    "personal": {
        "id": "personal",
        "name": "Personal Knowledge Organizer",
        "description": "Lighter-weight organization for projects, areas, resources, ideas, documents, and random life/admin notes.",
        "default_pack": None,
        "topic_types": {
            "project": ["summary", "next_actions", "resources", "decisions", "notes"],
            "area": ["summary", "routines", "resources", "open_loops", "notes"],
            "resource": ["summary", "why_it_matters", "links", "notes"],
            "topic": ["summary", "key_points", "links", "open_loops", "notes"],
        },
        "workflows": ["triage_inbox", "weekly_review", "rag_refresh"],
        "integrity": {
            "require_sources_for_claims": False,
            "stale_sources_block_promotion": False,
            "promotion_default": "draft",
        },
        "agent_rules": [
            "Prefer simple topic hygiene over heavy evidence ceremony.",
            "Use inbox triage to decide whether a note becomes a project, area, resource, or archive item.",
            "Keep follow-up actions visible and avoid scattering notes across new folders.",
        ],
    },
    "software": {
        "id": "software",
        "name": "Software Architecture Map",
        "description": "Architecture memory for services, modules, APIs, databases, dependencies, decisions, incidents, and risks.",
        "default_pack": "software-architecture",
        "topic_types": {
            "service": ["summary", "interfaces", "dependencies", "owners", "risks", "decisions"],
            "module": ["summary", "responsibilities", "dependencies", "callers", "risks"],
            "decision": ["context", "decision", "consequences", "evidence", "follow_up"],
            "topic": ["summary", "key_facts", "dependencies", "decisions", "notes"],
        },
        "workflows": ["triage_inbox", "map_codebase", "capture_architecture_decision", "dependency_review"],
        "integrity": {
            "require_sources_for_claims": True,
            "stale_sources_block_promotion": True,
            "promotion_default": "needs_evidence",
        },
        "agent_rules": [
            "Prefer architecture topics and decisions over loose implementation notes.",
            "Link claims to source files, commands, issues, or docs whenever possible.",
            "Record unknown ownership and dependency risk as explicit gaps.",
        ],
    },
    "project": {
        "id": "project",
        "name": "Focused Project Workspace",
        "description": "Outcome-oriented workspace for a grant, launch, thesis, investigation, migration, or other bounded effort.",
        "default_pack": None,
        "topic_types": {
            "milestone": ["summary", "success_criteria", "dependencies", "risks", "next_actions"],
            "decision": ["context", "decision", "evidence", "consequences", "follow_up"],
            "artifact": ["summary", "inputs", "status", "verification", "open_questions"],
            "topic": ["summary", "key_facts", "evidence", "open_questions", "notes"],
        },
        "workflows": ["triage_inbox", "project_doctor", "rag_refresh", "release_readiness"],
        "integrity": {
            "require_sources_for_claims": True,
            "stale_sources_block_promotion": True,
            "promotion_default": "needs_evidence",
        },
        "agent_rules": [
            "Keep knowledge tied to milestones, decisions, artifacts, and closeout criteria.",
            "Use tasks and workflows for execution; use topics for durable knowledge.",
            "Record blockers as project state instead of burying them in session logs.",
        ],
    },
}


def get_mode(mode_id: str | None) -> dict[str, Any]:
    selected = mode_id or "research"
    if selected not in DEFAULT_MODES:
        raise ValueError(f"Unknown knowledge mode: {selected}")
    return deepcopy(DEFAULT_MODES[selected])

