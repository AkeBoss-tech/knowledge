import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from krail.provider.v1 import ResourceRef
from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    MemoryRevocationRegistry,
    PacketRequestBinding,
)
from rail.shared_knowledge import (
    SharedKnowledgeError,
    SharedKnowledgeWorkspace,
    SignedSharedKnowledgeActionAuthorizer,
    shared_knowledge_action_request_digest,
)


class LiveActionAuthorizer:
    """A stand-in for the caller's signed live authorization adapter."""

    def __init__(self, granted: set[str]):
        self.granted = granted
        self.denied_resource_ids: set[str] = set()
        self.denied_actions: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str, tuple[str, str, str, str, str]]] = []

    def authorize(self, action, ref, *, subject_id):
        self.calls.append((action, subject_id, ref.exact_key))
        if (
            subject_id not in self.granted
            or ref.resource_id in self.denied_resource_ids
            or (action, ref.resource_id) in self.denied_actions
        ):
            raise PermissionError("source grant is revoked or absent")


class SignedLiveActionAuthorizer:
    """Caller-side resolver that issues one exact signed context per request."""

    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    capability_digest = "sha256:" + "c" * 64

    def __init__(self, authority: AccessContextAuthority, granted: set[str]):
        self.authority = authority
        self.granted = granted
        self.contexts: dict[tuple[str, str, str], object] = {}
        self.issue_count = 0

    def resolve(self, subject_id: str, action: str, ref: ResourceRef):
        if subject_id not in self.granted:
            return None
        key = (subject_id, action, ref.exact_key)
        context = self.contexts.get(key)
        if context is None:
            claims = AccessClaims(
                issuer="https://control.example.test",
                tenant_id="tenant-a",
                project_id="project-a",
                subject=subject_id,
                delegator="control-plane",
                delegation_id=f"delegation/{subject_id}",
                capability_id="shared-knowledge",
                capability_version="1.0.0",
                capability_digest=self.capability_digest,
                actions=(action,),
                source_ids=(ref.resource_id,),
                classifications=("internal",),
                policy_digest="sha256:" + "d" * 64,
                issued_at=self.now - timedelta(minutes=1),
                not_before=self.now - timedelta(seconds=1),
                expires_at=self.now + timedelta(hours=1),
                nonce=f"nonce-{self.issue_count}",
            )
            self.issue_count += 1
            context = self.authority.issue(claims, key_id="key-1")
            binding = self.authority.issue_packet_request_binding(
                PacketRequestBinding(
                    access_context_digest=context.context_digest,
                    tenant_id="tenant-a",
                    project_id="project-a",
                    capability_id="shared-knowledge",
                    capability_version="1.0.0",
                    capability_digest=self.capability_digest,
                    request_digest=shared_knowledge_action_request_digest(action, ref),
                    purpose="shared-knowledge-action",
                    scope=ref.authority,
                    issued_at=self.now - timedelta(minutes=1),
                    not_before=self.now - timedelta(seconds=1),
                    expires_at=self.now + timedelta(hours=1),
                    nonce=f"binding-{self.issue_count}",
                ),
                key_id="key-1",
            )
            self.contexts[key] = (context, binding)
        return self.contexts[key]

    def revoke_current(self, subject_id: str) -> None:
        for (subject, _action, _ref), (context, _binding) in self.contexts.items():
            if subject == subject_id:
                self.authority.revocations.revoke_context(
                    context.context_digest, revoked_at=self.now
                )

    def fresh_grant(self, subject_id: str) -> None:
        self.contexts = {
            key: context
            for key, context in self.contexts.items()
            if key[0] != subject_id
        }


def signed_authorizer(granted: set[str]) -> SignedLiveActionAuthorizer:
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"key-1": b"shared-knowledge-key"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    return SignedLiveActionAuthorizer(authority, granted)


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
    # A second cached reader and a restarted reader both key derived results to
    # canonical head, so the content-free tombstone exposes no deleted bytes
    # through context, search, export, or lineage.
    second_reader = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    assert second_reader.authorized_context("alice").files == ()
    assert second_reader.search("alice", "alice") == ()
    assert second_reader.export("alice")["files"] == {}
    assert SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer).export("alice")["files"] == {}
    source_digest = restarted._digest(restarted._remote_ref("refs/heads/main"))
    preview = restarted.preview_mode_transition(owner_id="owner", transition_id="to-local", to_mode="local-canonical-git", source_id=remote.resolve().as_uri(), source_digest=source_digest)
    assert Path(restarted.state_path.with_name(restarted.state_path.name + ".backups") / "transition-to-local.bundle").exists()
    with pytest.raises(Exception, match="already exists"):
        restarted.preview_mode_transition(owner_id="owner", transition_id="to-local", to_mode="local-canonical-git", source_id=remote.resolve().as_uri(), source_digest=source_digest)
    assert preview.to_mode == "local-canonical-git"
    assert restarted._validated_transition(preview, owner_id="owner")["owner_id"] == "owner"
    forged = preview.__class__(**{**preview.__dict__, "source_digest": "sha256:" + "0" * 64})
    with pytest.raises(Exception, match="forged"):
        restarted._validated_transition(forged, owner_id="owner")
    grants.remove("owner")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.backup_inventory(owner_id="owner")
    with pytest.raises(PermissionError, match="revoked"):
        restarted._validated_transition(preview, owner_id="owner")
    grants.add("owner")
    receipt = restarted.create_backup(owner_id="owner", backup_id="main-snapshot")
    assert receipt.bundle_digest.startswith("sha256:")
    restored = tmp_path / "restored.git"
    git("init", "--bare", str(restored))
    git("--git-dir", str(restored), "fetch", receipt.bundle_path, "refs/heads/main:refs/heads/main")
    assert git("--git-dir", str(restored), "rev-parse", "refs/heads/main") == receipt.canonical_commit
    Path(receipt.bundle_path).write_bytes(b"corrupt")
    with pytest.raises(Exception):
        restarted._run("git", "--git-dir", str(remote), "bundle", "verify", receipt.bundle_path)


