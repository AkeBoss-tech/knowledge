#!/usr/bin/env bash
# Offline end-to-end contract smoke for the KRAIL trust lifecycle.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/krail-trust-lifecycle.XXXXXX")"
PROJECT_DIR="$WORK_DIR/project"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

export PYTHONPATH="$ROOT_DIR/packages/rail-py${PYTHONPATH:+:$PYTHONPATH}"

run_json() {
  local output_path="$1"
  shift
  "$@" | tee "$output_path"
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
  "PDDLStream supports task and motion planning review." \
  --topic robotics --entity PDDLStream --entity-type Package
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
  --topic task-and-motion-planning --type method --entity PDDLStream --entity-type Package
assert_json "$WORK_DIR/promote.json" 'payload["status"] == "promoted" and payload["topic"]["path"] == "topics/task-and-motion-planning.md"'

run_json "$WORK_DIR/topic.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" topic upsert task-and-motion-planning \
  --content "Reviewed PDDLStream evidence for task and motion planning." \
  --source-path "$CAPTURE_PATH"
assert_json "$WORK_DIR/topic.json" 'payload["status"] == "updated"'

run_json "$WORK_DIR/think.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" think \
  "What evidence was captured about PDDLStream?" \
  --output "$PROJECT_DIR/artifacts/task-and-motion-planning-think.json" \
  --register-integrity --title "Task and motion planning trust smoke"
assert_json "$WORK_DIR/think.json" 'payload["status"] == "done" and payload["integrity"]["status"] == "registered" and payload["integrity"]["verification_run"]["status"] == "passed"'

run_json "$WORK_DIR/integrity.json" \
  "$PYTHON_BIN" -m rail.cli --local --path "$PROJECT_DIR" integrity status
assert_json "$WORK_DIR/integrity.json" 'payload["summary"]["artifactCount"] == 1 and payload["summary"]["verificationRunCount"] == 1 and payload["summary"]["claimCandidateCount"] >= 1 and payload["summary"]["status"] == "missing_evidence"'

echo "Trust lifecycle smoke passed: capture was promoted, think output was registered, and integrity status exposed the pending evidence review."
