from __future__ import annotations

import json
from pathlib import Path

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime
from rail.permissions import AuthorizationResource, PermissionActor, PermissionPolicy


def test_permissions_are_public_by_default(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Permission Project", slug="permission-project")
    (root / "topics" / "public-note.md").write_text("# Public\n\npubliccodename is available.\n", encoding="utf-8")

    result = KnowledgeRuntime(root).search("publiccodename")

    assert [hit["path"] for hit in result["hits"]] == ["topics/public-note.md"]


def test_private_topic_is_filtered_and_audited_until_actor_role_allowed(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Permission Project", slug="permission-project")
    private = root / "topics" / "private-note.md"
    private.write_text(
        "---\n"
        "visibility: private\n"
        "allowed_roles:\n"
        "  - reviewer\n"
        "sensitivity:\n"
        "  - confidential\n"
        "---\n\n"
        "# Private\n\nprivatecodename should be restricted.\n",
        encoding="utf-8",
    )
    runtime = KnowledgeRuntime(root)

    denied = runtime.search("privatecodename")

    assert denied["hits"] == []
    audit_path = root / "research_plan" / "audit" / "access.jsonl"
    audit = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["decision"] == "denied"
    assert audit[-1]["target"] == "topics/private-note.md"

    monkeypatch.setenv("KRAIL_ROLES", "reviewer")
    allowed = KnowledgeRuntime(root).find("privatecodename", rag=False)

    assert allowed["results"][0]["path"] == "topics/private-note.md"
    audit = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["decision"] == "allowed"
    assert audit[-1]["sensitivity"] == ["confidential"]


def test_permissions_doctor_reports_sensitive_topics_without_visibility(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Permission Project", slug="permission-project")
    (root / "topics" / "sensitive.md").write_text(
        "---\n"
        "sensitivity:\n"
        "  - pii\n"
        "---\n\n"
        "# Sensitive\n",
        encoding="utf-8",
    )

    result = KnowledgeRuntime(root).permissions_doctor()

    assert result["public_by_default"] is True
    assert result["ok"] is False
    assert result["unlabeled_sensitive"] == ["topics/sensitive.md"]


def test_allowed_roles_restrict_even_without_visibility_flag(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Permission Project", slug="permission-project")
    (root / "topics" / "role-note.md").write_text(
        "---\n"
        "allowed_roles:\n"
        "  - reviewer\n"
        "---\n\n"
        "# Role Note\n\nrolecodename is only for reviewers.\n",
        encoding="utf-8",
    )

    denied = KnowledgeRuntime(root).search("rolecodename")
    assert denied["hits"] == []

    monkeypatch.setenv("KRAIL_ROLES", "reviewer")
    allowed = KnowledgeRuntime(root).search("rolecodename")
    assert allowed["hits"][0]["path"] == "topics/role-note.md"


def test_grep_and_files_read_respect_permissions(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Permission Project", slug="permission-project")
    (root / "topics" / "public-note.md").write_text("# Public\n\nsharedcodename is visible.\n", encoding="utf-8")
    (root / "topics" / "private-note.md").write_text(
        "---\n"
        "visibility: private\n"
        "allowed_roles:\n"
        "  - reviewer\n"
        "---\n\n"
        "# Private\n\nhidden codename is restricted.\n",
        encoding="utf-8",
    )

    runtime = KnowledgeRuntime(root)
    denied_grep = runtime.grep("codename")
    denied_read = runtime.files_read("topics/private-note.md")
    listing = runtime.files_list(paths=["topics"], recursive=True)

    assert [match["path"] for match in denied_grep["matches"]] == ["topics/public-note.md"]
    assert denied_grep["summary"]["denied_files"] == 1
    assert denied_read["status"] == "blocked"
    assert {item["path"] for item in listing["items"]} == {"topics", "topics/brief.md", "topics/inbox", "topics/public-note.md"}

    monkeypatch.setenv("KRAIL_ROLES", "reviewer")
    allowed_runtime = KnowledgeRuntime(root)
    allowed_grep = allowed_runtime.grep("codename")
    allowed_read = allowed_runtime.files_read("topics/private-note.md", start_line=1, lines=10)

    assert {match["path"] for match in allowed_grep["matches"]} == {"topics/public-note.md", "topics/private-note.md"}
    assert "hidden codename is restricted." in allowed_read["content"]


def test_authorize_returns_typed_public_by_default_decision(tmp_path: Path):
    policy = PermissionPolicy(tmp_path, actor=PermissionActor(id="alice", roles=("reader",)))

    decision = policy.authorize("read", "topics/public.md")

    assert decision.allowed is True
    assert decision.reason == "public_default"
    assert decision.decision == "allowed"
    assert decision.resource.kind == "path"
    assert decision.resource.target == "topics/public.md"
    assert decision.to_dict()["actor"]["id"] == "alice"


def test_explicit_deny_wins_over_allowlist_and_public_default(tmp_path: Path):
    policy = PermissionPolicy(tmp_path, actor=PermissionActor(id="alice", roles=("reviewer",)))

    decision = policy.authorize(
        "read",
        "topics/restricted.md",
        {
            "visibility": "public",
            "allowed_roles": ["reviewer"],
            "denied_roles": ["reviewer"],
        },
    )

    assert decision.allowed is False
    assert decision.reason == "role_denied"
    assert decision.audit_required is True


def test_path_rules_are_merged_before_record_metadata(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Permission Project", slug="permission-project")
    (root / "rail.yaml").write_text(
        "version: 1\n"
        "project:\n"
        "  name: Permission Project\n"
        "  slug: permission-project\n"
        "paths:\n"
        "  ontology_root: .ontology\n"
        "  topics_root: topics\n"
        "hydration:\n"
        "  ontology_file: .ontology/ontology.yaml\n"
        "  sources_dir: .ontology/sources\n"
        "  pipelines_dir: .ontology/pipelines\n"
        "agents:\n"
        "  roles_dir: agents\n"
        "permissions:\n"
        "  rules:\n"
        "    - path: topics/private/*\n"
        "      visibility: private\n"
        "      allowed_roles:\n"
        "        - reviewer\n",
        encoding="utf-8",
    )
    policy = PermissionPolicy(root, actor=PermissionActor(id="alice"))

    denied = policy.authorize("read", "topics/private/design.md")
    allowed = policy.authorize(
        "read",
        "topics/private/design.md",
        {"visibility": "public", "allowed_roles": []},
    )

    assert denied.allowed is False
    assert denied.reason == "allowlist_not_matched"
    assert allowed.allowed is True
    assert allowed.reason == "public_default"


def test_write_authorization_checks_path_scope(tmp_path: Path):
    policy = PermissionPolicy(tmp_path, actor=PermissionActor(id="alice"))

    denied = policy.authorize("write", "artifacts/report.md", {"write": ["topics/*"]})
    allowed = policy.authorize("write", "topics/report.md", {"write": ["topics/*"]})

    assert denied.allowed is False
    assert denied.reason == "write_path_not_allowed"
    assert allowed.allowed is True
    assert allowed.reason == "public_default"


def test_execute_authorization_checks_allowed_agents(tmp_path: Path):
    policy = PermissionPolicy(tmp_path, actor=PermissionActor(id="worker-1", type="agent", agent="codex"))

    denied = policy.authorize(
        "execute",
        AuthorizationResource.workflow("weekly-refresh", {"allowed_agents": ["claude"]}),
    )
    allowed = policy.authorize(
        "execute",
        AuthorizationResource.workflow("weekly-refresh", {"allowed_agents": ["codex"]}),
    )

    assert denied.allowed is False
    assert denied.reason == "agent_not_allowed"
    assert allowed.allowed is True
    assert allowed.reason == "actor_allowed"


def test_secret_authorization_uses_allow_and_deny_lists(tmp_path: Path):
    policy = PermissionPolicy(tmp_path, actor=PermissionActor(id="alice"))

    denied = policy.authorize(
        "read_secret",
        AuthorizationResource.secret("OPENAI_API_KEY", {"allow": ["OPENAI_*"], "deny": ["OPENAI_API_KEY"]}),
    )
    allowed = policy.authorize(
        "read_secret",
        AuthorizationResource.secret("OPENAI_BASE_URL", {"allow": ["OPENAI_*"]}),
    )

    assert denied.allowed is False
    assert denied.reason == "explicit_deny"
    assert denied.audit_required is True
    assert allowed.allowed is True
    assert allowed.reason == "public_default"
    assert allowed.audit_required is True