def test_signed_authority_public_journey_rechecks_revoked_pending_review_after_restart(
    tmp_path, monkeypatch
):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "signed-state.json"
    grants = {"alice", "reviewer"}
    signed = signed_authorizer(grants)
    authorizer = SignedSharedKnowledgeActionAuthorizer(
        signed.authority,
        tenant_id="tenant-a",
        project_id="project-a",
        capability_id="shared-knowledge",
        capability_version="1.0.0",
        capability_digest=signed.capability_digest,
        context_for=signed.resolve,
        clock=lambda: signed.now,
    )
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    alice = checkout(remote, tmp_path / "signed-alice", "alice")
    proposal = workspace.propose(
        user_id="alice",
        checkout=alice,
        proposal_id="signed-review",
        path="knowledge.md",
        content="signed change\n",
    )

    persisted = workspace._persist_locked
    interrupted = True

    def persist_pending_then_interrupt(current_state):
        nonlocal interrupted
        persisted(current_state)
        if interrupted and current_state["proposals"][proposal.proposal_id]["status"] == "review-pending":
            interrupted = False
            raise OSError("interrupted before signed review CAS")

    monkeypatch.setattr(workspace, "_persist_locked", persist_pending_then_interrupt)
    with pytest.raises(OSError, match="interrupted"):
        workspace.review_and_promote(proposal.proposal_id, reviewer_id="reviewer")

    # The restart sees a durable pending receipt, but the reviewer context was
    # revoked while it was pending. A stale receipt cannot bypass live checks.
    signed.revoke_current("reviewer")
    restarted = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    with pytest.raises(PermissionError, match="source grant is revoked or absent"):
        restarted.review_and_promote(proposal.proposal_id, reviewer_id="reviewer")
    assert git("--git-dir", str(remote), "rev-parse", "refs/heads/main") == proposal.base_commit

    # A newly issued reviewer context is the only way to continue, and the
    # exact signed context still scopes the resulting public reads/exports.
    signed.fresh_grant("reviewer")
    promoted = restarted.review_and_promote(proposal.proposal_id, reviewer_id="reviewer")
    assert promoted.status == "promoted"
    assert restarted.authorized_context("alice").files == (("knowledge.md", "signed change\n"),)
    assert restarted.export("alice")["files"] == {"knowledge.md": "signed change\n"}

    # A resolver that returns no current grant is an explicit denial, including
    # after bytes have entered the disposable per-process cache.
    grants.remove("alice")
    with pytest.raises(PermissionError, match="source grant is revoked or absent"):
        restarted.export("alice")


def test_signed_authorizer_binds_full_resource_request_and_capability_version():
    ref = ResourceRef(
        authority="file:///expected",
        resource_type="git.repository",
        resource_id="knowledge.md",
        version="v1",
        digest="sha256:" + "a" * 64,
    )
    signed = signed_authorizer({"alice"})
    pair = signed.resolve("alice", "shared_knowledge.read", ref)
    authorizer = SignedSharedKnowledgeActionAuthorizer(
        signed.authority,
        tenant_id="tenant-a",
        project_id="project-a",
        capability_id="shared-knowledge",
        capability_version="1.0.0",
        capability_digest=signed.capability_digest,
        context_for=lambda *_: pair,
        clock=lambda: signed.now,
    )
    authorizer.authorize("shared_knowledge.read", ref, subject_id="alice")

    for mutation in (
        {"authority": "file:///different"},
        {"resource_type": "other.type"},
        {"resource_id": "other-resource"},
        {"version": "v2"},
        {"digest": "sha256:" + "b" * 64},
    ):
        with pytest.raises(PermissionError, match="source grant is revoked or absent"):
            authorizer.authorize(
                "shared_knowledge.read", ref.model_copy(update=mutation), subject_id="alice"
            )
    with pytest.raises(PermissionError, match="source grant is revoked or absent"):
        authorizer.authorize("shared_knowledge.search", ref, subject_id="alice")
    with pytest.raises(PermissionError, match="source grant is revoked or absent"):
        authorizer.authorize("shared_knowledge.read", ref, subject_id="bob")

    context, binding = pair
    wrong_claims = context.claims.model_copy(update={"capability_version": "2.0.0"})
    wrong_context = signed.authority.issue(wrong_claims, key_id="key-1")
    wrong_binding = signed.authority.issue_packet_request_binding(
        binding.binding.model_copy(
            update={
                "access_context_digest": wrong_context.context_digest,
                "capability_version": "2.0.0",
            }
        ),
        key_id="key-1",
    )
    wrong_version = SignedSharedKnowledgeActionAuthorizer(
        signed.authority,
        tenant_id="tenant-a",
        project_id="project-a",
        capability_id="shared-knowledge",
        capability_version="1.0.0",
        capability_digest=signed.capability_digest,
        context_for=lambda *_: (wrong_context, wrong_binding),
        clock=lambda: signed.now,
    )
    with pytest.raises(PermissionError, match="source grant is revoked or absent"):
        wrong_version.authorize("shared_knowledge.read", ref, subject_id="alice")

    for field in ("tenant_id", "project_id"):
        claims = context.claims.model_copy(update={field: "wrong"})
        altered = signed.authority.issue(claims, key_id="key-1")
        altered_binding = signed.authority.issue_packet_request_binding(
            binding.binding.model_copy(
                update={field: "wrong", "access_context_digest": altered.context_digest}
            ),
            key_id="key-1",
        )
        with pytest.raises(PermissionError, match="source grant is revoked or absent"):
            SignedSharedKnowledgeActionAuthorizer(
                signed.authority,
                tenant_id="tenant-a",
                project_id="project-a",
                capability_id="shared-knowledge",
                capability_version="1.0.0",
                capability_digest=signed.capability_digest,
                context_for=lambda *_: (altered, altered_binding),
                clock=lambda: signed.now,
            ).authorize("shared_knowledge.read", ref, subject_id="alice")

    wildcard_claims = context.claims.model_copy(update={"source_ids": ("*",)})
    wildcard_context = signed.authority.issue(wildcard_claims, key_id="key-1")
    wildcard_binding = signed.authority.issue_packet_request_binding(
        binding.binding.model_copy(update={"access_context_digest": wildcard_context.context_digest}),
        key_id="key-1",
    )
    with pytest.raises(PermissionError, match="source grant is revoked or absent"):
        SignedSharedKnowledgeActionAuthorizer(
            signed.authority,
            tenant_id="tenant-a",
            project_id="project-a",
            capability_id="shared-knowledge",
            capability_version="1.0.0",
            capability_digest=signed.capability_digest,
            context_for=lambda *_: (wildcard_context, wildcard_binding),
            clock=lambda: signed.now,
        ).authorize("shared_knowledge.read", ref, subject_id="alice")

    for field, value in (("purpose", "wrong-purpose"), ("scope", "file:///wrong")):
        altered_binding = signed.authority.issue_packet_request_binding(
            binding.binding.model_copy(update={field: value}), key_id="key-1"
        )
        with pytest.raises(PermissionError, match="source grant is revoked or absent"):
            SignedSharedKnowledgeActionAuthorizer(
                signed.authority,
                tenant_id="tenant-a",
                project_id="project-a",
                capability_id="shared-knowledge",
                capability_version="1.0.0",
                capability_digest=signed.capability_digest,
                context_for=lambda *_: (context, altered_binding),
                clock=lambda: signed.now,
            ).authorize("shared_knowledge.read", ref, subject_id="alice")


