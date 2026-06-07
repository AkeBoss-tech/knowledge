#!/usr/bin/env bash
# Install RAIL core dev stack on macOS, Linux, or WSL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OS="$(uname -s)"
echo "RAIL install — $OS"

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
    echo "Created .env from .env.example — set CONVEX_URL and API keys before running."
  else
    echo "No .env found. Create one with CONVEX_URL and provider keys (see docs/INSTALL.md)."
  fi
fi

echo ""
echo "Done. Next steps:"
echo "  make run          # API :8000"
echo "  make seed         # seed Convex (if configured)"
echo "  rail --help       # CLI"
echo ""
echo "Agent CLIs: ./scripts/install-agent-clis.sh"
