from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def _init_sample_repo(root: Path) -> Path:
    repo = root / "sample-repo"
    repo.mkdir()
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "docs" / "adr").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("* @platform-team\nsrc/api.py @backend-team\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[project]\nname='demo'\ndependencies=['fastapi>=0.110','pydantic>=2']\n[project.optional-dependencies]\ndev=['pytest>=8']\n",
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}}, indent=2) + "\n",
        encoding="utf-8",
    )
    (repo / "requirements-dev.txt").write_text("ruff==0.5.0\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (repo / "src" / "api.py").write_text("@app.get('/health')\ndef health():\n    return {'ok': True}\n", encoding="utf-8")
    (repo / "src" / "web.ts").write_text(
        "import { Router } from 'express'\n"
        "export const router = Router()\n"
        "export function renderHome() { return 'ok' }\n"
        "router.get('/ui-health', () => 'ok')\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_api.py").write_text("def test_health():\n    assert True\n", encoding="utf-8")
    (repo / "docs" / "adr" / "0001-demo.md").write_text("# Decision\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    return repo


def test_repo_snapshot_inventory_owners_dependencies_and_changed(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Software Project", slug="software-project", knowledge_mode="software")
    runtime = KnowledgeRuntime(root)
    repo = _init_sample_repo(root)

    snapshot = runtime.repo_snapshot("sample-repo")
    inventory = runtime.repo_inventory("sample-repo")
    owners = runtime.repo_owners("sample-repo")
    dependencies = runtime.repo_dependencies("sample-repo")
    symbols = runtime.repo_symbols("sample-repo")

    (repo / "src" / "api.py").write_text("@app.get('/health')\ndef health():\n    return {'ok': False}\n", encoding="utf-8")
    changed = runtime.repo_changed("sample-repo", base_ref="HEAD")

    assert snapshot["status"] == "inspected"
    assert snapshot["is_git"] is True
    assert snapshot["head"]
    assert inventory["status"] == "inspected"
    assert "python" in inventory["languages"]
    assert "src/api.py" in inventory["endpoint_files"]
    assert ".github/workflows/ci.yml" in inventory["ci_files"]
    assert owners["codeowners_path"] == ".github/CODEOWNERS"
    assert owners["entries"][0]["owners"] == ["@platform-team"]
    assert any(item["ecosystem"] == "python" for item in dependencies["ecosystems"])
    assert any(item["ecosystem"] == "node" for item in dependencies["ecosystems"])
    assert symbols["counts"]["files"] >= 2
    assert symbols["counts"]["by_language"]["python"] >= 1
    assert symbols["counts"]["by_language"]["typescript"] >= 1
    assert any(file["path"] == "src/api.py" and file["routes"][0]["path"] == "/health" for file in symbols["files"] if file["language"] == "python")
    assert any(file["path"] == "src/web.ts" and file["routes"][0]["path"] == "/ui-health" for file in symbols["files"] if file["language"] == "typescript")
    assert changed["status"] == "inspected"
    assert changed["working_tree"]["dirty"] is True
    assert "src/api.py" in changed["working_tree"]["changed_files"]
    assert changed["base_ref"] == "HEAD"
    assert (root / "research_plan" / "state" / "repo_inventory.json").exists()
    assert (root / "research_plan" / "state" / "repo_changes.json").exists()
    assert (root / "research_plan" / "state" / "repo_symbols.json").exists()


def test_repo_snapshot_reports_non_git_directories(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Software Project", slug="software-project", knowledge_mode="software")
    runtime = KnowledgeRuntime(root)
    repo = root / "plain-dir"
    repo.mkdir()
    (repo / "package.json").write_text('{"dependencies":{"next":"14.0.0"}}\n', encoding="utf-8")

    snapshot = runtime.repo_snapshot("plain-dir")
    changed = runtime.repo_changed("plain-dir")

    assert snapshot["status"] == "inspected"
    assert snapshot["is_git"] is False
    assert changed["status"] == "not_git"
