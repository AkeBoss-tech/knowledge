from __future__ import annotations

import json
from pathlib import Path

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


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
