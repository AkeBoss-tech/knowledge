from __future__ import annotations

import sys
from pathlib import Path

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def test_think_returns_citations_and_source_freshness(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Think Project", slug="think-project")
    runtime = KnowledgeRuntime(root)

    result = runtime.think("project objective", limit=3)

    assert result["citations"]
    assert result["source_freshness"]["dependency_manifest_ok"] is True
    assert "source_refresh" in "\n".join(result["suggested_next_actions"])
    assert result["confidence"] in {"low", "medium"}

