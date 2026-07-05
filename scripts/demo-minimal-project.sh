#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project doctor
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project search "employment index" --explain
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project think "How does the synthetic employment index differ by region?"
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project graph build
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project graph entities --type Dataset
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project workflow execute weekly_research_review --dry-run
