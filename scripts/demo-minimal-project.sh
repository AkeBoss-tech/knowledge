#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/krail-demo-minimal.XXXXXX")"
PROJECT_DIR="$WORK_DIR/minimal-project"

cp -R "$ROOT_DIR/examples/minimal-project" "$PROJECT_DIR"

echo "Demo workspace: $PROJECT_DIR"

PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" doctor
PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" capture "Synthetic regional employment fixture ready for review" --topic pilot-readiness --entity KRAIL --entity-type Package
PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" inbox list
PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" search "employment index" --explain
PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" think "How does the synthetic employment index differ by region?"
PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" graph build
PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" graph entities --type Dataset
PYTHONPATH="$ROOT_DIR/packages/rail-py" python -m rail.cli --local --path "$PROJECT_DIR" workflow run weekly_research_review --dry-run
