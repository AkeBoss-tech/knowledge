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


@dataclass(frozen=True)
class LocalKnowledgeCommit:
    write_id: str
    user_id: str
    base_commit: str
    candidate_commit: str
    path: str
    content_digest: str
    generation: int
    status: str = "pending"
    deleted: bool = False


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
            state.setdefault("writer_generation", 0)
            state.setdefault("revision", 0)
            state.setdefault("proposals", {})
            state.setdefault("backups", {})
            state.setdefault("transitions", {})
            state.setdefault("local_commits", {})
            state.setdefault(
                "canonical_base_commit", self._remote_ref("refs/heads/main")
            )

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
        if to_mode not in {self.mode, self.local_mode}:
            raise ValueError("unknown canonical mode")
        backup = self.create_backup(owner_id=owner_id, backup_id="transition-" + transition_id)
        with self._locked_state() as state:
            if transition_id in state.setdefault("transitions", {}):
                raise SharedKnowledgeError("transition id already exists")
            current = self._remote_ref("refs/heads/main")
            if current != backup.canonical_commit or state["mode"] not in {self.mode, self.local_mode}:
                raise SharedKnowledgeError("transition source changed while backup was created")
            if to_mode == state["mode"]:
                raise SharedKnowledgeError("workspace is already in requested authority mode")
            if source_id != self.remote.as_uri() or source_digest != self._digest(current):
                raise SharedKnowledgeError("transition source identity or digest does not match canonical head")
            self._authorize("shared_knowledge.mode_transition", self._repository_ref(current), owner_id)
            next_revision = state["revision"] + 1
            transition = KnowledgeModeTransition(transition_id, state["mode"], to_mode, current, next_revision, source_id, source_digest, backup.bundle_digest)
            state["transitions"][transition_id] = {"owner_id": owner_id, "generation": state["writer_generation"], "backup": asdict(backup), "transition": asdict(transition)}
        self._authorize("shared_knowledge.mode_transition", self._repository_ref(current), owner_id)
        return transition

    def commit_mode_transition(self, transition: KnowledgeModeTransition, *, owner_id: str) -> KnowledgeModeTransition:
        """Atomically activate a previously registered, verified transition.

        Validation and the mode/generation switch happen under a single
        acquisition of the state lock so no other mutation can observe (or
        race) a state where the preview was checked but the switch had not
        yet landed. A transition may only ever be activated once, and only
        one active_writer/mode pair can be durable at a time, so a stale
        connected-git caller (any instance, old or new) is fenced the moment
        the switch commits: `_require_writer` re-reads mode/active_writer from
        disk on every call.
        """
        if transition.to_mode == self.hosted_mode:
            raise SharedKnowledgeError("canonical mode transition is unavailable without a provisioned target adapter and verified backup")
        if transition.to_mode not in {self.local_mode, self.mode}:
            raise SharedKnowledgeError("unknown canonical mode activation target")
        with self._locked_state() as state:
            existing = state.setdefault("transitions", {}).get(transition.transition_id)
            if existing and existing.get("activated"):
                raise SharedKnowledgeError("transition was already activated")
            stored = self._validate_transition_locked(state, transition, owner_id=owner_id)
            if (
                any(item.get("status") == "pending" for item in state.setdefault("local_commits", {}).values())
                or any(item.get("status") == "review-pending" for item in state.setdefault("proposals", {}).values())
            ):
                raise SharedKnowledgeError("cannot activate mode with unresolved canonical effects")
            state["mode"] = transition.to_mode
            state["active_writer"] = "local-git" if transition.to_mode == self.local_mode else "connected-git"
            state["writer_generation"] = int(state["writer_generation"]) + 1
            stored["activated"] = True
            stored["activated_generation"] = state["writer_generation"]
            state["transitions"][transition.transition_id] = stored
            self._cache.clear()
        self._authorize("shared_knowledge.mode_transition", self._repository_ref(transition.canonical_commit), owner_id)
        return transition

    def _validate_transition_locked(self, state: dict, transition: KnowledgeModeTransition, *, owner_id: str) -> dict:
        """Validate a durable preview and its immutable bundle. Caller must already hold the state lock."""
        stored = state.setdefault("transitions", {}).get(transition.transition_id)
        if not stored or stored.get("owner_id") != owner_id or stored.get("transition") != asdict(transition):
            raise SharedKnowledgeError("transition preview is forged, missing, or owned by another subject")
        if state["mode"] != transition.from_mode or state["writer_generation"] != stored["generation"]:
            raise SharedKnowledgeError("transition writer generation is stale")
        current = self._remote_ref("refs/heads/main")
        if current != transition.canonical_commit or transition.source_id != self.remote.as_uri() or transition.source_digest != self._digest(current):
            raise SharedKnowledgeError("transition source or canonical head is stale")
        backup = KnowledgeBackupReceipt(**stored["backup"])
        bundle = Path(backup.bundle_path)
        if bundle.is_symlink() or not bundle.is_file() or "sha256:" + sha256(bundle.read_bytes()).hexdigest() != backup.bundle_digest:
            raise SharedKnowledgeError("transition backup bundle is missing or corrupt")
        self._run("git", "--git-dir", str(self.remote), "bundle", "verify", str(bundle))
        heads = self._run("git", "bundle", "list-heads", str(bundle)).splitlines()
        if not any(line.split()[0] == current for line in heads):
            raise SharedKnowledgeError("transition backup does not contain canonical head")
        self._authorize("shared_knowledge.mode_transition", self._repository_ref(current), owner_id)
        return stored

    def _validated_transition(self, transition: KnowledgeModeTransition, *, owner_id: str) -> dict:
        """Validate a durable preview and its immutable bundle before activation."""
        with self._locked_state() as state:
            return self._validate_transition_locked(state, transition, owner_id=owner_id)

    def _authorize(self, action: str, ref: ResourceRef, user_id: str) -> None:
        # Never cache authority. The adapter verifies live signature/grant state.
        self.action_authorizer.authorize(action, ref, subject_id=user_id)

    def _require_writer(self, state: dict, expected_mode: str, expected_writer: str, expected_generation: int | None = None) -> int:
        """Fence canonical mutation against a durable mode/active-writer/generation triple.

        Reads are always taken fresh from the on-disk state under the current
        lock acquisition, so a stale in-memory `SharedKnowledgeWorkspace`
        instance -- including one held from before a mode switch -- observes
        the switch on its very next call and is fenced immediately. Only one
        (mode, active_writer) pair can be durable at a time, so this also
        guarantees at most one canonical writer kind is ever active.
        """
        generation = int(state.get("writer_generation", 0))
        if state.get("mode") != expected_mode or state.get("active_writer") != expected_writer:
            raise SharedKnowledgeError(f"{expected_writer} canonical writer is disabled")
        if expected_generation is not None and generation != expected_generation:
            raise SharedKnowledgeError("canonical writer generation is stale")
        return generation

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
            self._require_writer(state, self.mode, "connected-git")
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

    def _local_write_ref(self, record: LocalKnowledgeCommit) -> ResourceRef:
        return self._ref("local/" + record.write_id, record.candidate_commit, record.content_digest)

    def _recover_local(self, state: dict, record: LocalKnowledgeCommit) -> LocalKnowledgeCommit:
        current = self._remote_ref("refs/heads/main")
        if record.status == "pending" and current == record.candidate_commit:
            record = LocalKnowledgeCommit(**{**asdict(record), "status": "landed"})
            state["local_commits"][record.write_id] = asdict(record)
        elif record.status == "pending" and current != record.base_commit:
            record = LocalKnowledgeCommit(**{**asdict(record), "status": "conflict"})
            state["local_commits"][record.write_id] = asdict(record)
        return record

    def write_local(self, *, user_id: str, checkout: str | Path, write_id: str, path: str, content: str | None, expected_head: str, expected_generation: int) -> LocalKnowledgeCommit:
        """Direct single-writer canonical mutation for local-canonical-git mode.

        Unlike ``propose``/``review_and_promote`` (which stage a branch for a
        separate reviewer to promote), local-canonical-git mode has exactly
        one durable active writer with no review step: the caller commits
        straight onto canonical `main`. Every check below is load-bearing:

          * durable active writer/generation: `_require_writer` re-reads
            mode/active_writer/writer_generation from disk under the lock,
            so a stale writer (wrong mode, or a superseded generation after a
            mode transition) is rejected before anything else runs;
          * exact expected main head: the caller must name the exact commit
            it believes canonical `main` sits at; a mismatch fails closed
            instead of silently rebasing onto an unexpected base;
          * live caller authorization: re-checked against the *current*
            canonical head, not any cached grant. This applies identically
            on a resumed (post-crash retry) call: authorization against the
            current repository and the exact prior record is proven before
            recovery is allowed to observe or mutate that record's status,
            and before any resumed effect (staging push or main CAS) can
            run -- a revoked caller is denied before Git changes, never
            after;
          * owned clean checkout and safe path: reuses `_validate_checkout`
            (real worktree, correct origin, HEAD at `expected_head`, no
            unsafe local Git config) and `_safe_path`;
          * candidate Git CAS: the local commit is first pushed to a
            disposable per-write staging ref (an ordinary object transfer,
            not a canonical mutation), and only then is `refs/heads/main`
            moved with a single `git update-ref refs/heads/main <candidate>
            <expected_head>` run directly against the bare remote. Unlike a
            plain (non-force) `git push`, which Git will happily fast-forward
            as long as the remote tip is *any* ancestor of the candidate,
            `update-ref` with an explicit old value only succeeds if the
            remote tip is *exactly* that value at the instant of the swap --
            a real compare-and-swap, not merely "still on the same line of
            history";
          * provenance and crash safety: a "pending" record naming the exact
            candidate is persisted durably *before* the CAS is attempted (the
            same pattern `review_and_promote` uses for its reviewer receipt),
            so a crash between commit and CAS can never lose the fact that
            this local commit exists. A later call with the same `write_id`
            recovers the exact outcome (landed / conflicted / still pending
            and safe to resume) by re-checking canonical Git, and never
            repeats the local commit or the CAS.
        """
        if not self._PROPOSAL_ID.fullmatch(write_id):
            raise ValueError("write id must be a bounded safe identifier")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_head):
            raise ValueError("expected head must be an exact commit id")
        path = self._safe_path(path)
        deleted = content is None
        with self._locked_state() as state:
            self._require_writer(state, self.local_mode, "local-git", expected_generation)
            local_commits = state.setdefault("local_commits", {})
            existing = local_commits.get(write_id)
            if existing is not None:
                prior = LocalKnowledgeCommit(**existing)
                current = self._remote_ref("refs/heads/main")
                # Live authorization against the current repository and the
                # exact prior record must be proven *before* anything else:
                # before `_recover_local` is allowed to observe or mutate
                # this record's status, and before any resumed effect (a
                # staging push or a main CAS) can run. Skipping this would
                # let a revoked caller cause real Git mutation and only be
                # told "denied" after the fact.
                self._authorize("shared_knowledge.local_write", self._repository_ref(current), user_id)
                self._authorize("shared_knowledge.local_write", self._local_write_ref(prior), user_id)
                # Exact idempotent identity: a retry must reassert the same
                # expected head and generation it originally used, not just
                # matching content. This closes off a stolen/guessed
                # write_id from recovering another subject's receipt by
                # supplying plausible content without having actually
                # observed the original preconditions.
                incoming_digest = self._digest(content if content is not None else f"tombstone:{path}:{prior.base_commit}")
                if (
                    prior.user_id != user_id
                    or prior.path != path
                    or prior.deleted != deleted
                    or prior.content_digest != incoming_digest
                    or prior.base_commit != expected_head
                    or prior.generation != expected_generation
                ):
                    raise SharedKnowledgeError("write id already exists")
                record = self._recover_local(state, prior)
                record_ref = self._local_write_ref(record)
                if record.status != "pending":
                    # Crash-safe idempotent recovery: the git write already
                    # happened (or definitively failed) before some earlier
                    # attempt crashed; never repeat it.
                    self._authorize("shared_knowledge.local_write", record_ref, user_id)
                    if record.status == "conflict":
                        raise SharedKnowledgeError("canonical head is stale")
                    return record
                # Resume an interrupted attempt: the local commit already
                # exists in this same checkout from before the crash.
                checkout_path = self._validate_checkout(checkout, record.candidate_commit)
            else:
                current = self._remote_ref("refs/heads/main")
                if current != expected_head:
                    raise SharedKnowledgeError("canonical head is stale")
                repository_ref = self._repository_ref(current)
                self._authorize("shared_knowledge.local_write", repository_ref, user_id)
                checkout_path = self._validate_checkout(checkout, current)
                if self._run("git", "status", "--porcelain", cwd=checkout_path):
                    raise SharedKnowledgeError("checkout must be clean")
                destination = checkout_path / path
                if any(parent.is_symlink() for parent in destination.parents if parent != checkout_path.parent):
                    raise SharedKnowledgeError("write path may not traverse symlinks")
                if deleted:
                    if not destination.is_file():
                        raise SharedKnowledgeError("only an existing regular file can be tombstoned")
                    self._run("git", "-c", "core.hooksPath=/dev/null", "rm", "--", path, cwd=checkout_path)
                else:
                    if destination.exists() and destination.is_symlink():
                        raise SharedKnowledgeError("write destination may not be a symlink")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(content)
                    self._run("git", "-c", "core.hooksPath=/dev/null", "add", "--", path, cwd=checkout_path)
                self._run("git", "-c", "core.hooksPath=/dev/null", "commit", "-m", f"krail local write {write_id}", cwd=checkout_path)
                candidate = self._run("git", "rev-parse", "HEAD", cwd=checkout_path)
                digest = self._digest(content if content is not None else f"tombstone:{path}:{current}")
                record = LocalKnowledgeCommit(write_id, user_id, current, candidate, path, digest, expected_generation, status="pending", deleted=deleted)
                record_ref = self._local_write_ref(record)
                self._authorize("shared_knowledge.local_write", record_ref, user_id)
                local_commits[write_id] = asdict(record)
                # Persist the pending intent before canonical Git can move.
                # A crash before or during the CAS below can therefore never
                # lose this write's provenance, and recovery never needs to
                # (and never will) re-run the local commit.
                self._persist_locked(state)
            staging_ref = f"refs/krail/local-staging/{write_id}"
            self._run("git", "-c", "core.hooksPath=/dev/null", "push", str(self.remote), f"HEAD:{staging_ref}", cwd=checkout_path)
            cas = subprocess.run(
                ("git", "--git-dir", str(self.remote), "update-ref", "refs/heads/main", record.candidate_commit, record.base_commit),
                check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self._run("git", "--git-dir", str(self.remote), "update-ref", "-d", staging_ref)
            status = "landed" if cas.returncode == 0 else "conflict"
            record = LocalKnowledgeCommit(**{**asdict(record), "status": status})
            record_ref = self._local_write_ref(record)
            self._authorize("shared_knowledge.local_write", record_ref, user_id)
            local_commits[write_id] = asdict(record)
            self._cache.clear()
        # State persistence is an effect too; fail closed if authority changed
        # while releasing the durable state lock.
        self._authorize("shared_knowledge.local_write", record_ref, user_id)
        if record.status == "conflict":
            raise SharedKnowledgeError("canonical head is stale")
        return record

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
            # Gate on durable writer identity before anything else, including
            # the proposal lookup: a stale writer (e.g. after a connected ->
            # local mode switch) must fail as "writer disabled", not leak an
            # "unknown proposal" message for a proposal id it never had.
            self._require_writer(state, self.mode, "connected-git")
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
                lineage = tuple(sorted({
                    *(item["candidate_commit"] for item in state.setdefault("proposals", {}).values() if item["status"] == "promoted" and item["candidate_commit"] == commit),
                    *(item["candidate_commit"] for item in state.setdefault("local_commits", {}).values() if item["status"] == "landed" and item["candidate_commit"] == commit),
                }))
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
            landed_local = {
                item["candidate_commit"]: LocalKnowledgeCommit(**item)
                for item in state.setdefault("local_commits", {}).values()
                if item["status"] == "landed" and item["candidate_commit"] in context.lineage
            }
        for candidate in context.lineage:
            matches = []
            if candidate in promoted:
                matches.append(self._proposal_ref(promoted[candidate]))
            if candidate in landed_local:
                matches.append(self._local_write_ref(landed_local[candidate]))
            if len(matches) != 1:
                raise SharedKnowledgeError(
                    "canonical lineage metadata is missing or ambiguous"
                )
            self._authorize("shared_knowledge.read", matches[0], user_id)
        return context

    def export(self, user_id: str) -> dict:
        context = self.authorized_context(user_id)
        self._authorize("shared_knowledge.export", self._repository_ref(context.canonical_commit), user_id)
        for path, content in context.files:
            self._authorize("shared_knowledge.export", self._ref("file/" + path, context.canonical_commit, self._digest(content)), user_id)
        # Read authorization used to be sufficient for the lineage returned
        # below. Resolve every cached lineage commit back to exactly one
        # durable writer record and authorize that exact identity for export.
        # Missing or ambiguous metadata is not safe to omit or guess.
        with self._locked_state() as state:
            canonical_base_commit = state.get("canonical_base_commit")
            if not isinstance(canonical_base_commit, str):
                raise SharedKnowledgeError("canonical lineage metadata is missing")
            proposals = tuple(
                KnowledgeProposal(**item)
                for item in state.setdefault("proposals", {}).values()
                if item["status"] == "promoted"
            )
            local_commits = tuple(
                LocalKnowledgeCommit(**item)
                for item in state.setdefault("local_commits", {}).values()
                if item["status"] == "landed"
            )
        if context.canonical_commit != canonical_base_commit and not context.lineage:
            raise SharedKnowledgeError("canonical lineage metadata is missing")
        lineage_refs: list[ResourceRef] = []
        for candidate in context.lineage:
            matches = [
                self._proposal_ref(item)
                for item in proposals
                if item.candidate_commit == candidate
            ] + [
                self._local_write_ref(item)
                for item in local_commits
                if item.candidate_commit == candidate
            ]
            if len(matches) != 1:
                raise SharedKnowledgeError(
                    "canonical lineage metadata is missing or ambiguous"
                )
            lineage_refs.append(matches[0])
        for ref in lineage_refs:
            self._authorize("shared_knowledge.export", ref, user_id)
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
