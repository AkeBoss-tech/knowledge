"""Connected canonical-Git shared knowledge with caller-owned live authority.

KRAIL does not issue grants. The embedding service supplies an authorizer that
re-evaluates every exact action against current signed access state. Git is the
sole canonical writer; JSON contains only crash-recoverable review metadata.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from krail.provider.v1 import ResourceRef


class SharedKnowledgeError(RuntimeError):
    pass


class SharedKnowledgeActionAuthorizer(Protocol):
    """Caller-owned, live, exact-resource action decision."""

    def authorize(self, action: str, ref: ResourceRef, *, subject_id: str) -> None:
        """Raise PermissionError if this action is currently denied or revoked."""


@dataclass(frozen=True)
class KnowledgeProposal:
    proposal_id: str
    user_id: str
    branch: str
    base_commit: str
    candidate_commit: str
    path: str
    content_digest: str
    status: str = "proposed"
    reviewer_receipt: str | None = None
    reviewer_id: str | None = None
    deleted: bool = False


@dataclass(frozen=True)
class AuthorizedKnowledgeContext:
    user_id: str
    canonical_commit: str
    files: tuple[tuple[str, str], ...]
    lineage: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeModeTransition:
    transition_id: str
    from_mode: str
    to_mode: str
    canonical_commit: str
    state_revision: int
    source_id: str
    source_digest: str
    backup_receipt: str


@dataclass(frozen=True)
class KnowledgeBackupReceipt:
    backup_id: str
    canonical_commit: str
    bundle_path: str
    bundle_digest: str


class SharedKnowledgeWorkspace:
    """A locked, restartable boundary around one configured bare Git remote."""

    mode = "connected-canonical-git-reviewed-changes"
    local_mode = "local-canonical-git"
    hosted_mode = "hosted-canonical-service"
    _PROPOSAL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

    def __init__(self, remote: str | Path, state_path: str | Path, *, action_authorizer: SharedKnowledgeActionAuthorizer) -> None:
        if action_authorizer is None:
            raise ValueError("a caller-owned live action authorizer is required")
        self.remote = Path(remote).resolve(strict=True)
        if not self.remote.is_dir() or not (self.remote / "HEAD").is_file():
            raise ValueError("remote must be an existing bare Git repository")
        self.state_path = Path(state_path)
        self.action_authorizer = action_authorizer
        self._cache: dict[tuple[str, str], AuthorizedKnowledgeContext] = {}
        with self._locked_state() as state:
            if state.get("mode", self.mode) not in {self.mode, self.local_mode}:
                raise SharedKnowledgeError("hosted canonical mode requires a real hosted adapter")
            state.setdefault("mode", self.mode)
            state.setdefault("active_writer", "connected-git" if state["mode"] == self.mode else "local-git")
            state.setdefault("revision", 0)
            state.setdefault("proposals", {})
            state.setdefault("backups", {})

    @staticmethod
    def _run(*args: str, cwd: Path | None = None) -> str:
        environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1"}
        result = subprocess.run(args, cwd=cwd, env=environment, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise SharedKnowledgeError(result.stderr.strip() or "git command failed")
        return result.stdout.strip()

    @contextmanager
    def _locked_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
            try:
                yield state
            except Exception:
                raise
            else:
                self._persist_locked(state)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _persist_locked(self, state: dict) -> None:
        """Durably save while the caller holds ``_locked_state``'s lock."""
        state["revision"] = int(state.get("revision", 0)) + 1
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)
        directory_fd = os.open(self.state_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _remote_ref(self, ref: str) -> str:
        return self._run("git", "--git-dir", str(self.remote), "rev-parse", ref)

    def _show(self, spec: str) -> str:
        result = subprocess.run(("git", "--git-dir", str(self.remote), "show", spec), check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise SharedKnowledgeError(result.stderr.strip() or "git show failed")
        return result.stdout

    @staticmethod
    def _digest(value: str) -> str:
        return "sha256:" + sha256(value.encode()).hexdigest()

    def _ref(self, resource_id: str, version: str, digest_value: str) -> ResourceRef:
        return ResourceRef(authority=self.remote.as_uri(), resource_type="git.repository", resource_id=resource_id, version=version, digest=digest_value)

    def _repository_ref(self, commit: str) -> ResourceRef:
        return self._ref("main", commit, self._digest(commit))

    def _proposal_ref(self, proposal: KnowledgeProposal) -> ResourceRef:
        return self._ref("proposal/" + proposal.proposal_id, proposal.candidate_commit, proposal.content_digest)

    def create_backup(self, *, owner_id: str, backup_id: str) -> KnowledgeBackupReceipt:
        """Create and verify an immutable, service-owned bundle of canonical main."""
        if not self._PROPOSAL_ID.fullmatch(backup_id):
            raise ValueError("backup id must be a bounded safe identifier")
        with self._locked_state() as state:
            backups = state.setdefault("backups", {})
            if backup_id in backups:
                raise SharedKnowledgeError("backup id already exists")
            commit = self._remote_ref("refs/heads/main")
            ref = self._repository_ref(commit)
            self._authorize("shared_knowledge.backup", ref, owner_id)
            directory = self.state_path.with_name(self.state_path.name + ".backups")
            if directory.is_symlink():
                raise SharedKnowledgeError("backup directory may not be a symlink")
            directory.mkdir(mode=0o700, exist_ok=True)
            bundle = directory / (backup_id + ".bundle")
            if bundle.exists() or bundle.is_symlink():
                raise SharedKnowledgeError("backup bundle path already exists or is unsafe")
            self._run("git", "--git-dir", str(self.remote), "bundle", "create", str(bundle), "refs/heads/main")
            self._run("git", "--git-dir", str(self.remote), "bundle", "verify", str(bundle))
            digest = "sha256:" + sha256(bundle.read_bytes()).hexdigest()
            receipt = KnowledgeBackupReceipt(backup_id, commit, str(bundle), digest)
            backups[backup_id] = asdict(receipt)
        self._authorize("shared_knowledge.backup", ref, owner_id)
        return receipt

    def preview_mode_transition(self, *, owner_id: str, transition_id: str, to_mode: str, source_id: str, source_digest: str) -> KnowledgeModeTransition:
        if not self._PROPOSAL_ID.fullmatch(transition_id) or not source_id or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_digest):
            raise ValueError("transition requires safe id and exact source identity/digest")
        if to_mode not in {self.mode, self.local_mode, self.hosted_mode}:
            raise ValueError("unknown canonical mode")
        # A local writer handoff needs a separately provisioned local canonical
        # adapter with verified backup/restore semantics. This connected-Git
        # adapter must not pretend that a state hash is such a backup.
        raise SharedKnowledgeError("canonical mode transition is unavailable without a provisioned target adapter and verified backup")

    def commit_mode_transition(self, transition: KnowledgeModeTransition, *, owner_id: str) -> KnowledgeModeTransition:
        raise SharedKnowledgeError("canonical mode transition is unavailable without a provisioned target adapter and verified backup")

    def _authorize(self, action: str, ref: ResourceRef, user_id: str) -> None:
        # Never cache authority. The adapter verifies live signature/grant state.
        self.action_authorizer.authorize(action, ref, subject_id=user_id)

    @staticmethod
    def _safe_path(path: str) -> str:
        candidate = Path(path)
        if not path or candidate.is_absolute() or ".." in candidate.parts or ".git" in candidate.parts:
            raise ValueError("proposal path must remain within its checkout")
        return path

    def _validate_checkout(self, checkout: str | Path, base: str) -> Path:
        supplied = Path(checkout)
        if supplied.is_symlink():
            raise SharedKnowledgeError("checkout must be an owned regular directory")
        checkout = supplied.resolve(strict=True)
        if not checkout.is_dir():
            raise SharedKnowledgeError("checkout must be an owned regular directory")
        if self._run("git", "rev-parse", "--is-inside-work-tree", cwd=checkout) != "true":
            raise SharedKnowledgeError("checkout is not a Git worktree")
        try:
            configured_remote = Path(self._run("git", "remote", "get-url", "origin", cwd=checkout)).resolve(strict=True)
        except OSError as exc:
            raise SharedKnowledgeError("checkout origin is not the configured canonical remote") from exc
        if configured_remote != self.remote:
            raise SharedKnowledgeError("checkout origin is not the configured canonical remote")
        if self._run("git", "rev-parse", "HEAD", cwd=checkout) != base:
            raise SharedKnowledgeError("checkout is not at the expected canonical revision")
        configured = subprocess.run(
            ("git", "config", "--local", "--name-only", "--get-regexp", r"^(filter\.|url\.|remote\.origin\.pushurl|core\.(hooksPath|fsmonitor))"),
            cwd=checkout, env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if configured.returncode not in {0, 1}:
            raise SharedKnowledgeError("could not inspect checkout Git configuration")
        unsafe = configured.stdout.strip()
        if unsafe or (checkout / ".gitattributes").exists():
            raise SharedKnowledgeError("checkout Git configuration is not service-controlled")
        return checkout

    def propose(self, *, user_id: str, checkout: str | Path, proposal_id: str, path: str, content: str | None) -> KnowledgeProposal:
        if not self._PROPOSAL_ID.fullmatch(proposal_id):
            raise ValueError("proposal id must be a bounded safe identifier")
        path = self._safe_path(path)
        with self._locked_state() as state:
            if proposal_id in state.setdefault("proposals", {}):
                raise SharedKnowledgeError("proposal id already exists")
            base = self._remote_ref("refs/heads/main")
            repository_ref = self._repository_ref(base)
            self._authorize("shared_knowledge.propose", repository_ref, user_id)
            checkout_path = self._validate_checkout(checkout, base)
            destination = checkout_path / path
            if any(parent.is_symlink() for parent in destination.parents if parent != checkout_path.parent):
                raise SharedKnowledgeError("proposal path may not traverse symlinks")
            if self._run("git", "status", "--porcelain", cwd=checkout_path):
                raise SharedKnowledgeError("checkout must be clean")
            branch_name = f"krail/proposals/{proposal_id}"
            self._run("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "checkout", "-b", branch_name, base, cwd=checkout_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.is_symlink():
                raise SharedKnowledgeError("proposal destination may not be a symlink")
            deleted = content is None
            if deleted:
                if not destination.is_file():
                    raise SharedKnowledgeError("only an existing regular file can be tombstoned")
                self._run("git", "-c", "core.hooksPath=/dev/null", "rm", "--", path, cwd=checkout_path)
            else:
                destination.write_text(content)
                self._run("git", "-c", "core.hooksPath=/dev/null", "add", "--", path, cwd=checkout_path)
            self._run("git", "-c", "core.hooksPath=/dev/null", "commit", "-m", f"krail proposal {proposal_id}", cwd=checkout_path)
            candidate = self._run("git", "rev-parse", "HEAD", cwd=checkout_path)
            proposal = KnowledgeProposal(proposal_id, user_id, "refs/heads/" + branch_name, base, candidate, path, self._digest(content if content is not None else f"tombstone:{path}:{base}"), deleted=deleted)
            proposal_ref = self._proposal_ref(proposal)
            self._authorize("shared_knowledge.propose", proposal_ref, user_id)
            # Never honor an untrusted origin.pushurl; the configured bare
            # remote was checked above and is supplied as the exact target.
            self._run("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "push", str(self.remote), f"HEAD:{proposal.branch}", cwd=checkout_path)
            self._authorize("shared_knowledge.propose", proposal_ref, user_id)
            state["proposals"][proposal_id] = asdict(proposal)
        # State persistence is an effect too; fail closed if authority changed
        # while releasing the durable state lock.
        self._authorize("shared_knowledge.propose", proposal_ref, user_id)
        return proposal

    def _recover(self, state: dict, proposal: KnowledgeProposal) -> KnowledgeProposal:
        current = self._remote_ref("refs/heads/main")
        if proposal.status == "review-pending" and current == proposal.candidate_commit:
            proposal = KnowledgeProposal(**{**asdict(proposal), "status": "promoted"})
            state["proposals"][proposal.proposal_id] = asdict(proposal)
        elif proposal.status in {"proposed", "review-pending"} and current != proposal.base_commit:
            proposal = KnowledgeProposal(**{**asdict(proposal), "status": "conflict"})
            state["proposals"][proposal.proposal_id] = asdict(proposal)
        return proposal

    def review_and_promote(self, proposal_id: str, *, reviewer_id: str) -> KnowledgeProposal:
        result: KnowledgeProposal
        with self._locked_state() as state:
            try:
                proposal = KnowledgeProposal(**state.setdefault("proposals", {})[proposal_id])
            except KeyError as exc:
                raise SharedKnowledgeError("unknown proposal") from exc
            if not reviewer_id:
                raise ValueError("reviewer identity is required")
            self._authorize("shared_knowledge.review", self._proposal_ref(proposal), reviewer_id)
            if proposal.status == "review-pending" and proposal.reviewer_id != reviewer_id:
                raise PermissionError("review-pending proposal belongs to a different reviewer")
            proposal = self._recover(state, proposal)
            if proposal.status not in {"proposed", "review-pending"}:
                result = proposal
            else:
                if proposal.status == "proposed":
                    receipt = self._digest(f"{reviewer_id}:{proposal_id}:{proposal.candidate_commit}")
                    proposal = KnowledgeProposal(**{**asdict(proposal), "status": "review-pending", "reviewer_receipt": receipt, "reviewer_id": reviewer_id})
                    state["proposals"][proposal_id] = asdict(proposal)
                    # Persist the authorized receipt before canonical Git can
                    # move. Recovery can therefore never invent a reviewer.
                    self._persist_locked(state)
                proposal_ref = self._proposal_ref(proposal)
                current = self._remote_ref("refs/heads/main")
                candidate = self._remote_ref(proposal.branch)
                if candidate != proposal.candidate_commit or current != proposal.base_commit:
                    result = KnowledgeProposal(**{**asdict(proposal), "status": "conflict"})
                    state["proposals"][proposal_id] = asdict(result)
                else:
                    self._run("git", "--git-dir", str(self.remote), "update-ref", "refs/heads/main", candidate, current)
                    result = KnowledgeProposal(**{**asdict(proposal), "status": "promoted"})
                    state["proposals"][proposal_id] = asdict(result)
                    for key, value in tuple(state["proposals"].items()):
                        other = KnowledgeProposal(**value)
                        if key != proposal_id and other.status == "proposed" and other.base_commit == current:
                            state["proposals"][key] = asdict(KnowledgeProposal(**{**value, "status": "conflict"}))
                    self._cache.clear()
        self._authorize("shared_knowledge.review", self._proposal_ref(result), reviewer_id)
        return result

    def authorized_context(self, user_id: str) -> AuthorizedKnowledgeContext:
        commit = self._remote_ref("refs/heads/main")
        repository_ref = self._repository_ref(commit)
        self._authorize("shared_knowledge.read", repository_ref, user_id)
        cache_key = (user_id, commit)
        context = self._cache.get(cache_key)
        if context is None:
            paths = self._run("git", "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", commit).splitlines()
            files = []
            for path in paths:
                content = self._show(f"{commit}:{path}")
                self._authorize("shared_knowledge.read", self._ref("file/" + path, commit, self._digest(content)), user_id)
                files.append((path, content))
            with self._locked_state() as state:
                lineage = tuple(sorted(item["candidate_commit"] for item in state.setdefault("proposals", {}).values() if item["status"] == "promoted" and item["candidate_commit"] == commit))
            context = AuthorizedKnowledgeContext(user_id, commit, tuple(files), lineage)
            self._cache[cache_key] = context
        # A repository grant does not imply an exact-file grant. Recheck every
        # cached byte and every lineage node at the return boundary.
        self._authorize("shared_knowledge.read", repository_ref, user_id)
        for path, content in context.files:
            self._authorize("shared_knowledge.read", self._ref("file/" + path, context.canonical_commit, self._digest(content)), user_id)
        with self._locked_state() as state:
            promoted = {
                item["candidate_commit"]: KnowledgeProposal(**item)
                for item in state.setdefault("proposals", {}).values()
                if item["status"] == "promoted" and item["candidate_commit"] in context.lineage
            }
        for candidate in context.lineage:
            self._authorize("shared_knowledge.read", self._proposal_ref(promoted[candidate]), user_id)
        return context

    def export(self, user_id: str) -> dict:
        context = self.authorized_context(user_id)
        self._authorize("shared_knowledge.export", self._repository_ref(context.canonical_commit), user_id)
        for path, content in context.files:
            self._authorize("shared_knowledge.export", self._ref("file/" + path, context.canonical_commit, self._digest(content)), user_id)
        return {"canonical_commit": context.canonical_commit, "files": dict(context.files), "lineage": context.lineage}

    def search(self, user_id: str, query: str) -> tuple[tuple[str, str], ...]:
        if not query:
            raise ValueError("search query is required")
        context = self.authorized_context(user_id)
        self._authorize("shared_knowledge.search", self._repository_ref(context.canonical_commit), user_id)
        results = tuple((path, content) for path, content in context.files if query.casefold() in content.casefold())
        for path, content in results:
            self._authorize("shared_knowledge.search", self._ref("file/" + path, context.canonical_commit, self._digest(content)), user_id)
        return results
