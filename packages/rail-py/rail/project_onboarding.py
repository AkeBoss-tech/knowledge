"""Safe, deterministic discovery for bringing an existing repository into KRAIL."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

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
    ".ruff_cache", ".tox", ".next", ".nuxt", "generated", "artifacts", "out",
    ".krail", ".opensaddle", ".ontology",
}
KRAIL_ROOT_DIRS = {"research_plan", "skills", "agents", "topics", "sources"}
KRAIL_MANIFESTS = {"rail.yaml", "krail.yaml"}
SECRET_NAMES = re.compile(r"(^|[._-])(secret|credential|private|token|password|passwd|api[_-]?key)([._-]|$)", re.I)
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks"}
SECRET_FILES = {
    ".git-credentials", ".netrc", ".npmrc", ".pypirc",
    "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa",
}
MAX_FILE_BYTES = 2_000_000
MAX_DISCOVERY_FILES = 100_000
MAX_DISCOVERY_BYTES = 500_000_000
MAX_INVENTORY_BYTES = 64_000_000
MAX_RELATIVE_PATH_BYTES = 4_096
GIT_DEADLINE_SECONDS = 15.0
GIT_TERM_GRACE_SECONDS = 0.5
MATERIALIZATION_KEYS = {
    "recommendation_id",
    "discovery_fingerprint",
    "artifact_kind",
    "target_path",
    "target_contract",
}
MATERIALIZATION_TARGETS = {
    "codex_skill": (
        re.compile(r"^\.agents/skills/[a-z0-9]+(?:-[a-z0-9]+)*/SKILL\.md$"),
        "codex.project-skill/v1",
    ),
    "claude_skill": (
        re.compile(r"^\.claude/skills/[a-z0-9]+(?:-[a-z0-9]+)*/SKILL\.md$"),
        "claude.project-skill/v1",
    ),
    "krail_workflow": (
        re.compile(r"^research_plan/workflows/[a-z0-9]+(?:-[a-z0-9]+)*\.ya?ml$"),
        "krail.workflow/v1",
    ),
}

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


def _close_process_pipes(process: subprocess.Popen[bytes]) -> None:
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None and not pipe.closed:
            pipe.close()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Boundedly terminate the complete POSIX process group."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + GIT_TERM_GRACE_SECONDS
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _git_output(root: Path, *args: str) -> bytes:
    """Run a bounded, sanitized, side-effect-free Git inspection command."""

    if os.name != "posix":
        raise OnboardingError("project Git inspection is unavailable")
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ("git", *args),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, _ = process.communicate(timeout=GIT_DEADLINE_SECONDS)
        if process.returncode != 0:
            raise OnboardingError("project Git inspection failed")
        return stdout
    except subprocess.TimeoutExpired:
        if process is not None:
            _terminate_process_group(process)
            try:
                process.wait(timeout=GIT_TERM_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        raise OnboardingError("project Git inspection timed out") from None
    except OSError:
        if process is not None:
            _terminate_process_group(process)
        raise OnboardingError("project Git inspection failed") from None
    finally:
        if process is not None:
            _close_process_pipes(process)


def _git_text(root: Path, *args: str) -> str:
    try:
        return _git_output(root, *args).decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        raise OnboardingError("project Git inspection returned invalid output") from None


def _is_excluded_relative(relative: Path) -> bool:
    return (
        not relative.parts
        or any(part in EXCLUDED_DIRS for part in relative.parts)
        or relative.parts[0] in KRAIL_ROOT_DIRS
        or (len(relative.as_posix().encode("utf-8", errors="surrogateescape")) > MAX_RELATIVE_PATH_BYTES)
    )


def _safe_candidate(root: Path, relative: Path) -> tuple[Path, int] | None:
    if relative.is_absolute() or ".." in relative.parts or _is_excluded_relative(relative):
        return None
    path = root / relative
    name = path.name
    if root == path.parent and name in KRAIL_MANIFESTS:
        return None
    if (
        SECRET_NAMES.search(name)
        or name.lower() in SECRET_FILES
        or path.suffix.lower() in SECRET_SUFFIXES
        or name == ".env"
        or name.startswith(".env.")
    ):
        return None
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_FILE_BYTES
            or not path.resolve().is_relative_to(root)
        ):
            return None
    except OSError:
        return None
    return path, metadata.st_size


def _git_inventory(root: Path) -> list[Path]:
    raw = _git_output(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        ".",
    )
    if len(raw) > MAX_INVENTORY_BYTES:
        raise OnboardingError("project Git inventory exceeds the safety limit")
    if raw and not raw.endswith(b"\0"):
        raise OnboardingError("project Git inventory returned invalid output")
    result: list[Path] = []
    for encoded in raw.split(b"\0")[:-1]:
        if not encoded or len(encoded) > MAX_RELATIVE_PATH_BYTES or b"\0" in encoded:
            raise OnboardingError("project Git inventory returned an invalid path")
        relative = Path(os.fsdecode(encoded))
        if relative.is_absolute() or ".." in relative.parts:
            raise OnboardingError("project Git inventory returned an invalid path")
        result.append(relative)
    return result


def _directory_inventory(root: Path) -> list[Path]:
    result: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        excluded_here = EXCLUDED_DIRS | (KRAIL_ROOT_DIRS if current_path == root else set())
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in excluded_here and not (current_path / d).is_symlink()
        )
        result.extend((current_path / name).relative_to(root) for name in sorted(files))
    return result


def _safe_files(root: Path, *, git_repository: bool) -> list[Path]:
    file_count = 0
    total_bytes = 0
    result: list[Path] = []
    inventory = _git_inventory(root) if git_repository else _directory_inventory(root)
    for relative in sorted(set(inventory), key=lambda item: item.as_posix()):
        candidate = _safe_candidate(root, relative)
        if candidate is None:
            continue
        path, size = candidate
        file_count += 1
        total_bytes += size
        if file_count > MAX_DISCOVERY_FILES:
            raise OnboardingError(
                f"project discovery exceeds the {MAX_DISCOVERY_FILES:,}-file safety limit"
            )
        if total_bytes > MAX_DISCOVERY_BYTES:
            raise OnboardingError(
                f"project discovery exceeds the {MAX_DISCOVERY_BYTES:,}-byte safety limit"
            )
        result.append(path)
    return result


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
    ):
        if rel in by_name:
            for command, kind in commands: add(command, kind, rel)
    if "Makefile" in by_name:
        makefile = by_name["Makefile"].read_text("utf-8", errors="replace")
        for target, kind in (("test", "test"), ("lint", "lint"), ("typecheck", "typecheck"), ("build", "build")):
            if re.search(rf"(?m)^{re.escape(target)}\s*:(?:\s|$)", makefile):
                add(f"make {target}", kind, "Makefile")
    return found


def _ecosystems(root: Path, files: list[Path], revision: str) -> list[dict[str, Any]]:
    by_relative = {path.relative_to(root).as_posix(): path for path in files}
    markers: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("python", ("pyproject.toml", "setup.py", "setup.cfg")),
        ("node", ("package.json",)),
        ("go", ("go.mod",)),
        ("rust", ("Cargo.toml",)),
        ("make", ("Makefile",)),
    )
    result: list[dict[str, Any]] = []
    for ecosystem, names in markers:
        evidence = [
            _evidence(root, by_relative[name], revision)
            for name in names
            if name in by_relative
        ]
        if evidence:
            result.append(
                {
                    "ecosystem": ecosystem,
                    "manifests": [item["path"] for item in evidence],
                    "evidence": evidence,
                }
            )
    requirements = sorted(
        path for path in by_relative if re.fullmatch(r"requirements(?:-[a-z0-9_.-]+)?\.txt", path)
    )
    if requirements:
        evidence = [_evidence(root, by_relative[path], revision) for path in requirements]
        result.append(
            {
                "ecosystem": "python_requirements",
                "manifests": requirements,
                "evidence": evidence,
            }
        )
    return result


def discover_project(path: str | Path, *, allowed_root: str | Path | None = None) -> dict[str, Any]:
    requested = Path(path).expanduser().absolute()
    if requested.is_symlink() or not requested.is_dir():
        raise OnboardingError("project path must be an existing, non-symlink directory")
    root = requested.resolve()
    boundary = Path(allowed_root).expanduser().resolve() if allowed_root else root
    if not root.is_relative_to(boundary):
        raise OnboardingError("project path escapes the authorized root")
    git_repository = (root / ".git").exists()
    files = _safe_files(root, git_repository=git_repository)
    revision = _git_text(root, "rev-parse", "--verify", "HEAD") if git_repository else ""
    dirty = bool(
        _git_text(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude,glob)**/.opensaddle/**",
        )
    ) if git_repository else False
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
        "repository": {"kind": "git" if git_repository else "directory", "revision": revision or None, "dirty": dirty},
        "fingerprint": "sha256:" + digest.hexdigest(), "languages": sorted(set(languages)),
        "ecosystems": _ecosystems(root, files, revision),
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
    fingerprint = payload.get("fingerprint")
    for recommendation in payload.get("recommendations", []):
        descriptor = recommendation.get("materialization")
        if descriptor is None:
            continue
        if not isinstance(descriptor, dict) or set(descriptor) != MATERIALIZATION_KEYS:
            raise OnboardingError("materialization descriptor has an unsupported shape")
        recommendation_id = recommendation.get("recommendation_id")
        if descriptor["recommendation_id"] != recommendation_id:
            raise OnboardingError("materialization recommendation binding is invalid")
        if descriptor["discovery_fingerprint"] != fingerprint:
            raise OnboardingError("materialization discovery binding is invalid")
        target = MATERIALIZATION_TARGETS.get(descriptor["artifact_kind"])
        if target is None:
            raise OnboardingError("unsupported materialization artifact kind")
        path_pattern, expected_contract = target
        if (
            not isinstance(descriptor["target_path"], str)
            or path_pattern.fullmatch(descriptor["target_path"]) is None
            or descriptor["target_contract"] != expected_contract
        ):
            raise OnboardingError("materialization target is invalid")
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
