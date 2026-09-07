import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rail.shared_knowledge import SharedKnowledgeWorkspace


class LiveActionAuthorizer:
    """A stand-in for the caller's signed live authorization adapter."""

    def __init__(self, granted: set[str]):
        self.granted = granted
        self.denied_resource_ids: set[str] = set()
        self.calls: list[tuple[str, str, tuple[str, str, str, str, str]]] = []

    def authorize(self, action, ref, *, subject_id):
        self.calls.append((action, subject_id, ref.exact_key))
        if subject_id not in self.granted or ref.resource_id in self.denied_resource_ids:
            raise PermissionError("source grant is revoked or absent")


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(("git", *args), cwd=cwd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def checkout(remote: Path, destination: Path, user: str) -> Path:
    git("clone", "--branch", "main", str(remote), str(destination))
    git("config", "user.name", user, cwd=destination)
    git("config", "user.email", f"{user}@example.test", cwd=destination)
    return destination


def test_two_user_git_proposals_review_conflict_restart_and_revocation(tmp_path, monkeypatch):
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
    grants = {"alice", "bob", "reviewer", "owner"}
    authorizer = LiveActionAuthorizer(grants)
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    second_workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    barrier = threading.Barrier(2)

    def propose(instance, **kwargs):
        barrier.wait(timeout=5)
        return instance.propose(**kwargs)

    # Independent workspaces enter at once. The state-file lock serializes
    # metadata mutation while both bare-remote branches remain valid.
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(propose, workspace, user_id="alice", checkout=alice, proposal_id="alice-edit", path="knowledge.md", content="alice reviewed change\n")
        second_future = executor.submit(propose, second_workspace, user_id="bob", checkout=bob, proposal_id="bob-edit", path="knowledge.md", content="bob competing change\n")
        first, second = first_future.result(), second_future.result()
    assert first.base_commit == second.base_commit

    grants.remove("reviewer")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.review_and_promote("alice-edit", reviewer_id="reviewer")
    assert git("--git-dir", str(remote), "rev-parse", "refs/heads/main") == first.base_commit
    grants.add("reviewer")
    persist = workspace._persist_locked

    def fail_after_git_cas(state):
        if state["proposals"]["alice-edit"]["status"] == "promoted":
            raise OSError("injected state write failure after Git CAS")
        persist(state)

    monkeypatch.setattr(workspace, "_persist_locked", fail_after_git_cas)
    with pytest.raises(OSError, match="injected"):
        workspace.review_and_promote("alice-edit", reviewer_id="reviewer")
    assert git("--git-dir", str(remote), "rev-parse", "refs/heads/main") != first.base_commit
    monkeypatch.setattr(workspace, "_persist_locked", persist)
    # The receipt was flushed as review-pending before CAS, so reopen recovery
    # promotes only that exact candidate under the same reviewer identity.
    promoted = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer).review_and_promote("alice-edit", reviewer_id="reviewer")
    conflicted = workspace.review_and_promote("bob-edit", reviewer_id="reviewer")
    assert promoted.status == "promoted"
    assert conflicted.status == "conflict"
    assert git("--git-dir", str(remote), "rev-parse", "refs/heads/main") == promoted.candidate_commit
    assert workspace.authorized_context("alice").files == (("knowledge.md", "alice reviewed change\n"),)
    assert workspace.export("bob")["lineage"] == (promoted.candidate_commit,)
    assert workspace.search("bob", "alice") == (("knowledge.md", "alice reviewed change\n"),)
    assert promoted.reviewer_receipt

    # A broad repository grant is insufficient once the exact file is revoked:
    # cached context, search, export, and lineage must all fail closed.
    authorizer.denied_resource_ids.add("file/knowledge.md")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.authorized_context("alice")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.search("alice", "alice")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.export("alice")
    authorizer.denied_resource_ids.clear()

    # Restart rebuilds only the disposable per-process cache from canonical Git
    # and persisted proposal metadata; it cannot promote the stale proposal.
    restarted = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    assert restarted.authorized_context("alice").canonical_commit == promoted.candidate_commit
    assert restarted.review_and_promote("bob-edit", reviewer_id="reviewer").status == "conflict"
    # A live instance must reload durable grants before serving its cached
    # context, so another instance's revocation takes effect immediately.
    cached_alice = restarted.authorized_context("alice")
    assert cached_alice.files
    other_instance = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    grants.remove("alice")
    with pytest.raises(PermissionError, match="revoked"):
        restarted.authorized_context("alice")
    grants.add("alice")
    grants.remove("bob")
    with pytest.raises(PermissionError, match="revoked"):
        restarted.authorized_context("bob")
    with pytest.raises(PermissionError, match="revoked"):
        restarted.export("bob")
    assert restarted.authorized_context("alice").files == (("knowledge.md", "alice reviewed change\n"),)
    assert any(action == "shared_knowledge.export" for action, _subject, _ref in authorizer.calls)
    deletion_checkout = checkout(remote, tmp_path / "deleter", "alice")
    tombstone = restarted.propose(user_id="alice", checkout=deletion_checkout, proposal_id="remove-knowledge", path="knowledge.md", content=None)
    assert tombstone.deleted is True
    assert restarted.review_and_promote("remove-knowledge", reviewer_id="reviewer").status == "promoted"
    assert restarted.authorized_context("alice").files == ()
    source_digest = "sha256:" + "1" * 64
    with pytest.raises(Exception, match="provisioned target adapter"):
        restarted.preview_mode_transition(owner_id="owner", transition_id="to-local", to_mode="local-canonical-git", source_id="canonical.git", source_digest=source_digest)
    with pytest.raises(Exception, match="provisioned target adapter"):
        restarted.preview_mode_transition(owner_id="owner", transition_id="hosted", to_mode="hosted-canonical-service", source_id="canonical.git", source_digest=source_digest)


def test_rejects_untrusted_checkout_and_authorizes_before_return(tmp_path):
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
    grants = {"alice", "reviewer"}
    workspace = SharedKnowledgeWorkspace(remote, tmp_path / "state.json", action_authorizer=LiveActionAuthorizer(grants))
    git("remote", "set-url", "origin", str(tmp_path / "different.git"), cwd=alice)
    with pytest.raises(Exception, match="configured canonical remote"):
        workspace.propose(user_id="alice", checkout=alice, proposal_id="wrong-origin", path="knowledge.md", content="no\n")

    symlink_checkout = checkout(remote, tmp_path / "symlink-alice", "alice")
    (symlink_checkout / "nested").mkdir()
    (symlink_checkout / "nested" / "link").symlink_to(tmp_path)
    with pytest.raises(Exception, match="symlink"):
        workspace.propose(user_id="alice", checkout=symlink_checkout, proposal_id="symlink", path="nested/link/knowledge.md", content="no\n")

    controlled = checkout(remote, tmp_path / "controlled-alice", "alice")
    git("config", "filter.untrusted.clean", "false", cwd=controlled)
    with pytest.raises(Exception, match="service-controlled"):
        workspace.propose(user_id="alice", checkout=controlled, proposal_id="unsafe-config", path="knowledge.md", content="no\n")
