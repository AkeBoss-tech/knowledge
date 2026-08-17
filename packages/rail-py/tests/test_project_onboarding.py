import json
import os
import signal
import subprocess
import sys
import time
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


def _fake_git(tmp_path: Path, body: str, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "git"
    executable.write_text(
        f"#!/usr/bin/env python3\nimport os, signal, subprocess, sys, time\n{body}\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return executable


def _materialization_proposal(
    artifact_kind: str,
    target_path: str,
    target_contract: str,
) -> dict:
    fingerprint = "sha256:" + "a" * 64
    recommendation_id = "materialize-project-artifact"
    return {
        "contract": "krail.automation-recommendations/v1",
        "fingerprint": fingerprint,
        "review": {"status": "proposed"},
        "claims": [],
        "recommendations": [
            {
                "recommendation_id": recommendation_id,
                "materialization": {
                    "recommendation_id": recommendation_id,
                    "discovery_fingerprint": fingerprint,
                    "artifact_kind": artifact_kind,
                    "target_path": target_path,
                    "target_contract": target_contract,
                },
            }
        ],
    }


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


def test_git_inventory_is_single_nul_safe_process_without_check_ignore(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"tracked.txt": "tracked\n"}, git=True)
    (root / "untracked.txt").write_text("untracked\n")
    calls: list[tuple[str, ...]] = []
    real_popen = subprocess.Popen

    def recording_popen(args, *popen_args, **popen_kwargs):
        calls.append(tuple(args))
        return real_popen(args, *popen_args, **popen_kwargs)

    monkeypatch.setattr("rail.project_onboarding.subprocess.Popen", recording_popen)
    result = discover_project(root)

    assert result["file_count"] == 2
    assert sum(call[1:2] == ("ls-files",) for call in calls) == 1
    assert not any("check-ignore" in call for call in calls)


def test_git_inventory_includes_tracked_ignored_and_excludes_untracked_ignored(tmp_path):
    root = _repo(tmp_path, {".gitignore": "*.ignored\n"}, git=True)
    (root / "tracked.ignored").write_text("tracked\n")
    subprocess.run(["git", "add", "-f", "tracked.ignored"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "track ignored file"], cwd=root, check=True)
    (root / "untracked.ignored").write_text("ignored\n")

    result = discover_project(root)
    paths = {
        locator["path"]
        for values in result["inventory"].values()
        for locator in values
    }

    assert result["file_count"] == 2
    assert "untracked.ignored" not in paths
    baseline = result["fingerprint"]
    (root / "tracked.ignored").write_text("changed\n")
    assert discover_project(root)["fingerprint"] != baseline


def test_git_inventory_preserves_newline_paths(tmp_path):
    root = _repo(tmp_path, {"ordinary.txt": "one\n"}, git=True)
    unusual = root / "line\nbreak.py"
    unusual.write_text("VALUE = 1\n")
    subprocess.run(["git", "add", unusual.name], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "unusual"], cwd=root, check=True)

    result = discover_project(root)

    assert result["file_count"] == 2
    assert "python" in result["languages"]


def test_large_git_inventory_is_stable_and_bounded(tmp_path):
    root = _repo(tmp_path, {"README.md": "large fixture\n"}, git=True)
    generated = root / "bulk"
    generated.mkdir()
    for index in range(10_001):
        (generated / f"item-{index:05d}.txt").write_text(f"{index}\n")

    started = time.monotonic()
    first = discover_project(root)
    first_elapsed = time.monotonic() - started
    started = time.monotonic()
    second = discover_project(root)
    second_elapsed = time.monotonic() - started

    assert first["file_count"] == 10_002
    assert second["fingerprint"] == first["fingerprint"]
    assert max(first_elapsed, second_elapsed) < 15
    assert max(first_elapsed, second_elapsed) < max(0.25, min(first_elapsed, second_elapsed) * 3)


def test_non_git_discovery_never_launches_git(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"main.py": "pass\n"})

    def forbidden(*args, **kwargs):
        raise AssertionError("Git must not run for non-Git discovery")

    monkeypatch.setattr("rail.project_onboarding.subprocess.Popen", forbidden)
    assert discover_project(root)["repository"] == {
        "kind": "directory",
        "revision": None,
        "dirty": False,
    }


