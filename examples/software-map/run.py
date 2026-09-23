"""Edit a copied route, detect dependent topics, and write a review proposal."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROUTE = Path("sources/sample-service/app/routes/health.py")
EXPECTED = {"topics/api-service.md", "topics/health-endpoint.md"}


def cli(project: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "rail.cli", "--local", "--path", str(project), *args],
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="retain the copied workspace for inspection")
    args = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix="krail-software-map-"))
    project = workspace / "project"
    try:
        shutil.copytree(HERE, project, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        baseline = cli(project, "sources", "check")
        assert baseline["status"] == "checked"
        route = project / ROUTE
        original = route.read_text()
        route.write_text(original + "\n@router.get('/ready')\ndef ready() -> dict[str, bool]:\n    return {'ready': True}\n")
        changed = cli(project, "sources", "check")
        affected = cli(project, "sources", "affected")
        assert changed["status"] == "checked"
        assert affected["changed_sources"] == ["local:sample-service-route"]
        paths = {item["path"] for item in affected["affected_documents"]}
        assert paths == EXPECTED, paths
        assert "topics/dependency-review.md" not in paths

        proposal = project / "artifacts/proposed-health-update.md"
        proposal.parent.mkdir(exist_ok=True)
        proposal.write_text(
            "# Proposed architecture topic update\n\n"
            "Status: proposed, awaiting review.\n\n"
            "The copied route now exposes `/ready` as well as `/health`. "
            "Review `topics/api-service.md` and `topics/health-endpoint.md` "
            "against `sources/sample-service/app/routes/health.py` before promotion. "
            "The unrelated dependency-review topic is unchanged.\n"
        )
        print("Changed source:", affected["changed_sources"][0])
        print("Affected topics:", ", ".join(sorted(paths)))
        print("Unrelated dependency topic: unchanged")
        print("Proposed update:", proposal)
        print(proposal.read_text().strip())
        if args.keep:
            print("Retained workspace:", project)
    finally:
        if not args.keep:
            shutil.rmtree(workspace)


if __name__ == "__main__":
    main()
