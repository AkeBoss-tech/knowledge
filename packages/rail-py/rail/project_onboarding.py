"""Safe, deterministic discovery for bringing an existing repository into KRAIL."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

import yaml

CONTRACTS = {
    "discovery": "krail.project-discovery/v1",
    "profile": "krail.project-profile/v1",
    "proposal": "krail.project-pack-proposal/v1",
    "recommendations": "krail.automation-recommendations/v1",
    "work_order": "krail.project-onboarding-work-order/v1",
}
RUNNERS = {"codex_cli", "claude_code"}
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "vendor", "dist", "build",
    "target", "coverage", ".coverage", ".cache", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".next", ".nuxt", "generated", "artifacts",
    ".krail", ".ontology", "research_plan", "skills", "agents", "topics", "sources",
}
KRAIL_MANIFESTS = {"rail.yaml", "krail.yaml"}
SECRET_NAMES = re.compile(r"(^|[._-])(secret|credential|private|token|password|passwd|api[_-]?key)([._-]|$)", re.I)
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks"}
MAX_FILE_BYTES = 2_000_000

STARTER_SKILLS = {
    "project-orientation": "Orient to the project using discovery evidence; do not infer unsupported facts.",
    "project-automation-recommender": "Propose bounded automation only when repository configuration cites the command.",
    "change-verification": "Select repository-backed test, lint, typecheck, and build commands for a change.",
    "project-memory-refresh": "Refresh stale project knowledge as proposals requiring human review.",
}
WORKFLOWS = {
    "onboard_project": {
        "description": "Refresh deterministic discovery and emit a bounded Codex onboarding work order.",
        "steps": [
            {"id": "refresh_discovery", "kind": "command", "run": "krail onboard . --apply --runner codex_cli --dry-run"},
            {"id": "review_proposals", "kind": "approval", "description": "Review source-backed profile and automation proposals before promotion."},
        ],
    },
    "recommend_project_automations": {
        "description": "Refresh evidence and request reviewed project-specific automation recommendations.",
        "steps": [
            {"id": "refresh_discovery", "kind": "command", "run": "krail onboard . --apply --runner codex_cli --dry-run"},
            {"id": "review_recommendations", "kind": "approval", "description": "Approve, reject, or request changes to recommended automation."},
        ],
    },
    "project_memory_refresh": {
        "description": "Create a new immutable discovery snapshot and request reviewed memory updates.",
        "steps": [
            {"id": "refresh_discovery", "kind": "command", "run": "krail onboard . --apply --runner codex_cli --dry-run"},
            {"id": "review_memory", "kind": "approval", "description": "Review proposed memory changes before promotion."},
            {"id": "rebuild_after_review", "kind": "command", "run": "krail --local graph build && krail --local vector build"},
        ],
    },
    "verify_change": {
        "description": "Refresh changed-file evidence and create a bounded verification work order.",
        "steps": [
            {"id": "refresh_discovery", "kind": "command", "run": "krail onboard . --apply --runner codex_cli --dry-run"},
            {"id": "review_commands", "kind": "approval", "description": "Confirm evidence-backed verification commands before execution."},
        ],
    },
}


class OnboardingError(ValueError):
    pass


def _run(root: Path, *args: str) -> str:
    result = subprocess.run(args, cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_ignored(root: Path, path: Path) -> bool:
    if not (root / ".git").exists():
        return False
    relative = path.relative_to(root).as_posix()
    return subprocess.run(["git", "check-ignore", "-q", "--", relative], cwd=root, check=False).returncode == 0


def _safe_files(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not (current_path / d).is_symlink())
        for name in sorted(files):
            path = current_path / name
            if current_path == root and name in KRAIL_MANIFESTS:
                continue
            if path.is_symlink() or any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
                continue
            if SECRET_NAMES.search(name) or path.suffix.lower() in SECRET_SUFFIXES or name == ".env" or name.startswith(".env."):
                continue
            if _git_ignored(root, path):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES or not path.resolve().is_relative_to(root):
                    continue
            except OSError:
                continue
            yield path


def _evidence(root: Path, path: Path, revision: str) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(), "revision": revision or "working-tree",
        "span": {"start_line": 1, "end_line": raw.count(b"\n") + 1},
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }


def _commands(root: Path, files: list[Path], revision: str) -> list[dict[str, Any]]:
    by_name = {p.relative_to(root).as_posix(): p for p in files}
    found: list[dict[str, Any]] = []
    def add(command: str, kind: str, rel: str) -> None:
        found.append({"command": command, "kind": kind, "evidence": [_evidence(root, by_name[rel], revision)]})
    if "pyproject.toml" in by_name:
        text = by_name["pyproject.toml"].read_text("utf-8", errors="replace")
        if "pytest" in text: add("pytest", "test", "pyproject.toml")
        if "ruff" in text: add("ruff check .", "lint", "pyproject.toml")
        if "mypy" in text: add("mypy .", "typecheck", "pyproject.toml")
        add("python -m build", "build", "pyproject.toml")
    if "package.json" in by_name:
        try: scripts = json.loads(by_name["package.json"].read_text()).get("scripts", {})
        except (ValueError, OSError): scripts = {}
        for kind in ("test", "lint", "typecheck", "build"):
            if kind in scripts: add(f"npm run {kind}", kind, "package.json")
    for rel, commands in (
        ("go.mod", [("go test ./...", "test"), ("go build ./...", "build")]),
        ("Cargo.toml", [("cargo test", "test"), ("cargo build", "build")]),
        ("Makefile", [("make test", "test")]),
    ):
        if rel in by_name:
            for command, kind in commands: add(command, kind, rel)
    return found


def discover_project(path: str | Path, *, allowed_root: str | Path | None = None) -> dict[str, Any]:
    requested = Path(path).expanduser().absolute()
    if requested.is_symlink() or not requested.is_dir():
        raise OnboardingError("project path must be an existing, non-symlink directory")
    root = requested.resolve()
    boundary = Path(allowed_root).expanduser().resolve() if allowed_root else root
    if not root.is_relative_to(boundary):
        raise OnboardingError("project path escapes the authorized root")
    files = list(_safe_files(root))
    revision = _run(root, "git", "rev-parse", "HEAD")
    dirty = bool(_run(root, "git", "status", "--porcelain=v1", "--untracked-files=all")) if (root / ".git").exists() else False
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    digest.update(f"revision={revision};dirty={dirty}".encode())
    suffixes = {p.suffix.lower() for p in files}
    languages = [lang for lang, marker in (("python", ".py"), ("javascript/typescript", ".js"), ("javascript/typescript", ".ts"), ("go", ".go"), ("rust", ".rs")) if marker in suffixes]
    categories = {
        "manifests": [p for p in files if p.name in {"pyproject.toml", "setup.py", "package.json", "go.mod", "Cargo.toml", "Makefile"}],
        "ci": [p for p in files if ".github/workflows" in p.as_posix() or p.name in {".gitlab-ci.yml", "Jenkinsfile"}],
        "ownership": [p for p in files if p.name in {"CODEOWNERS", "OWNERS"}],
        "deployment": [p for p in files if p.name in {"Dockerfile", "docker-compose.yml", "Procfile", "fly.toml"} or p.suffix in {".tf"}],
        "migrations": [p for p in files if "migration" in p.relative_to(root).as_posix().lower()],
        "documentation": [p for p in files if p.suffix.lower() in {".md", ".rst"}],
        "entry_points": [p for p in files if p.name in {"main.py", "app.py", "main.go", "main.rs", "index.js", "index.ts"}],
    }
    return {
        "contract": CONTRACTS["discovery"], "root": str(root), "mode": "refresh" if any((root / n).exists() for n in ("krail.yaml", "rail.yaml")) else "onboard",
        "repository": {"kind": "git" if (root / ".git").exists() else "directory", "revision": revision or None, "dirty": dirty},
        "fingerprint": "sha256:" + digest.hexdigest(), "languages": sorted(set(languages)),
        "inventory": {key: [_evidence(root, p, revision) for p in value] for key, value in categories.items()},
        "commands": _commands(root, files, revision), "file_count": len(files),
    }


def validate_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("contract") not in {CONTRACTS["profile"], CONTRACTS["proposal"], CONTRACTS["recommendations"]}:
        raise OnboardingError("unsupported or missing proposal contract")
    if payload.get("review", {}).get("status") not in {"proposed", "accepted", "rejected"}:
        raise OnboardingError("proposal review.status must be proposed, accepted, or rejected")
    for claim in payload.get("claims", []):
        if not claim.get("evidence"):
            raise OnboardingError("every proposed claim requires evidence")
    return payload


def promote_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    validate_proposal(payload)
    if payload["review"]["status"] != "accepted" or not payload["review"].get("reviewed_by"):
        raise OnboardingError("proposal must be explicitly accepted by a reviewer before promotion")
    return {"status": "eligible_for_promotion", "claims": payload.get("claims", [])}


def _starter_files(root: Path) -> dict[Path, str]:
    files: dict[Path, str] = {
        root / "rail.yaml": "version: 1\nproject:\n  name: %s\n  slug: %s\n  mode: markdown_graph\n  knowledge_mode: software\n" % (root.name, re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")),
    }
    for name, description in STARTER_SKILLS.items():
        files[root / "skills" / f"{name}.md"] = f"---\nid: {name}\nstatus: starter\n---\n\n# {name.replace('-', ' ').title()}\n\n{description}\n"
    for name, workflow in WORKFLOWS.items():
        files[root / "research_plan" / "workflows" / f"{name}.yaml"] = yaml.safe_dump(
            {
                "version": 1,
                "id": name,
                "description": workflow["description"],
                "schedule": "",
                "review_required": True,
                "steps": workflow["steps"],
            },
            sort_keys=False,
        )
    return files


def onboard_project(path: str | Path, *, apply: bool = False, runner: str | None = None, dry_run: bool = False, allowed_root: str | Path | None = None) -> dict[str, Any]:
    if runner and runner not in RUNNERS:
        raise OnboardingError(f"runner must be one of: {', '.join(sorted(RUNNERS))}")
    discovery = discover_project(path, allowed_root=allowed_root)
    root = Path(discovery["root"])
    planned = _starter_files(root)
    snapshot_dir = root / "research_plan" / "state" / "project_discovery"
    fingerprint_id = discovery["fingerprint"].removeprefix("sha256:")
    state = snapshot_dir / f"{fingerprint_id}.json"
    planned[state] = json.dumps(discovery, indent=2) + "\n"
    if runner:
        work_order = {
            "contract": CONTRACTS["work_order"], "runner": runner, "execution": "external",
            "dry_run": dry_run, "root": str(root), "fingerprint": discovery["fingerprint"],
            "bounds": {"read_root": ".", "write_paths": ["research_plan/state/project_profile.proposal.json", "research_plan/state/automation_recommendations.proposal.json"], "launch_subprocess": False},
            "requested_contracts": [CONTRACTS["profile"], CONTRACTS["recommendations"]], "review_required": True,
        }
        wo_path = root / "research_plan" / "state" / "project_onboarding_work_orders" / f"{fingerprint_id}.json"
        planned[wo_path] = json.dumps(work_order, indent=2) + "\n"
    missing = [p for p in planned if not p.exists()]
    written: list[str] = []
    if apply:
        for target in missing:
            if not target.resolve().is_relative_to(root):
                raise OnboardingError("planned write escapes the authorized root")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(planned[target], encoding="utf-8")
            written.append(target.relative_to(root).as_posix())
    return {"status": "applied" if apply else "preview", "mode": discovery["mode"], "root": str(root), "fingerprint": discovery["fingerprint"], "would_create": [p.relative_to(root).as_posix() for p in missing], "written": written, "preserved": [p.relative_to(root).as_posix() for p in planned if p.exists() and p not in missing], "discovery": discovery}
