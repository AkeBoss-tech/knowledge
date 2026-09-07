import subprocess
from pathlib import Path

import pytest

from rail.shared_knowledge import SharedKnowledgeWorkspace


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(("git", *args), cwd=cwd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def checkout(remote: Path, destination: Path, user: str) -> Path:
    git("clone", "--branch", "main", str(remote), str(destination))
    git("config", "user.name", user, cwd=destination)
    git("config", "user.email", f"{user}@example.test", cwd=destination)
    return destination


def test_two_user_git_proposals_review_conflict_restart_and_revocation(tmp_path):
    remote = tmp_path / "canonical.git"
    git("init", "--bare", str(remote))
    owner = tmp_path / "owner"
    git("clone", str(remote), str(owner))
    git("config", "user.name", "owner", cwd=owner)
    git("config", "user.email", "owner@example.test", cwd=owner)
    (owner / "knowledge.md").write_text("base\n")
    git("add", "knowledge.md", cwd=owner)
    git("commit", "-m", "base", cwd=owner)
    git("push", "origin", "HEAD:main", cwd=owner)
    git("branch", "-M", "main", cwd=owner)
    alice = checkout(remote, tmp_path / "alice", "alice")
    bob = checkout(remote, tmp_path / "bob", "bob")
    state = tmp_path / "shared-state.json"
    workspace = SharedKnowledgeWorkspace(remote, state)
    workspace.grant_source("alice")
    workspace.grant_source("bob")
    first = workspace.propose(user_id="alice", checkout=alice, proposal_id="alice-edit", path="knowledge.md", content="alice reviewed change\n")
    second = workspace.propose(user_id="bob", checkout=bob, proposal_id="bob-edit", path="knowledge.md", content="bob competing change\n")
    assert first.base_commit == second.base_commit

    promoted = workspace.review_and_promote("alice-edit", reviewer_id="reviewer")
    conflicted = workspace.review_and_promote("bob-edit", reviewer_id="reviewer")
    assert promoted.status == "promoted"
    assert conflicted.status == "conflict"
    assert git("--git-dir", str(remote), "rev-parse", "refs/heads/main") == promoted.candidate_commit
    assert workspace.authorized_context("alice").files == (("knowledge.md", "alice reviewed change\n"),)
    assert workspace.export("bob")["lineage"] == (promoted.candidate_commit,)

    # Restart rebuilds only the disposable per-process cache from canonical Git
    # and persisted proposal metadata; it cannot promote the stale proposal.
    restarted = SharedKnowledgeWorkspace(remote, state)
    assert restarted.authorized_context("alice").canonical_commit == promoted.candidate_commit
    assert restarted.review_and_promote("bob-edit", reviewer_id="reviewer").status == "conflict"
    # A live instance must reload durable grants before serving its cached
    # context, so another instance's revocation takes effect immediately.
    cached_alice = restarted.authorized_context("alice")
    assert cached_alice.files
    other_instance = SharedKnowledgeWorkspace(remote, state)
    other_instance.revoke_source("alice")
    with pytest.raises(PermissionError, match="revoked"):
        restarted.authorized_context("alice")
    other_instance.grant_source("alice")
    restarted.revoke_source("bob")
    with pytest.raises(PermissionError, match="revoked"):
        restarted.authorized_context("bob")
    with pytest.raises(PermissionError, match="revoked"):
        restarted.export("bob")
    assert restarted.authorized_context("alice").files == (("knowledge.md", "alice reviewed change\n"),)