@pytest.mark.parametrize("failure", ["expired", "revoked", "missing", "malformed"])
def test_signed_authorizer_denies_expired_revoked_or_missing_current_context(failure):
    ref = ResourceRef(
        authority="file:///expected",
        resource_type="git.repository",
        resource_id="knowledge.md",
        version="v1",
        digest="sha256:" + "a" * 64,
    )
    signed = signed_authorizer({"alice"})
    pair = signed.resolve("alice", "shared_knowledge.read", ref)
    clock = lambda: signed.now
    context, _binding = pair
    if failure == "expired":
        clock = lambda: signed.now + timedelta(hours=2)
    elif failure == "revoked":
        signed.authority.revocations.revoke_context(
            context.context_digest, revoked_at=signed.now
        )
    if failure == "missing":
        resolver = None
    elif failure == "malformed":
        resolver = lambda *_: ("not-a-signed-context",)
    else:
        resolver = lambda *_: pair
    authorizer = SignedSharedKnowledgeActionAuthorizer(
        signed.authority,
        tenant_id="tenant-a",
        project_id="project-a",
        capability_id="shared-knowledge",
        capability_version="1.0.0",
        capability_digest=signed.capability_digest,
        context_for=resolver,
        clock=clock,
    )
    with pytest.raises(PermissionError, match="source grant is revoked or absent"):
        authorizer.authorize("shared_knowledge.read", ref, subject_id="alice")


def test_signed_authority_two_user_conflict_promotion_and_other_conflict(tmp_path):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "signed-two-user-state.json"
    grants = {"alice", "bob", "reviewer"}
    signed = signed_authorizer(grants)
    authorizer = SignedSharedKnowledgeActionAuthorizer(
        signed.authority,
        tenant_id="tenant-a",
        project_id="project-a",
        capability_id="shared-knowledge",
        capability_version="1.0.0",
        capability_digest=signed.capability_digest,
        context_for=signed.resolve,
        clock=lambda: signed.now,
    )
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    second = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    alice = checkout(remote, tmp_path / "signed-two-alice", "alice")
    bob = checkout(remote, tmp_path / "signed-two-bob", "bob")
    barrier = threading.Barrier(2)

    def propose(instance, **kwargs):
        barrier.wait(timeout=5)
        return instance.propose(**kwargs)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            propose,
            workspace,
            user_id="alice",
            checkout=alice,
            proposal_id="signed-alice-edit",
            path="knowledge.md",
            content="alice signed change\n",
        )
        second_future = executor.submit(
            propose,
            second,
            user_id="bob",
            checkout=bob,
            proposal_id="signed-bob-edit",
            path="knowledge.md",
            content="bob signed competing change\n",
        )
        first, competing = first_future.result(), second_future.result()
    assert first.base_commit == competing.base_commit
    promoted = workspace.review_and_promote(first.proposal_id, reviewer_id="reviewer")
    conflicted = second.review_and_promote(competing.proposal_id, reviewer_id="reviewer")
    assert promoted.status == "promoted"
    assert conflicted.status == "conflict"
    assert workspace.authorized_context("alice").files == (
        ("knowledge.md", "alice signed change\n"),
    )
    assert workspace.search("bob", "alice signed") == (
        ("knowledge.md", "alice signed change\n"),
    )


def _bootstrap_remote(tmp_path: Path) -> Path:
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
    return remote


