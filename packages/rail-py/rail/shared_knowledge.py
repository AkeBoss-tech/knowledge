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


@dataclass(frozen=True)
class AuthorizedKnowledgeContext:
    user_id: str
    canonical_commit: str
    files: tuple[tuple[str, str], ...]
    lineage: tuple[str, ...]


class SharedKnowledgeWorkspace:
    """A locked, restartable boundary around one configured bare Git remote."""

    mode = "connected-canonical-git-reviewed-changes"
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
            if state.get("mode", self.mode) != self.mode:
                raise SharedKnowledgeError("workspace authority mode cannot change implicitly")
            state.setdefault("mode", self.mode)
            state.setdefault("revision", 0)
            state.setdefault("proposals", {})

    @staticmethod
    def _run(*args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(args, cwd=cwd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

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
        checkout = Path(checkout).resolve(strict=True)
        if checkout.is_symlink() or not checkout.is_dir():
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
        return checkout

    def propose(self, *, user_id: str, checkout: str | Path, proposal_id: str, path: str, content: str) -> KnowledgeProposal:
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
            self._run("git", "checkout", "-b", branch_name, base, cwd=checkout_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.is_symlink():
                raise SharedKnowledgeError("proposal destination may not be a symlink")
            destination.write_text(content)
            self._run("git", "-c", "core.hooksPath=/dev/null", "add", "--", path, cwd=checkout_path)
            self._run("git", "-c", "core.hooksPath=/dev/null", "commit", "-m", f"krail proposal {proposal_id}", cwd=checkout_path)
            candidate = self._run("git", "rev-parse", "HEAD", cwd=checkout_path)
            proposal = KnowledgeProposal(proposal_id, user_id, "refs/heads/" + branch_name, base, candidate, path, self._digest(content))
            proposal_ref = self._proposal_ref(proposal)
            self._authorize("shared_knowledge.propose", proposal_ref, user_id)
            self._run("git", "push", "origin", f"HEAD:{proposal.branch}", cwd=checkout_path)
            self._authorize("shared_knowledge.propose", proposal_ref, user_id)
            state["proposals"][proposal_id] = asdict(proposal)
            return proposal

    def _recover(self, state: dict, proposal: KnowledgeProposal) -> KnowledgeProposal:
        current = self._remote_ref("refs/heads/main")
        if proposal.status == "proposed" and current == proposal.candidate_commit:
            proposal = KnowledgeProposal(**{**asdict(proposal), "status": "promoted"})
            state["proposals"][proposal.proposal_id] = asdict(proposal)
        elif proposal.status == "proposed" and current != proposal.base_commit:
            proposal = KnowledgeProposal(**{**asdict(proposal), "status": "conflict"})
            state["proposals"][proposal.proposal_id] = asdict(proposal)
        return proposal

    def review_and_promote(self, proposal_id: str, *, reviewer_id: str) -> KnowledgeProposal:
        with self._locked_state() as state:
            try:
                proposal = KnowledgeProposal(**state.setdefault("proposals", {})[proposal_id])
            except KeyError as exc:
                raise SharedKnowledgeError("unknown proposal") from exc
            proposal = self._recover(state, proposal)
            if proposal.status != "proposed":
                return proposal
            if not reviewer_id:
                raise ValueError("reviewer identity is required")
            proposal_ref = self._proposal_ref(proposal)
            self._authorize("shared_knowledge.review", proposal_ref, reviewer_id)
            current = self._remote_ref("refs/heads/main")
            candidate = self._remote_ref(proposal.branch)
            if candidate != proposal.candidate_commit or current != proposal.base_commit:
                conflict = KnowledgeProposal(**{**asdict(proposal), "status": "conflict"})
                state["proposals"][proposal_id] = asdict(conflict)
                return conflict
            self._run("git", "--git-dir", str(self.remote), "update-ref", "refs/heads/main", candidate, current)
            receipt = self._digest(f"{reviewer_id}:{proposal_id}:{candidate}")
            promoted = KnowledgeProposal(**{**asdict(proposal), "status": "promoted", "reviewer_receipt": receipt})
            state["proposals"][proposal_id] = asdict(promoted)
            for key, value in tuple(state["proposals"].items()):
                other = KnowledgeProposal(**value)
                if key != proposal_id and other.status == "proposed" and other.base_commit == current:
                    state["proposals"][key] = asdict(KnowledgeProposal(**{**value, "status": "conflict"}))
            self._cache.clear()
            self._authorize("shared_knowledge.review", self._proposal_ref(promoted), reviewer_id)
            return promoted

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
        self._authorize("shared_knowledge.read", repository_ref, user_id)
        return context

    def export(self, user_id: str) -> dict:
        context = self.authorized_context(user_id)
        self._authorize("shared_knowledge.export", self._repository_ref(context.canonical_commit), user_id)
        return {"canonical_commit": context.canonical_commit, "files": dict(context.files), "lineage": context.lineage}

    def search(self, user_id: str, query: str) -> tuple[tuple[str, str], ...]:
        if not query:
            raise ValueError("search query is required")
        context = self.authorized_context(user_id)
        self._authorize("shared_knowledge.search", self._repository_ref(context.canonical_commit), user_id)
        return tuple((path, content) for path, content in context.files if query.casefold() in content.casefold())
