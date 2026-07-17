from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


REPO_ROOT = Path(__file__).parents[3]


def _copy_minimal_project(tmp_path: Path) -> Path:
    source = REPO_ROOT / "examples" / "minimal-project"
    target = tmp_path / "minimal-project"
    shutil.copytree(source, target)
    vector_db = target / ".krail" / "vector.sqlite"
    if vector_db.exists():
        vector_db.unlink()
    return target


def test_search_defaults_to_deterministic_hybrid_for_minimal_project_fixture(tmp_path: Path):
    root = _copy_minimal_project(tmp_path)
    runtime = KnowledgeRuntime(root)

    result = runtime.search("Synthetic Regional Indicators", limit=3, explain=True)

    assert result["explain"]["mode"] == "local_hybrid"
    assert result["explain"]["ranker"] == "deterministic_rrf_v2"
    assert result["query_plan"]["version"] == "krail.query-plan/v1"
    assert result["evidence_packet"]["version"] == "krail.evidence-packet/v1"
    assert result["retrieval_trace"]["trace_digest"].startswith("sha256:")
    assert result["rag"]["status"] == "ok"
    assert result["rag"]["embedding"]["provider"] == "local_hash"
    assert [hit["path"] for hit in result["hits"][:2]] == [
        "topics/brief.md",
        "research_plan/current_plan.md",
    ]
    assert {hit["path"] for hit in result["vector_hits"][:3]} >= {
        "topics/brief.md",
        "research_plan/current_plan.md",
    }


@pytest.mark.parametrize(
    ("query", "expected_path"),
    [
        ("Synthetic Regional Indicators", "topics/brief.md"),
        ("employment index", "topics/data/observations.csv"),
    ],
)
def test_minimal_project_fixture_queries_return_expected_top_evidence(
    tmp_path: Path,
    query: str,
    expected_path: str,
):
    root = _copy_minimal_project(tmp_path)
    runtime = KnowledgeRuntime(root)

    result = runtime.search(query, limit=5)

    assert result["hits"][0]["path"] == expected_path


def test_vector_hits_are_permission_filtered_before_ranking(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Permission Project", slug="permission-project")
    (root / "topics" / "public-note.md").write_text("# Public\n\nshared term only.\n", encoding="utf-8")
    (root / "topics" / "private-note.md").write_text(
        "---\n"
        "visibility: private\n"
        "allowed_roles:\n"
        "  - reviewer\n"
        "---\n\n"
        "# Private\n\nhidden term only.\n",
        encoding="utf-8",
    )

    result = KnowledgeRuntime(root).search("hidden term", limit=5, rag=True)

    assert "topics/private-note.md" not in {hit["path"] for hit in result["hits"]}
    assert "topics/private-note.md" not in {hit["path"] for hit in result["vector_hits"]}
    assert result["rag"]["status"] == "ok"
    assert result["vector_hits"]


def test_search_reports_embedding_provider_errors_without_crashing(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Search Project", slug="search-project")
    (root / "topics" / "public-note.md").write_text("# Public\n\npubliccodename is available.\n", encoding="utf-8")

    monkeypatch.setenv("KRAIL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = KnowledgeRuntime(root).search("publiccodename", limit=5)

    assert result["hits"][0]["path"] == "topics/public-note.md"
    assert result["rag"]["status"] == "error"
    assert result["rag"]["error"]["provider"] == "openai"
    assert "OPENAI_API_KEY" in result["rag"]["error"]["message"]
    assert result["rag"]["fallback"] == "lexical_graph_only"
    assert result["vector_hits"] == []


def test_vector_build_error_is_clear_and_non_destructive(tmp_path: Path, monkeypatch):
    root = bootstrap_future_project(tmp_path, name="Search Project", slug="search-project")
    (root / "topics" / "public-note.md").write_text("# Public\n\npubliccodename is available.\n", encoding="utf-8")
    runtime = KnowledgeRuntime(root)

    indexed = runtime.vector_build(provider="local_hash")
    assert indexed["status"] == "indexed"
    vector_db = root / ".krail" / "vector.sqlite"
    before = vector_db.read_bytes()

    monkeypatch.setenv("KRAIL_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    failed = runtime.vector_build()

    assert failed["status"] == "error"
    assert failed["error"]["provider"] == "openai"
    assert vector_db.exists()
    assert vector_db.read_bytes() == before

    recovered = runtime.vector_search("publiccodename", limit=3)
    assert recovered["hits"][0]["path"] == "topics/public-note.md"