def test_backup_inventory_and_exact_prune_retention_journey(
    tmp_path, monkeypatch
):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    grants = {"owner", "alice", "reviewer"}
    authorizer = LiveActionAuthorizer(grants)
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)

    private_backup = workspace.create_backup(
        owner_id="owner", backup_id="private-before-tombstone"
    )
    private_bundle = Path(private_backup.bundle_path)
    assert private_bundle.is_file()

    alice = checkout(remote, tmp_path / "alice", "alice")
    tombstone = workspace.propose(
        user_id="alice",
        checkout=alice,
        proposal_id="remove-private-content",
        path="knowledge.md",
        content=None,
    )
    workspace.review_and_promote(tombstone.proposal_id, reviewer_id="reviewer")
    assert workspace.authorized_context("alice").files == ()
    assert workspace.search("alice", "base") == ()
    assert workspace.export("alice")["files"] == {}

    current = workspace._remote_ref("refs/heads/main")
    transition = workspace.preview_mode_transition(
        owner_id="owner",
        transition_id="retention-pending",
        to_mode=workspace.local_mode,
        source_id=remote.resolve().as_uri(),
        source_digest=workspace._digest(current),
    )
    transition_backup_id = "transition-retention-pending"
    transition_bundle = state.with_name(state.name + ".backups") / (
        transition_backup_id + ".bundle"
    )
    assert transition_bundle.is_file()

    inventory = workspace.backup_inventory(owner_id="owner")
    assert inventory.retention_policy == "explicit-owner-prune-only"
    assert inventory.maximum_managed_backups == 128
    assert inventory.automatic_expiry is False
    assert inventory.canonical_git_history_may_retain_prior_content is True
    assert inventory.external_clones_may_retain_prior_content is True
    items = {item.backup_id: item for item in inventory.backups}
    assert set(items) == {private_backup.backup_id, transition_backup_id}
    assert all(item.may_retain_prior_content for item in items.values())
    assert items[private_backup.backup_id].eligible_for_prune is True
    assert items[transition_backup_id].eligible_for_prune is False
    assert items[transition_backup_id].protected_by_transitions == (
        transition.transition_id,
    )

    grants.remove("owner")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.prune_backup(
            owner_id="owner",
            backup_id=private_backup.backup_id,
            expected_digest=private_backup.bundle_digest,
        )
    assert private_bundle.is_file()
    grants.add("owner")

    with pytest.raises(SharedKnowledgeError, match="digest does not match"):
        workspace.prune_backup(
            owner_id="owner",
            backup_id=private_backup.backup_id,
            expected_digest="sha256:" + "0" * 64,
        )
    assert private_bundle.is_file()

    with workspace._locked_state() as persisted:
        original_path = persisted["backups"][private_backup.backup_id][
            "bundle_path"
        ]
        persisted["backups"][private_backup.backup_id]["bundle_path"] = str(
            transition_bundle
        )
    with pytest.raises(SharedKnowledgeError, match="path is unsafe"):
        workspace.prune_backup(
            owner_id="owner",
            backup_id=private_backup.backup_id,
            expected_digest=private_backup.bundle_digest,
        )
    with workspace._locked_state() as persisted:
        persisted["backups"][private_backup.backup_id][
            "bundle_path"
        ] = original_path

    saved_bundle = private_bundle.with_suffix(".saved")
    private_bundle.rename(saved_bundle)
    private_bundle.symlink_to(transition_bundle)
    with pytest.raises(SharedKnowledgeError, match="path is unsafe"):
        workspace.prune_backup(
            owner_id="owner",
            backup_id=private_backup.backup_id,
            expected_digest=private_backup.bundle_digest,
        )
    private_bundle.unlink()
    saved_bundle.rename(private_bundle)

    unlink_verified = workspace._unlink_backup_bundle

    def substitute_after_verification(path, expected_identity):
        path.rename(saved_bundle)
        path.hardlink_to(transition_bundle)
        try:
            unlink_verified(path, expected_identity)
        finally:
            path.unlink(missing_ok=True)
            saved_bundle.rename(path)

    monkeypatch.setattr(
        workspace, "_unlink_backup_bundle", substitute_after_verification
    )
    with pytest.raises(SharedKnowledgeError, match="changed before prune"):
        workspace.prune_backup(
            owner_id="owner",
            backup_id=private_backup.backup_id,
            expected_digest=private_backup.bundle_digest,
        )
    assert private_bundle.is_file()
    assert transition_bundle.is_file()
    monkeypatch.setattr(workspace, "_unlink_backup_bundle", unlink_verified)

    with pytest.raises(SharedKnowledgeError, match="mode transition"):
        workspace.prune_backup(
            owner_id="owner",
            backup_id=transition_backup_id,
            expected_digest=items[transition_backup_id].bundle_digest,
        )
    assert transition_bundle.is_file()

    persist = workspace._persist_locked

    def unbounded_read_forbidden(_path):
        raise AssertionError("backup lifecycle loaded the whole bundle")

    monkeypatch.setattr(Path, "read_bytes", unbounded_read_forbidden)

    def interrupt_after_unlink(persisted):
        prune = persisted.get("backup_prunes", {}).get(private_backup.backup_id)
        if prune is not None and prune.get("status") == "complete":
            raise OSError("interrupted after exact bundle unlink")
        persist(persisted)

    monkeypatch.setattr(workspace, "_persist_locked", interrupt_after_unlink)
    with pytest.raises(OSError, match="interrupted"):
        workspace.prune_backup(
            owner_id="owner",
            backup_id=private_backup.backup_id,
            expected_digest=private_backup.bundle_digest,
        )
    assert not private_bundle.exists()
    assert transition_bundle.is_file()

    restarted = SharedKnowledgeWorkspace(
        remote, state, action_authorizer=authorizer
    )
    receipt = restarted.prune_backup(
        owner_id="owner",
        backup_id=private_backup.backup_id,
        expected_digest=private_backup.bundle_digest,
    )
    assert receipt.backup_id == private_backup.backup_id
    assert receipt.bundle_digest == private_backup.bundle_digest
    assert set(receipt.__dict__) == {
        "backup_id",
        "canonical_commit",
        "bundle_digest",
        "pruned_at",
        "audit_digest",
    }
    assert "base" not in str(receipt.__dict__)
    assert restarted.prune_backup(
        owner_id="owner",
        backup_id=private_backup.backup_id,
        expected_digest=private_backup.bundle_digest,
    ) == receipt
    assert transition_bundle.is_file()
    assert "base" in git(
        "--git-dir", str(remote), "show", f"{private_backup.canonical_commit}:knowledge.md"
    )
    with restarted._locked_state() as persisted:
        assert persisted["mode"] == restarted.mode
        assert persisted["active_writer"] == "connected-git"


