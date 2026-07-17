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


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "retrieval_v2"


@pytest.fixture
def benchmark_runtime(tmp_path: Path) -> KnowledgeRuntime:
    root = bootstrap_future_project(
        tmp_path / "retrieval-v2-project",
        name="Retrieval v2 Benchmark",
        slug="retrieval-v2-benchmark",
        mode="markdown_graph",
        knowledge_mode="software",
    )
    shutil.copytree(FIXTURE_ROOT, root, dirs_exist_ok=True)
    runtime = KnowledgeRuntime(root)
    runtime.graph_build(write=True)
    return runtime


@pytest.mark.parametrize(
    ("query", "expected_path", "expected_retriever"),
    [
        ("cobalt route leases", "topics/aurora-routing.md", "lexical"),
        ("MeridianLink", "topics/graph-consumer.md", "graph"),
        ("function reconcile_quartz_token", "code/quartz_worker.py", "exact_code"),
        ("latest copperfin rollout status", "topics/a-current-release.md", "recency"),
        ("who owns the lattice gateway", "topics/owned-gateway.md", "ownership"),
    ],
)
def test_retrieval_v2_benchmark_top_k_and_specialized_signals(
    benchmark_runtime: KnowledgeRuntime,
    query: str,
    expected_path: str,
    expected_retriever: str,
):
    result = benchmark_runtime.search(query, limit=3, explain=True, rag=False)

    assert result["hits"][0]["path"] == expected_path
    assert expected_path in result["retrieval_trace"]["retrievers"][expected_retriever]["top_records"]
    assert expected_retriever in result["hits"][0]["retrievers"]
    assert result["explain"]["mode"] == "local_keyword_graph"
    assert "vector" not in result["query_plan"]["retrievers"]


def test_retrieval_v2_benchmark_trace_digest_is_repeatable(benchmark_runtime: KnowledgeRuntime):
    first = benchmark_runtime.search("latest copperfin rollout status", limit=3, rag=False)
    second = benchmark_runtime.search("latest copperfin rollout status", limit=3, rag=False)

    assert first["retrieval_trace"]["trace_digest"] == second["retrieval_trace"]["trace_digest"]
    assert [hit["path"] for hit in first["hits"]] == [hit["path"] for hit in second["hits"]]
    assert first["retrieval_trace"]["trace_digest"].startswith("sha256:")


def test_retrieval_v2_benchmark_exposes_source_trust_and_freshness_labels(
    benchmark_runtime: KnowledgeRuntime,
):
    result = benchmark_runtime.search("cobalt route leases", limit=2, rag=False)
    hit = result["hits"][0]
    evidence = result["evidence_packet"]["evidence"][0]

    assert (hit["source_type"], hit["trust_state"], hit["freshness"]) == (
        "topic",
        "verified",
        "fresh",
    )
    assert evidence["source_type"] == "topic"
    assert evidence["trust_state"] == "verified"
    assert evidence["freshness"] == "fresh"


def test_retrieval_v2_benchmark_returns_empty_evidence_for_no_result(
    benchmark_runtime: KnowledgeRuntime,
):
    result = benchmark_runtime.search("nonexistent zephyrwisp protocol", limit=3, rag=False)

    assert result["hits"] == []
    assert result["retrieval_trace"]["fused_records"] == 0
    assert result["evidence_packet"]["evidence"] == []
    assert result["evidence_packet"]["citations"] == []