def test_git_deadline_is_fixed_at_fifteen_seconds():
    import rail.project_onboarding as onboarding

    assert onboarding.GIT_DEADLINE_SECONDS == 15.0


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group containment")
@pytest.mark.parametrize("hanging_command", ["ls-files", "rev-parse", "status"])
def test_hanging_git_inspections_time_out_safely(tmp_path, monkeypatch, hanging_command):
    root = _repo(tmp_path, {"main.py": "pass\n"}, git=True)
    _fake_git(
        tmp_path,
        f'''\ncommand = sys.argv[1]\nif command == {hanging_command!r}:\n    time.sleep(60)\nif command == "ls-files":\n    os.write(1, b"main.py\\0")\nelif command == "rev-parse":\n    print("a" * 40)\nelif command == "status":\n    pass\n''',
        monkeypatch,
    )
    monkeypatch.setattr("rail.project_onboarding.GIT_DEADLINE_SECONDS", 0.5)
    started = time.monotonic()

    with pytest.raises(OnboardingError, match="timed out") as error:
        discover_project(root)

    assert time.monotonic() - started < 2
    assert str(root) not in str(error.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group containment")
def test_timeout_destroys_term_resistant_descendants(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"main.py": "pass\n"}, git=True)
    pid_file = tmp_path / "descendant.pid"
    _fake_git(
        tmp_path,
        f'''\nif sys.argv[1] == "ls-files":\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])\n    with open({str(pid_file)!r}, "w") as stream:\n        stream.write(str(child.pid))\n        stream.flush()\n        os.fsync(stream.fileno())\n    time.sleep(60)\n''',
        monkeypatch,
    )
    monkeypatch.setattr("rail.project_onboarding.GIT_DEADLINE_SECONDS", 2.0)

    with pytest.raises(OnboardingError, match="timed out"):
        discover_project(root)

    descendant = int(pid_file.read_text())
    for _ in range(100):
        try:
            os.kill(descendant, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        pytest.fail("TERM-resistant Git descendant survived process-group KILL")


def test_git_failures_never_expose_stderr_or_source_path(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"main.py": "pass\n"}, git=True)
    _fake_git(
        tmp_path,
        '''\nos.write(2, ("stderr-canary " + os.getcwd()).encode())\nraise SystemExit(9)\n''',
        monkeypatch,
    )

    with pytest.raises(OnboardingError) as error:
        discover_project(root)

    message = str(error.value)
    assert "stderr-canary" not in message
    assert str(root) not in message


def test_make_commands_require_declared_targets(tmp_path):
    root = _repo(tmp_path, {"Makefile": "all:\n\t@true\n", "test_example.py": "pass\n"})
    assert discover_project(root)["commands"] == []


@pytest.mark.parametrize("target,kind", [("test", "test"), ("lint", "lint"), ("typecheck", "typecheck"), ("build", "build")])
def test_make_commands_are_inferred_from_declared_targets(tmp_path, target, kind):
    root = _repo(tmp_path, {"Makefile": f"{target}:\n\t@true\n"})
    commands = discover_project(root)["commands"]
    assert [(item["command"], item["kind"]) for item in commands] == [
        (f"make {target}", kind)
    ]
    assert commands[0]["evidence"][0]["path"] == "Makefile"


@pytest.mark.parametrize(
    "files,ecosystem",
    [
        ({"pyproject.toml": "[project]\nname='demo'\n"}, "python"),
        ({"package.json": "{}"}, "node"),
        ({"go.mod": "module example.test/demo\n"}, "go"),
        ({"Cargo.toml": "[package]\nname='demo'\nversion='0.1.0'\n"}, "rust"),
        ({"requirements-dev.txt": "pytest\n"}, "python_requirements"),
    ],
)
def test_ecosystems_are_source_backed(tmp_path, files, ecosystem):
    result = discover_project(_repo(tmp_path, files))
    item = next(item for item in result["ecosystems"] if item["ecosystem"] == ecosystem)
    assert item["manifests"]
    assert all(locator["digest"].startswith("sha256:") for locator in item["evidence"])


@pytest.mark.parametrize(
    "kind,path,contract",
    [
        ("codex_skill", ".agents/skills/project-review/SKILL.md", "codex.project-skill/v1"),
        ("claude_skill", ".claude/skills/project-review/SKILL.md", "claude.project-skill/v1"),
        ("krail_workflow", "research_plan/workflows/project-review.yaml", "krail.workflow/v1"),
    ],
)
def test_materialization_descriptors_bind_exact_authoritative_targets(kind, path, contract):
    proposal = _materialization_proposal(kind, path, contract)
    assert validate_proposal(proposal) is proposal


@pytest.mark.parametrize("kind", ["claude_command", "opensaddle_definition", "generic_file"])
def test_legacy_and_unsupported_materialization_kinds_fail_closed(kind):
    proposal = _materialization_proposal(kind, ".agents/skills/demo/SKILL.md", "codex.project-skill/v1")
    with pytest.raises(OnboardingError, match="unsupported"):
        validate_proposal(proposal)


@pytest.mark.parametrize(
    "path",
    [
        ".codex/skills/demo/SKILL.md",
        ".agents/skills/../demo/SKILL.md",
        ".agents/skills/Demo/SKILL.md",
    ],
)
def test_non_authoritative_codex_materialization_paths_fail_closed(path):
    proposal = _materialization_proposal("codex_skill", path, "codex.project-skill/v1")
    with pytest.raises(OnboardingError, match="target"):
        validate_proposal(proposal)


@pytest.mark.parametrize("binding", ["recommendation_id", "discovery_fingerprint"])
def test_materialization_bindings_must_match_the_proposal(binding):
    proposal = _materialization_proposal(
        "claude_skill",
        ".claude/skills/demo/SKILL.md",
        "claude.project-skill/v1",
    )
    proposal["recommendations"][0]["materialization"][binding] = "mismatch"
    with pytest.raises(OnboardingError, match="binding"):
        validate_proposal(proposal)


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