def test_pruned_backup_id_cannot_be_reused_after_restart_and_head_change(
    tmp_path,
):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    authorizer = LiveActionAuthorizer({"owner", "alice", "reviewer"})
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    original = workspace.create_backup(owner_id="owner", backup_id="reuse")
    original_receipt = workspace.prune_backup(
        owner_id="owner",
        backup_id=original.backup_id,
        expected_digest=original.bundle_digest,
    )
    assert not Path(original.bundle_path).exists()

    alice = checkout(remote, tmp_path / "reuse-alice", "alice")
    proposal = workspace.propose(
        user_id="alice",
        checkout=alice,
        proposal_id="changed-head-after-prune",
        path="knowledge.md",
        content="changed head\n",
    )
    workspace.review_and_promote(proposal.proposal_id, reviewer_id="reviewer")
    assert workspace._remote_ref("refs/heads/main") != original.canonical_commit

    restarted = SharedKnowledgeWorkspace(
        remote, state, action_authorizer=authorizer
    )
    with pytest.raises(SharedKnowledgeError, match="previously used"):
        restarted.create_backup(owner_id="owner", backup_id="reuse")

    replacement = restarted.create_backup(
        owner_id="owner", backup_id="changed-head-control"
    )
    assert replacement.canonical_commit != original.canonical_commit
    assert replacement.bundle_digest != original.bundle_digest
    assert Path(replacement.bundle_path).is_file()

    assert restarted.prune_backup(
        owner_id="owner",
        backup_id=original.backup_id,
        expected_digest=original.bundle_digest,
    ) == original_receipt
    assert Path(replacement.bundle_path).is_file()
    assert {
        item.backup_id
        for item in restarted.backup_inventory(owner_id="owner").backups
    } == {replacement.backup_id}


def test_export_requires_exact_promoted_proposal_lineage_authority_for_cached_and_reopened_readers(
    tmp_path,
):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    authorizer = LiveActionAuthorizer({"alice", "reviewer"})
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    alice = checkout(remote, tmp_path / "alice", "alice")
    proposal = workspace.propose(
        user_id="alice",
        checkout=alice,
        proposal_id="export-lineage",
        path="knowledge.md",
        content="reviewed\n",
    )
    promoted = workspace.review_and_promote(
        proposal.proposal_id, reviewer_id="reviewer"
    )
    assert workspace.authorized_context("alice").lineage == (
        promoted.candidate_commit,
    )

    authorizer.denied_actions.add(
        ("shared_knowledge.export", "proposal/" + proposal.proposal_id)
    )
    # Read remains independently authorized, but both the cached reader and a
    # newly opened reader must check export on the exact proposal identity.
    assert workspace.authorized_context("alice").files == (
        ("knowledge.md", "reviewed\n"),
    )
    with pytest.raises(PermissionError, match="revoked"):
        workspace.export("alice")
    reopened = SharedKnowledgeWorkspace(
        remote, state, action_authorizer=authorizer
    )
    assert reopened.authorized_context("alice").files == (
        ("knowledge.md", "reviewed\n"),
    )
    with pytest.raises(PermissionError, match="revoked"):
        reopened.export("alice")

    authorizer.denied_actions.clear()
    workspace.authorized_context("alice")
    with workspace._locked_state() as persisted:
        persisted["proposals"].pop(proposal.proposal_id)
    with pytest.raises(SharedKnowledgeError, match="lineage metadata"):
        workspace.export("alice")
    reopened_without_lineage = SharedKnowledgeWorkspace(
        remote, state, action_authorizer=authorizer
    )
    assert reopened_without_lineage.authorized_context("alice").files == (
        ("knowledge.md", "reviewed\n"),
    )
    with pytest.raises(SharedKnowledgeError, match="lineage metadata"):
        reopened_without_lineage.export("alice")


def test_export_requires_exact_local_write_lineage_authority_for_cached_and_reopened_readers(
    tmp_path,
):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    authorizer = LiveActionAuthorizer({"owner", "alice"})
    workspace = _activate_local(remote, state, authorizer)
    alice = checkout(remote, tmp_path / "alice", "alice")
    head = workspace._remote_ref("refs/heads/main")
    record = workspace.write_local(
        user_id="alice",
        checkout=alice,
        write_id="export-local-lineage",
        path="knowledge.md",
        content="local\n",
        expected_head=head,
        expected_generation=1,
    )
    assert workspace.authorized_context("alice").lineage == (
        record.candidate_commit,
    )

    authorizer.denied_actions.add(
        ("shared_knowledge.export", "local/" + record.write_id)
    )
    assert workspace.authorized_context("alice").files == (
        ("knowledge.md", "local\n"),
    )
    with pytest.raises(PermissionError, match="revoked"):
        workspace.export("alice")
    reopened = SharedKnowledgeWorkspace(
        remote, state, action_authorizer=authorizer
    )
    assert reopened.authorized_context("alice").files == (
        ("knowledge.md", "local\n"),
    )
    with pytest.raises(PermissionError, match="revoked"):
        reopened.export("alice")


