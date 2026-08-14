import json
import subprocess
from pathlib import Path

import pytest

from rail.project_onboarding import OnboardingError, discover_project, onboard_project, promote_proposal, validate_proposal


def _repo(tmp_path: Path, files: dict[str, str], *, git: bool = False) -> Path:
    root = tmp_path / "ordinary-project"
    root.mkdir(parents=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def test_preview_writes_nothing_and_apply_is_idempotent(tmp_path):
    root = _repo(tmp_path, {"README.md": "keep me\n", "pyproject.toml": "[tool.pytest.ini_options]\n"})
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    preview = onboard_project(root)
    assert preview["status"] == "preview"
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before
    first = onboard_project(root, apply=True)
    assert first["written"]
    snapshot = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    second = onboard_project(root, apply=True)
    assert second["written"] == []
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == snapshot
    assert (root / "README.md").read_bytes() == b"keep me\n"


@pytest.mark.parametrize(("files", "language"), [
    ({"pyproject.toml": "[tool.pytest.ini_options]\n", "main.py": "pass\n"}, "python"),
    ({"package.json": '{"scripts":{"test":"vitest"}}', "index.ts": "export {}"}, "javascript/typescript"),
    ({"go.mod": "module example.test/x\n", "main.go": "package main"}, "go"),
    ({"Cargo.toml": "[package]\nname='x'\nversion='0.1.0'", "main.rs": "fn main(){}"}, "rust"),
])
def test_language_discovery_and_evidence_backed_commands(tmp_path, files, language):
    result = discover_project(_repo(tmp_path, files))
    assert language in result["languages"]
    assert result["commands"]
    assert all(c["evidence"][0]["path"] and c["evidence"][0]["digest"].startswith("sha256:") for c in result["commands"])


def test_mixed_non_git_refresh_exclusions_and_dirty_fingerprint(tmp_path):
    root = _repo(tmp_path, {"rail.yaml": "version: 1\n", "main.py": "pass\n", "package.json": '{"scripts":{"build":"vite build"}}', ".env": "TOKEN=x", "node_modules/x.js": "ignored"})
    result = discover_project(root)
    assert result["mode"] == "refresh" and result["repository"]["kind"] == "directory"
    assert result["file_count"] == 3
    git_root = _repo(tmp_path / "git", {"main.py": "pass\n"}, git=True)
    clean = discover_project(git_root)["fingerprint"]
    (git_root / "main.py").write_text("print('dirty')\n")
    assert discover_project(git_root)["fingerprint"] != clean


def test_symlink_and_allowed_root_safety(tmp_path):
    root = _repo(tmp_path, {"main.py": "pass\n"})
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(OnboardingError): discover_project(link)
    with pytest.raises(OnboardingError): discover_project(root, allowed_root=tmp_path / "elsewhere")
    outside = tmp_path / "outside.txt"; outside.write_text("secret")
    (root / "escape").symlink_to(outside)
    assert discover_project(root)["file_count"] == 1


@pytest.mark.parametrize("runner", ["codex_cli", "claude_code"])
def test_dry_run_creates_bounded_work_order_without_launch(tmp_path, runner, monkeypatch):
    root = _repo(tmp_path, {"main.py": "pass\n"})
    result = onboard_project(root, apply=True, runner=runner, dry_run=True)
    order = json.loads((root / "research_plan/state/project_onboarding_work_order.json").read_text())
    assert order["runner"] == runner and order["dry_run"] is True
    assert order["execution"] == "external" and order["bounds"]["launch_subprocess"] is False
    assert "research_plan/state/project_onboarding_work_order.json" in result["written"]


def test_proposal_validation_and_review_gate():
    with pytest.raises(OnboardingError): validate_proposal({"contract": "bad"})
    proposal = {"contract": "krail.project-profile/v1", "review": {"status": "proposed"}, "claims": [{"text": "x", "evidence": [{"path": "README.md"}]}]}
    validate_proposal(proposal)
    with pytest.raises(OnboardingError): promote_proposal(proposal)
    proposal["review"] = {"status": "accepted", "reviewed_by": "maintainer"}
    assert promote_proposal(proposal)["status"] == "eligible_for_promotion"
