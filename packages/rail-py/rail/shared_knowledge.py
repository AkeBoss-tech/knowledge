"""Local connected-Git shared knowledge boundary for the #29 first journey.

The Git remote is canonical. This module stores only proposal/review metadata
and disposable authorized context caches; it never turns a clone or cache into
a writer.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path


class SharedKnowledgeError(RuntimeError):
    pass


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


@dataclass(frozen=True)
class AuthorizedKnowledgeContext:
    user_id: str
    canonical_commit: str
    files: tuple[tuple[str, str], ...]
    lineage: tuple[str, ...]


class SharedKnowledgeWorkspace:
    """A restartable local contract around one connected canonical Git remote."""

    mode = "connected-canonical-git-reviewed-changes"

    def __init__(self, remote: str | Path, state_path: str | Path) -> None:
        self.remote, self.state_path = Path(remote), Path(state_path)
        self._state = self._load()
        if self._state.get("mode", self.mode) != self.mode:
            raise SharedKnowledgeError("workspace authority mode cannot change implicitly")
        self._state.setdefault("mode", self.mode)
        self._state.setdefault("proposals", {})
        self._state.setdefault("grants", {})
        self._cache: dict[tuple[str, str], AuthorizedKnowledgeContext] = {}
        self._save()

    @staticmethod
    def _run(*args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(args, cwd=cwd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise SharedKnowledgeError(result.stderr.strip() or "git command failed")
        return result.stdout.strip()

    def _remote_ref(self, ref: str) -> str:
        return self._run("git", "--git-dir", str(self.remote), "rev-parse", ref)

    def _show(self, spec: str) -> str:
        result = subprocess.run(("git", "--git-dir", str(self.remote), "show", spec), check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise SharedKnowledgeError(result.stderr.strip() or "git show failed")
        return result.stdout

    def _load(self) -> dict:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, sort_keys=True, indent=2) + "\n")
        temporary.replace(self.state_path)

    @staticmethod
    def _safe_path(path: str) -> str:
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts or not path:
            raise ValueError("proposal path must remain within its checkout")
        return path

    def grant_source(self, user_id: str) -> None:
        self._state["grants"][user_id] = True
        self._save()

    def revoke_source(self, user_id: str) -> None:
        self._state["grants"][user_id] = False
        self._cache = {key: value for key, value in self._cache.items() if key[0] != user_id}
        self._save()

    def propose(self, *, user_id: str, checkout: str | Path, proposal_id: str, path: str, content: str) -> KnowledgeProposal:
        path = self._safe_path(path)
        if proposal_id in self._state["proposals"]:
            raise SharedKnowledgeError("proposal id already exists")
        checkout = Path(checkout)
        base = self._run("git", "rev-parse", "HEAD", cwd=checkout)
        if base != self._remote_ref("refs/heads/main"):
            raise SharedKnowledgeError("proposal checkout is not at canonical main")
        branch = f"refs/heads/krail/proposals/{proposal_id}"
        self._run("git", "checkout", "-B", branch.removeprefix("refs/heads/"), base, cwd=checkout)
        destination = checkout / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        self._run("git", "add", "--", path, cwd=checkout)
        self._run("git", "commit", "-m", f"krail proposal {proposal_id}", cwd=checkout)
        candidate = self._run("git", "rev-parse", "HEAD", cwd=checkout)
        self._run("git", "push", "origin", f"HEAD:{branch}", cwd=checkout)
        proposal = KnowledgeProposal(proposal_id, user_id, branch, base, candidate, path, "sha256:" + sha256(content.encode()).hexdigest())
        self._state["proposals"][proposal_id] = asdict(proposal)
        self._save()
        return proposal

    def review_and_promote(self, proposal_id: str, *, reviewer_id: str) -> KnowledgeProposal:
        del reviewer_id  # Review identity is caller-owned; this local slice records no grants.
        proposal = KnowledgeProposal(**self._state["proposals"][proposal_id])
        if proposal.status == "conflict":
            return proposal
        if proposal.status != "proposed":
            raise SharedKnowledgeError("only proposed changes can be reviewed")
        current = self._remote_ref("refs/heads/main")
        candidate = self._remote_ref(proposal.branch)
        if candidate != proposal.candidate_commit or current != proposal.base_commit:
            conflict = KnowledgeProposal(**{**asdict(proposal), "status": "conflict"})
            self._state["proposals"][proposal_id] = asdict(conflict)
            self._save()
            return conflict
        self._run("git", "--git-dir", str(self.remote), "update-ref", "refs/heads/main", candidate, current)
        promoted = KnowledgeProposal(**{**asdict(proposal), "status": "promoted"})
        self._state["proposals"][proposal_id] = asdict(promoted)
        for key, value in tuple(self._state["proposals"].items()):
            other = KnowledgeProposal(**value)
            if key != proposal_id and other.status == "proposed" and other.base_commit == current:
                self._state["proposals"][key] = asdict(KnowledgeProposal(**{**value, "status": "conflict"}))
        self._cache.clear()
        self._save()
        return promoted

    def authorized_context(self, user_id: str) -> AuthorizedKnowledgeContext:
        if not self._state["grants"].get(user_id, False):
            raise PermissionError("source grant is revoked or absent")
        commit = self._remote_ref("refs/heads/main")
        cache_key = (user_id, commit)
        if cache_key in self._cache:
            return self._cache[cache_key]
        paths = self._run("git", "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", commit).splitlines()
        files = tuple((path, self._show(f"{commit}:{path}")) for path in paths)
        lineage = tuple(sorted(item["candidate_commit"] for item in self._state["proposals"].values() if item["status"] == "promoted" and item["candidate_commit"] == commit))
        context = AuthorizedKnowledgeContext(user_id, commit, files, lineage)
        self._cache[cache_key] = context
        return context

    def export(self, user_id: str) -> dict:
        context = self.authorized_context(user_id)
        return {"canonical_commit": context.canonical_commit, "files": dict(context.files), "lineage": context.lineage}