def test_activation_switches_mode_and_fences_stale_connected_writers(tmp_path):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    grants = {"owner", "alice", "reviewer"}
    authorizer = LiveActionAuthorizer(grants)
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    stale_connected_instance = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    source_digest = workspace._digest(workspace._remote_ref("refs/heads/main"))
    preview = workspace.preview_mode_transition(
        owner_id="owner", transition_id="go-local", to_mode="local-canonical-git",
        source_id=remote.resolve().as_uri(), source_digest=source_digest,
    )

    activated = workspace.commit_mode_transition(preview, owner_id="owner")
    assert activated.to_mode == "local-canonical-git"

    # A durably persisted switch is visible to every reader of the state
    # file, not just the instance that performed it.
    with workspace._locked_state() as persisted:
        assert persisted["mode"] == "local-canonical-git"
        assert persisted["active_writer"] == "local-git"
        assert persisted["writer_generation"] == 1
        assert persisted["transitions"]["go-local"]["activated"] is True

    # A single transition can only ever be activated once.
    with pytest.raises(Exception, match="already activated"):
        workspace.commit_mode_transition(preview, owner_id="owner")

    # Connected proposal/review is denied post-switch, including from a
    # workspace object constructed before the switch happened (no in-memory
    # mode caching -- every mutation re-reads durable state under the lock).
    alice = checkout(remote, tmp_path / "alice", "alice")
    with pytest.raises(SharedKnowledgeError, match="connected-git canonical writer is disabled"):
        workspace.propose(user_id="alice", checkout=alice, proposal_id="late-edit", path="knowledge.md", content="no\n")
    with pytest.raises(SharedKnowledgeError, match="connected-git canonical writer is disabled"):
        stale_connected_instance.propose(user_id="alice", checkout=alice, proposal_id="late-edit", path="knowledge.md", content="no\n")

    # A restarted process observes the same durable switch.
    restarted = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    with pytest.raises(SharedKnowledgeError, match="connected-git canonical writer is disabled"):
        restarted.review_and_promote("late-edit", reviewer_id="reviewer")

    reverse_digest = workspace._digest(workspace._remote_ref("refs/heads/main"))
    reverse = workspace.preview_mode_transition(
        owner_id="owner", transition_id="back-connected", to_mode=workspace.mode,
        source_id=remote.resolve().as_uri(), source_digest=reverse_digest,
    )
    workspace.commit_mode_transition(reverse, owner_id="owner")
    with workspace._locked_state() as persisted:
        assert persisted["mode"] == workspace.mode
        assert persisted["active_writer"] == "connected-git"
        assert persisted["writer_generation"] == 2
    with pytest.raises(SharedKnowledgeError, match="local-git canonical writer is disabled"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="stale-local", path="knowledge.md", content="no\n", expected_head=workspace._remote_ref("refs/heads/main"), expected_generation=1)
    fresh = checkout(remote, tmp_path / "fresh-connected", "alice")
    assert workspace.propose(user_id="alice", checkout=fresh, proposal_id="connected-again", path="knowledge.md", content="back\n").status == "proposed"


def test_activation_rejects_stale_head_and_corrupt_backup(tmp_path):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    grants = {"owner", "alice"}
    authorizer = LiveActionAuthorizer(grants)
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    source_digest = workspace._digest(workspace._remote_ref("refs/heads/main"))

    stale_preview = workspace.preview_mode_transition(
        owner_id="owner", transition_id="stale-head", to_mode="local-canonical-git",
        source_id=remote.resolve().as_uri(), source_digest=source_digest,
    )
    alice = checkout(remote, tmp_path / "alice", "alice")
    proposal = workspace.propose(user_id="alice", checkout=alice, proposal_id="moves-head", path="knowledge.md", content="moved on\n")
    workspace.review_and_promote("moves-head", reviewer_id="alice")
    assert workspace._remote_ref("refs/heads/main") == proposal.candidate_commit
    with pytest.raises(SharedKnowledgeError, match="stale"):
        workspace.commit_mode_transition(stale_preview, owner_id="owner")
    with workspace._locked_state() as persisted:
        assert persisted["mode"] == workspace.mode
        assert persisted["writer_generation"] == 0


def test_activation_refuses_pending_review_until_connected_recovery(tmp_path):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    authorizer = LiveActionAuthorizer({"owner", "alice", "reviewer"})
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    alice = checkout(remote, tmp_path / "alice", "alice")
    proposal = workspace.propose(user_id="alice", checkout=alice, proposal_id="pending-review", path="knowledge.md", content="pending\n")
    with workspace._locked_state() as persisted:
        item = persisted["proposals"][proposal.proposal_id]
        item["status"] = "review-pending"
        item["reviewer_id"] = "reviewer"
        item["reviewer_receipt"] = workspace._digest("reviewer:pending-review:" + proposal.candidate_commit)
    digest = workspace._digest(workspace._remote_ref("refs/heads/main"))
    preview = workspace.preview_mode_transition(owner_id="owner", transition_id="blocked-pending", to_mode=workspace.local_mode, source_id=remote.resolve().as_uri(), source_digest=digest)
    with pytest.raises(SharedKnowledgeError, match="unresolved canonical effects"):
        workspace.commit_mode_transition(preview, owner_id="owner")
    assert workspace.review_and_promote("pending-review", reviewer_id="reviewer").status == "promoted"

    fresh_digest = workspace._digest(workspace._remote_ref("refs/heads/main"))
    corrupt_preview = workspace.preview_mode_transition(
        owner_id="owner", transition_id="corrupt-backup", to_mode="local-canonical-git",
        source_id=remote.resolve().as_uri(), source_digest=fresh_digest,
    )
    backups_dir = state.with_name(state.name + ".backups")
    Path(backups_dir / "transition-corrupt-backup.bundle").write_bytes(b"corrupt")
    with pytest.raises(SharedKnowledgeError, match="corrupt"):
        workspace.commit_mode_transition(corrupt_preview, owner_id="owner")
    with workspace._locked_state() as persisted:
        assert persisted["mode"] == workspace.mode
        assert persisted["writer_generation"] == 0


