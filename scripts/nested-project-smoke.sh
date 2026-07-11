#!/usr/bin/env bash
# Offline integration smoke for parent/child KRAIL mounts.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/krail-nested-project.XXXXXX")"
PARENT_DIR="$WORK_DIR/parent"
CHILD_DIR="$WORK_DIR/child"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

export PYTHONPATH="$ROOT_DIR/packages/rail-py${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("The nested-project smoke requires Python 3.11+")
PY

run_json() {
  local output_path="$1"
  shift
  "$@" >"$output_path"
}

assert_json() {
  local result_path="$1"
  local expression="$2"
  "$PYTHON_BIN" - "$result_path" "$expression" <<'PY'
import json
import sys

result_path, expression = sys.argv[1:]
with open(result_path, encoding="utf-8") as handle:
    payload = json.load(handle)
allowed = {"all": all, "any": any, "len": len}
if not eval(expression, {"__builtins__": {}}, {"payload": payload, **allowed}):
    raise SystemExit(f"Unexpected JSON result for {result_path}: {expression}")
PY
}

echo "Nested-project smoke workspace: $WORK_DIR"
run_json "$WORK_DIR/parent-init.json" \
  "$PYTHON_BIN" -m rail.cli init "$PARENT_DIR" --name "Parent Project" \
  --slug parent-project --pack research-intelligence --mode markdown_graph
run_json "$WORK_DIR/child-init.json" \
  "$PYTHON_BIN" -m rail.cli init "$CHILD_DIR" --name "Child Project" \
  --slug child-project --pack research-intelligence --mode markdown_graph
assert_json "$WORK_DIR/parent-init.json" 'payload["status"] == "initialized" and payload["materialized_workflows"]'
assert_json "$WORK_DIR/child-init.json" 'payload["status"] == "initialized" and payload["materialized_workflows"]'

run_json "$WORK_DIR/parent-doctor.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" doctor
run_json "$WORK_DIR/child-doctor.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$CHILD_DIR" doctor
assert_json "$WORK_DIR/parent-doctor.json" 'payload["ok"] is True'
assert_json "$WORK_DIR/child-doctor.json" 'payload["ok"] is True'

"$PYTHON_BIN" - "$PARENT_DIR" "$CHILD_DIR" <<'PY'
import sys
from pathlib import Path

import yaml

parent, child = map(Path, sys.argv[1:])
manifest_path = parent / "rail.yaml"
manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
manifest["mounts"] = [
    {
        "id": "child-delegated",
        "name": "Child delegated",
        "path": "../child",
        "access_mode": "delegated",
        "search_weight": 1.0,
    },
    {
        "id": "child-full",
        "name": "Child full",
        "path": "../child",
        "access_mode": "full",
        "search_weight": 1.0,
    },
    {
        "id": "child-metadata",
        "name": "Child metadata",
        "path": "../child",
        "access_mode": "metadata_only",
        "search_weight": 1.0,
    },
]
manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
(parent / "topics" / "parent-release.md").write_text(
    "# Parent release\n\nparentreleaseanchor belongs to the parent project.\n",
    encoding="utf-8",
)
(child / "topics" / "child-release.md").write_text(
    "# Child release\n\nchildreleaseanchor belongs to the mounted child project.\n",
    encoding="utf-8",
)
PY

run_json "$WORK_DIR/mounts.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" mount list
assert_json "$WORK_DIR/mounts.json" 'payload["summary"] == {"total": 3, "healthy": 3, "unhealthy": 0}'
assert_json "$WORK_DIR/mounts.json" 'all(item["ok"] and item["slug"] == "child-project" for item in payload["mounts"])'

run_json "$WORK_DIR/search-delegated.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" search childreleaseanchor \
  --federated --mount child-delegated --no-rag
assert_json "$WORK_DIR/search-delegated.json" 'payload["hits"][0]["path"] == "child-delegated:topics/child-release.md"'
assert_json "$WORK_DIR/search-delegated.json" 'payload["hits"][0]["project"] == "child-project" and "snippet" in payload["hits"][0]'

run_json "$WORK_DIR/find-full.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" find childreleaseanchor \
  --federated --mount child-full --no-rag --json
assert_json "$WORK_DIR/find-full.json" 'payload["results"][0]["path"] == "child-full:topics/child-release.md"'
assert_json "$WORK_DIR/find-full.json" 'payload["results"][0]["child_path"] == "topics/child-release.md" and "snippet" in payload["results"][0]'

run_json "$WORK_DIR/think-delegated.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" think childreleaseanchor \
  --federated --mount child-delegated
assert_json "$WORK_DIR/think-delegated.json" 'payload["status"] == "done" and payload["federated"] is True'
assert_json "$WORK_DIR/think-delegated.json" '"child-delegated" in payload["consulted_mounts"]'
assert_json "$WORK_DIR/think-delegated.json" 'any(item["path"] == "child-delegated:topics/child-release.md" for item in payload["citations"])'

# metadata_only is a KRAIL result-shaping restriction. It is not a claim of
# host filesystem or process sandboxing.
run_json "$WORK_DIR/search-metadata.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" search childreleaseanchor \
  --federated --mount child-metadata --no-rag
assert_json "$WORK_DIR/search-metadata.json" 'payload["hits"][0]["path"] == "child-metadata:topics/child-release.md"'
assert_json "$WORK_DIR/search-metadata.json" '"snippet" not in payload["hits"][0] and payload["hits"][0]["project"] == "child-project"'

run_json "$WORK_DIR/workflow-list.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" workflow list --mount child-delegated
assert_json "$WORK_DIR/workflow-list.json" 'payload["mount"] == "child-delegated" and payload["project"] == "child-project"'
assert_json "$WORK_DIR/workflow-list.json" 'any(item["id"] == "weekly_literature_refresh" and item["readiness"] == "ready" for item in payload["available"])'

run_json "$WORK_DIR/workflow-execute.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" workflow execute \
  weekly_literature_refresh --mount child-delegated --dry-run
assert_json "$WORK_DIR/workflow-execute.json" 'payload["status"] == "dry_run" and payload["mount"] == "child-delegated"'
assert_json "$WORK_DIR/workflow-execute.json" 'payload["project"] == "child-project" and all(item["status"] == "dry_run" for item in payload["steps"])'

run_json "$WORK_DIR/task-create.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" task create \
  "Nested child release task" --description "Verify mounted child dispatch." \
  --runner codex_cli --role research --mount child-delegated
assert_json "$WORK_DIR/task-create.json" 'payload["status"] == "created" and payload["mount"] == "child-delegated"'
TASK_ID="$($PYTHON_BIN - "$WORK_DIR/task-create.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["task"]["id"])
PY
)"

run_json "$WORK_DIR/task-dispatch.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PARENT_DIR" task dispatch "$TASK_ID" \
  --runner codex_cli --dry-run --mount child-delegated
assert_json "$WORK_DIR/task-dispatch.json" 'payload["status"] == "dry_run" and payload["runner"] == "codex_cli"'
assert_json "$WORK_DIR/task-dispatch.json" 'payload["mount"] == "child-delegated" and payload["project"] == "child-project"'
assert_json "$WORK_DIR/task-dispatch.json" 'payload["command"][1] == "exec" and payload["work_order"].startswith("research_plan/work_orders/")'

echo "Nested-project smoke passed: mount health, provenance, access shaping, child workflow, and child Codex dry-run dispatch are release-ready."
