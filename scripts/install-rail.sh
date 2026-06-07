#!/usr/bin/env bash
# Install KRAIL local dev stack on macOS, Linux, or WSL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OS="$(uname -s)"
echo "KRAIL install — $OS"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    return 1
  fi
}

need_cmd git
need_cmd python3

if [[ ! -d "$ROOT/.venv" ]]; then
  echo "Creating Python virtualenv at .venv"
  python3 -m venv "$ROOT/.venv"
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

echo "Installing Python packages (API, engine, CLI, MCP)..."
pip install -q --upgrade pip
pip install -q -e "$ROOT/packages/api" -e "$ROOT/packages/engine" -e "$ROOT/packages/rail-py" -e "$ROOT/packages/mcp-server"

if [[ ! -f "$ROOT/.env" ]]; then
  if [[ -f "$ROOT/.env.example" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "Created .env from .env.example — edit local overrides if needed."
  else
    echo "No .env found. Create one from .env.example if you need local overrides."
  fi
fi

echo ""
echo "Done. Next steps:"
echo "  source .venv/bin/activate"
echo "  krail --version"
echo "  krail --local --path examples/minimal-project doctor"
echo "  krail --local --path examples/minimal-project search \"employment index\" --rag --explain"
echo ""
echo "Agent CLIs: ./scripts/install-agent-clis.sh"