def test_local_direct_writer_cas_generation_head_and_revocation(tmp_path):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    grants = {"owner", "alice"}
    authorizer = LiveActionAuthorizer(grants)
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    source_digest = workspace._digest(workspace._remote_ref("refs/heads/main"))
    preview = workspace.preview_mode_transition(
        owner_id="owner", transition_id="go-local", to_mode="local-canonical-git",
        source_id=remote.resolve().as_uri(), source_digest=source_digest,
    )
    workspace.commit_mode_transition(preview, owner_id="owner")

    alice = checkout(remote, tmp_path / "alice", "alice")
    head = workspace._remote_ref("refs/heads/main")

    # Wrong durable generation is rejected before anything else runs.
    with pytest.raises(SharedKnowledgeError, match="generation is stale"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head=head, expected_generation=0)

    # Wrong exact expected head is rejected even with the correct generation.
    with pytest.raises(SharedKnowledgeError, match="head is stale"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head="0" * 40, expected_generation=1)

    record = workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head=head, expected_generation=1)
    assert record.candidate_commit == workspace._remote_ref("refs/heads/main")
    assert workspace.authorized_context("alice").files == (("knowledge.md", "v1\n"),)

    # A landed local-direct write is first-class provenance, exactly like a
    # promoted proposal: it shows up in lineage and is live-authorized under
    # its own exact resource id, not just the broad repository grant.
    assert workspace.export("alice")["lineage"] == (record.candidate_commit,)
    authorizer.denied_resource_ids.add("local/" + record.write_id)
    with pytest.raises(PermissionError, match="revoked"):
        workspace.authorized_context("alice")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.export("alice")
    authorizer.denied_resource_ids.discard("local/" + record.write_id)

    # A write id can only be used once (durable provenance ledger).
    with pytest.raises(SharedKnowledgeError, match="already exists"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v2\n", expected_head=record.candidate_commit, expected_generation=1)

    # Canonical main has moved past the caller's believed head (the first
    # write landed), so a second write naming the now-stale exact head is
    # rejected before any checkout mutation, regardless of what the caller's
    # own checkout happens to be reset to.
    alice_lagging = checkout(remote, tmp_path / "alice-lag", "alice")
    git("reset", "--hard", head, cwd=alice_lagging)
    with pytest.raises(SharedKnowledgeError, match="head is stale"):
        workspace.write_local(user_id="alice", checkout=alice_lagging, write_id="w2", path="knowledge.md", content="v2\n", expected_head=head, expected_generation=1)

    grants.remove("alice")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w3", path="knowledge.md", content="v3\n", expected_head=record.candidate_commit, expected_generation=1)
    grants.add("alice")

    # A restarted process enforces the same durable generation/head fencing.
    restarted = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    with pytest.raises(SharedKnowledgeError, match="generation is stale"):
        restarted.write_local(user_id="alice", checkout=alice, write_id="w4", path="knowledge.md", content="v4\n", expected_head=record.candidate_commit, expected_generation=0)
    tombstone = restarted.write_local(user_id="alice", checkout=alice, write_id="w5", path="knowledge.md", content=None, expected_head=record.candidate_commit, expected_generation=1)
    assert tombstone.deleted is True
    assert restarted.authorized_context("alice").files == ()


def _activate_local(remote, state, authorizer, owner_id="owner", transition_id="go-local"):
    workspace = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    source_digest = workspace._digest(workspace._remote_ref("refs/heads/main"))
    preview = workspace.preview_mode_transition(
        owner_id=owner_id, transition_id=transition_id, to_mode="local-canonical-git",
        source_id=remote.resolve().as_uri(), source_digest=source_digest,
    )
    workspace.commit_mode_transition(preview, owner_id=owner_id)
    return workspace


def test_write_local_cas_rejects_concurrent_head_change_even_to_an_ancestor(tmp_path, monkeypatch):
    """A plain non-force `git push` would accept this race; the atomic
    `update-ref <candidate> <expected_head>` compare-and-swap must not."""
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    authorizer = LiveActionAuthorizer({"owner", "alice"})
    workspace = _activate_local(remote, state, authorizer)
    alice = checkout(remote, tmp_path / "alice", "alice")
    head = workspace._remote_ref("refs/heads/main")

    first = workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head=head, expected_generation=1)
    assert first.candidate_commit == workspace._remote_ref("refs/heads/main")

    original_ref = workspace._local_write_ref

    def race_main_backward_then_ref(record):
        # Simulate an external actor moving canonical main back to `head`
        # (an ancestor of the candidate we are about to publish) in the
        # instant between our exact-head check and the CAS. A naive
        # non-force `git push` from a checkout descended from `head` would
        # treat this as a legitimate fast-forward and silently clobber the
        # first write; the atomic update-ref CAS must instead see that the
        # remote no longer holds the exact expected old value and fail.
        workspace._run("git", "--git-dir", str(remote), "update-ref", "refs/heads/main", head)
        return original_ref(record)

    monkeypatch.setattr(workspace, "_local_write_ref", race_main_backward_then_ref)
    with pytest.raises(SharedKnowledgeError, match="canonical head is stale"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w2", path="knowledge.md", content="v2\n", expected_head=first.candidate_commit, expected_generation=1)
    monkeypatch.setattr(workspace, "_local_write_ref", original_ref)

    # Canonical main must reflect the CAS rejection, not the race's ancestor
    # value: the rejected write's local commit was never applied to main.
    assert workspace._remote_ref("refs/heads/main") == head
    with workspace._locked_state() as persisted:
        assert persisted["local_commits"]["w2"]["status"] == "conflict"

    # Recovering the same write_id again must not repeat the CAS attempt
    # with a stale precondition; it fails closed as a conflict every time.
    with pytest.raises(SharedKnowledgeError, match="canonical head is stale"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w2", path="knowledge.md", content="v2\n", expected_head=first.candidate_commit, expected_generation=1)


def test_write_local_persists_pending_before_cas_and_recovers_after_crash(tmp_path, monkeypatch):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    authorizer = LiveActionAuthorizer({"owner", "alice"})
    workspace = _activate_local(remote, state, authorizer)
    alice = checkout(remote, tmp_path / "alice", "alice")
    head = workspace._remote_ref("refs/heads/main")

    real_persist = workspace._persist_locked

    def crash_after_pending_persist(state):
        pending = state["local_commits"].get("crash-write", {})
        if pending.get("status") == "pending":
            real_persist(state)
            raise OSError("injected crash after pending persist, before CAS")
        real_persist(state)

    monkeypatch.setattr(workspace, "_persist_locked", crash_after_pending_persist)
    with pytest.raises(OSError, match="injected"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="crash-write", path="knowledge.md", content="landed after crash\n", expected_head=head, expected_generation=1)
    monkeypatch.setattr(workspace, "_persist_locked", real_persist)

    # The crash landed strictly before the CAS: canonical main is untouched,
    # but the pending intent survived the crash durably.
    assert workspace._remote_ref("refs/heads/main") == head
    with workspace._locked_state() as persisted:
        assert persisted["local_commits"]["crash-write"]["status"] == "pending"

    # A retry with the identical write intent resumes from the durably
    # persisted pending record (same checkout, same already-made local
    # commit) instead of re-committing, and lands exactly once.
    recovered = workspace.write_local(user_id="alice", checkout=alice, write_id="crash-write", path="knowledge.md", content="landed after crash\n", expected_head=head, expected_generation=1)
    assert recovered.status == "landed"
    assert recovered.candidate_commit == workspace._remote_ref("refs/heads/main")
    assert workspace.authorized_context("alice").files == (("knowledge.md", "landed after crash\n"),)

    # A further retry after landing recovers the same receipt idempotently,
    # without attempting another commit, push, or CAS.
    idempotent = workspace.write_local(user_id="alice", checkout=alice, write_id="crash-write", path="knowledge.md", content="landed after crash\n", expected_head=head, expected_generation=1)
    assert idempotent == recovered


def test_write_local_resume_requires_live_authorization_before_any_effect(tmp_path, monkeypatch):
    """A pending record must never let a resumed retry push or CAS before
    live authorization is re-proven against the current repository and the
    exact prior record -- a revoked caller must be denied strictly before
    any Git mutation, not merely before the final CAS."""
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    grants = {"owner", "alice"}
    authorizer = LiveActionAuthorizer(grants)
    workspace = _activate_local(remote, state, authorizer)
    alice = checkout(remote, tmp_path / "alice", "alice")
    head = workspace._remote_ref("refs/heads/main")

    # Crash exactly at the first local-staging push: the pending record is
    # already durably persisted (from before this call), but nothing has
    # reached the remote yet.
    original_run = workspace._run
    crash_once = {"fired": False}

    def crash_at_first_staging_push(*args, cwd=None):
        if not crash_once["fired"] and "push" in args and any("local-staging" in a for a in args):
            crash_once["fired"] = True
            raise OSError("injected crash at first local-staging push")
        return original_run(*args, cwd=cwd)

    monkeypatch.setattr(workspace, "_run", crash_at_first_staging_push)
    with pytest.raises(OSError, match="injected"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head=head, expected_generation=1)
    monkeypatch.setattr(workspace, "_run", original_run)

    assert workspace._remote_ref("refs/heads/main") == head
    assert workspace._run("git", "--git-dir", str(remote), "for-each-ref", "refs/krail/local-staging") == ""
    with workspace._locked_state() as persisted:
        assert persisted["local_commits"]["w1"]["status"] == "pending"

    # A retry naming a different expected_head/generation than the pending
    # record's original preconditions must not be treated as the same
    # idempotent retry -- exact identity (including head and generation) is
    # required, not just matching content, so a stolen/guessed write_id
    # cannot recover another subject's in-flight receipt on a plausible
    # guess.
    with pytest.raises(SharedKnowledgeError, match="already exists"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head="0" * 40, expected_generation=1)
    assert workspace._remote_ref("refs/heads/main") == head
    assert workspace._run("git", "--git-dir", str(remote), "for-each-ref", "refs/krail/local-staging") == ""

    # A revoked subject retrying the identical write must be denied before
    # any resumed effect: before the staging push, before the main CAS, and
    # before recovery is even allowed to touch the pending record's status.
    grants.remove("alice")
    with pytest.raises(PermissionError, match="revoked"):
        workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head=head, expected_generation=1)
    assert workspace._remote_ref("refs/heads/main") == head
    assert workspace._run("git", "--git-dir", str(remote), "for-each-ref", "refs/krail/local-staging") == ""
    with workspace._locked_state() as persisted:
        assert persisted["local_commits"]["w1"]["status"] == "pending"
    grants.add("alice")

    # Restoring the grant lets the identical retry resume and land normally
    # -- live-authorized recovery still works once authorization is live
    # again.
    recovered = workspace.write_local(user_id="alice", checkout=alice, write_id="w1", path="knowledge.md", content="v1\n", expected_head=head, expected_generation=1)
    assert recovered.status == "landed"
    assert recovered.candidate_commit == workspace._remote_ref("refs/heads/main")
    assert workspace.authorized_context("alice").files == (("knowledge.md", "v1\n"),)


def test_review_and_promote_gates_writer_before_proposal_lookup(tmp_path):
    remote = _bootstrap_remote(tmp_path)
    state = tmp_path / "shared-state.json"
    authorizer = LiveActionAuthorizer({"owner", "reviewer"})
    _activate_local(remote, state, authorizer)

    # In local-canonical-git mode there is no connected-git reviewer at all,
    # so a lookup for a proposal id that was never created must still report
    # the writer as disabled, not leak an "unknown proposal" message that
    # would imply connected review is otherwise live.
    restarted = SharedKnowledgeWorkspace(remote, state, action_authorizer=authorizer)
    with pytest.raises(SharedKnowledgeError, match="connected-git canonical writer is disabled"):
        restarted.review_and_promote("never-existed", reviewer_id="reviewer")


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
