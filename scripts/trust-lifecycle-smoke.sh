#!/usr/bin/env bash
# Offline end-to-end contract smoke for the KRAIL trust lifecycle.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/krail-trust-lifecycle.XXXXXX")"
PROJECT_DIR="$WORK_DIR/project"

cleanup() {
  if [[ "${KRAIL_KEEP_WORKDIR:-0}" != "1" ]]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

if [[ "${KRAIL_SOURCE_TREE:-0}" == "1" ]]; then
  export PYTHONPATH="$ROOT_DIR/packages/rail-py${PYTHONPATH:+:$PYTHONPATH}"
fi

run_json() {
  local output_path="$1"
  shift
  "$@" > "$output_path"
}

assert_json() {
  local result_path="$1"
  local expression="$2"
  "$PYTHON_BIN" - "$result_path" "$expression" <<'PY'
import json
import sys

result_path, expression = sys.argv[1:]
payload = json.loads(open(result_path, encoding="utf-8").read())
if not eval(expression, {"__builtins__": {}}, {"payload": payload}):
    raise SystemExit(f"Unexpected JSON result for {result_path}: {expression}")
PY
}

echo "Trust lifecycle smoke workspace: $PROJECT_DIR"
run_json "$WORK_DIR/init.json" \
  "$PYTHON_BIN" -m rail.cli init "$PROJECT_DIR" --pack research-intelligence --mode markdown_graph
assert_json "$WORK_DIR/init.json" 'payload["status"] == "initialized"'

run_json "$WORK_DIR/capture.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" capture \
  "The deployment runbook says production releases require a reviewer." \
  --topic deployment --entity release-runbook --entity-type Document
assert_json "$WORK_DIR/capture.json" 'payload["status"] == "captured" and payload["path"].startswith("topics/inbox/")'
CAPTURE_PATH="$($PYTHON_BIN - "$WORK_DIR/capture.json" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1], encoding="utf-8").read())["path"])
PY
)"

run_json "$WORK_DIR/inbox.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" inbox list
assert_json "$WORK_DIR/inbox.json" 'payload["unhandled"] == 1'

run_json "$WORK_DIR/promote.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" inbox promote "$CAPTURE_PATH" \
  --topic deployment-runbook --type procedure --entity release-runbook --entity-type Document
assert_json "$WORK_DIR/promote.json" 'payload["status"] == "promoted" and payload["topic"]["path"] == "topics/deployment-runbook.md"'

run_json "$WORK_DIR/topic.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" topic upsert deployment-runbook \
  --content "Captured runbook note: production releases require a reviewer. Evidence review is pending." \
  --source-path "$CAPTURE_PATH"
assert_json "$WORK_DIR/topic.json" 'payload["status"] == "updated"'

run_json "$WORK_DIR/search.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" search "deployment runbook reviewer" --no-rag
assert_json "$WORK_DIR/search.json" '"topics/deployment-runbook.md" in [hit["path"] for hit in payload["hits"]]'

run_json "$WORK_DIR/think.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" think \
  "What does the deployment runbook say about review?" \
  --output "$PROJECT_DIR/artifacts/deployment-review-think.json" \
  --register-integrity --title "Deployment review trust smoke"
assert_json "$WORK_DIR/think.json" 'payload["status"] == "done" and payload["integrity"]["status"] == "registered" and payload["integrity"]["verification_run"]["status"] == "passed"'

run_json "$WORK_DIR/integrity.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" integrity status
assert_json "$WORK_DIR/integrity.json" 'payload["summary"]["artifactCount"] == 1 and payload["summary"]["verificationRunCount"] == 1 and payload["summary"]["claimCandidateCount"] >= 1 and payload["summary"]["status"] == "missing_evidence"'

"$PYTHON_BIN" - "$WORK_DIR" "$PROJECT_DIR" <<'PY'
import json
import sys
from pathlib import Path

results, project = map(Path, sys.argv[1:])
def read(name):
    return json.loads((results / f"{name}.json").read_text())

print("capture:", read("capture")["path"])
print("promotion:", read("promote")["topic"]["path"])
print("retrieved:", read("search")["hits"][0]["path"])
print("artifact:", project / "artifacts/deployment-review-think.json")
print("integrity:", read("integrity")["summary"]["status"])
print("claim candidates:", read("integrity")["summary"]["claimCandidateCount"])
PY
echo "The artifact passed registration; its candidate claims still need evidence review."
