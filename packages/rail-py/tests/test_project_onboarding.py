import json
import os
import subprocess
from pathlib import Path

import pytest

from rail.project_onboarding import OnboardingError, discover_project, onboard_project, promote_proposal, validate_proposal
from rail.knowledge import KnowledgeRuntime


def _repo(tmp_path: Path, files: dict[str, str], *, git: bool = False) -> Path:
    root = tmp_path / "ordinary-project"
    root.mkdir(parents=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "KRAIL Tests"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "tests@krail.local"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def _source_tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes | None]]:
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", path.stat().st_mode, None)
        else:
            snapshot[relative] = ("file", path.stat().st_mode, path.read_bytes())
    return snapshot


def test_discover_project_is_read_only(tmp_path):
    root = _repo(tmp_path, {
        "README.md": "# Existing project\n",
        "pyproject.toml": "[tool.pytest.ini_options]\n",
        "src/example.py": "VALUE = 1\n",
    })
    before = _source_tree_snapshot(root)

    discovery = discover_project(root)

    assert discovery["file_count"] == 3
    assert _source_tree_snapshot(root) == before


def test_opensaddle_worktrees_and_state_do_not_change_discovery(tmp_path):
    root = _repo(tmp_path, {
        "README.md": "# Existing project\n",
        "main.py": "VALUE = 1\n",
        "pyproject.toml": "[tool.pytest.ini_options]\n",
    }, git=True)
    baseline = discover_project(root)
    assert baseline["repository"]["dirty"] is False

    opensaddle_state = {
        ".opensaddle/episodes/ep_123.json": '{"status":"complete"}\n',
        ".opensaddle/onboarding-receipts/ep_123.json": '{"status":"committed"}\n',
        ".opensaddle/worktrees/ep_123/.git": "gitdir: /tmp/example-worktree\n",
        ".opensaddle/worktrees/ep_123/package.json": '{"scripts":{"build":"vite build"}}\n',
        ".opensaddle/worktrees/ep_123/src/index.ts": "export const nested = true\n",
    }
    for relative, content in opensaddle_state.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    assert subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert discover_project(root) == baseline

    (root / ".opensaddle/worktrees/ep_123/src/index.ts").write_text(
        "export const nested = false\n"
    )
    (root / ".opensaddle/worktrees/ep_123/main.go").write_text("package main\n")
    assert discover_project(root) == baseline


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


def test_changed_project_writes_new_immutable_discovery_snapshot(tmp_path):
    root = _repo(tmp_path, {"main.py": "pass\n"})
    first = onboard_project(root, apply=True)
    first_snapshots = sorted((root / "research_plan/state/project_discovery").glob("*.json"))
    assert len(first_snapshots) == 1
    original = first_snapshots[0].read_bytes()

    (root / "main.py").write_text("print('changed')\n")
    second = onboard_project(root, apply=True)
    snapshots = sorted((root / "research_plan/state/project_discovery").glob("*.json"))

    assert first["fingerprint"] != second["fingerprint"]
    assert len(snapshots) == 2
    assert first_snapshots[0].read_bytes() == original


def test_generated_starter_workflows_are_valid_krail_workflows(tmp_path):
    root = _repo(tmp_path, {"main.py": "pass\n"})
    onboard_project(root, apply=True)
    runtime = KnowledgeRuntime(root)

    for workflow_id in (
        "onboard_project",
        "recommend_project_automations",
        "project_memory_refresh",
        "verify_change",
    ):
        assert runtime.workflow_validate(workflow_id)["ok"] is True


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
    assert result["file_count"] == 2
    git_root = _repo(tmp_path / "git", {"main.py": "pass\n"}, git=True)
    clean = discover_project(git_root)["fingerprint"]
    (git_root / "main.py").write_text("print('dirty')\n")
    assert discover_project(git_root)["fingerprint"] != clean


def test_nested_source_agents_and_skills_are_not_mistaken_for_krail_state(tmp_path):
    root = _repo(tmp_path, {
        "src/agents/planner.py": "PLAN = True\n",
        "lib/skills/index.ts": "export const skill = true\n",
        "agents/generated.md": "# KRAIL-owned root state\n",
        "skills/generated.md": "# KRAIL-owned root state\n",
    })

    result = discover_project(root)

    assert result["file_count"] == 2
    assert result["languages"] == ["javascript/typescript", "python"]
    before = result["fingerprint"]
    (root / "src/agents/planner.py").write_text("PLAN = False\n")
    assert discover_project(root)["fingerprint"] != before


def test_generated_output_and_sensitive_dotfiles_never_enter_discovery(tmp_path):
    root = _repo(tmp_path, {
        "main.py": "pass\n",
        "out/generated.py": "SHOULD_NOT_APPEAR = True\n",
        ".npmrc": "//registry.example/:_authToken=canary\n",
        ".pypirc": "password=canary\n",
        ".netrc": "password canary\n",
        ".git-credentials": "https://user:canary@example.test\n",
        "id_ed25519": "canary-private-key\n",
    })

    baseline = discover_project(root)
    assert baseline["file_count"] == 1
    for relative in ("out/generated.py", ".npmrc", ".pypirc", ".netrc", ".git-credentials", "id_ed25519"):
        (root / relative).write_text("changed canary\n")
    assert discover_project(root) == baseline


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are not available on this platform")
def test_non_regular_files_are_skipped_without_blocking(tmp_path):
    root = _repo(tmp_path, {"main.py": "pass\n"})
    os.mkfifo(root / "event-stream")

    assert discover_project(root)["file_count"] == 1


def test_discovery_fails_closed_at_resource_bounds(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"one.py": "1\n", "two.py": "2\n"})
    monkeypatch.setattr("rail.project_onboarding.MAX_DISCOVERY_FILES", 1)

    with pytest.raises(OnboardingError, match="file safety limit"):
        discover_project(root)


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
    order_path = next((root / "research_plan/state/project_onboarding_work_orders").glob("*.json"))
    order = json.loads(order_path.read_text())
    assert order["runner"] == runner and order["dry_run"] is True
    assert order["execution"] == "external" and order["bounds"]["launch_subprocess"] is False
    assert order_path.relative_to(root).as_posix() in result["written"]


def test_proposal_validation_and_review_gate():
    with pytest.raises(OnboardingError): validate_proposal({"contract": "bad"})
    proposal = {"contract": "krail.project-profile/v1", "review": {"status": "proposed"}, "claims": [{"text": "x", "evidence": [{"path": "README.md"}]}]}
    validate_proposal(proposal)
    with pytest.raises(OnboardingError): promote_proposal(proposal)
    proposal["review"] = {"status": "accepted", "reviewed_by": "maintainer"}
    assert promote_proposal(proposal)["status"] == "eligible_for_promotion"
