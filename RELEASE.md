# Release Checklist

Target: `v0.1.0-local-preview`

## Fresh Clone Smoke

```bash
git clone https://github.com/AkeBoss-tech/knowledge.git
cd knowledge
git checkout future
./scripts/install-rail.sh
krail --version
krail --local --path examples/minimal-project doctor
krail --local --path examples/minimal-project graph build
krail --local --path examples/minimal-project graph check
krail --local --path examples/minimal-project vector build
krail --local --path examples/minimal-project search "employment index" --rag --explain
```

## Pre-Tag Checks

```bash
PYTHONPATH=packages/rail-py:packages/mcp-server pytest -q \
  packages/rail-py/tests/test_bootstrap.py \
  packages/rail-py/tests/test_markdown_graph.py \
  packages/rail-py/tests/test_cli.py \
  packages/mcp-server/tests/test_server.py::test_mcp_graph_entities_calls_project \
  packages/mcp-server/tests/test_server.py::test_mcp_vector_search_calls_project

python3 -m compileall -q packages/rail-py/rail packages/mcp-server/rail_mcp
git diff --check
```

## Tag

```bash
git tag v0.1.0-local-preview
git push origin v0.1.0-local-preview
```
