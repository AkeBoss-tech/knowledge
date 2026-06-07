# Release Checklist

Target: `v0.2.0-local-preview`

## Fresh Clone Smoke

```bash
git clone https://github.com/AkeBoss-tech/knowledge.git
cd knowledge
git checkout future
./scripts/install-rail.sh
krail --version
krail --local --path examples/minimal-project doctor
krail --local --path examples/minimal-project sources validate
krail --local --path examples/minimal-project sources check
krail --local --path examples/minimal-project sources affected
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
  packages/rail-py/tests/test_issue_intake.py \
  packages/rail-py/tests/test_source_dependencies.py \
  packages/rail-py/tests/test_workflows.py \
  packages/mcp-server/tests/test_server.py::test_mcp_graph_entities_calls_project \
  packages/mcp-server/tests/test_server.py::test_mcp_vector_search_calls_project \
  packages/mcp-server/tests/test_server.py::test_mcp_sources_affected_passes_source_ids \
  packages/mcp-server/tests/test_server.py::test_mcp_sources_check_calls_project

python3 -m compileall -q packages/rail-py/rail packages/mcp-server/rail_mcp
python3 -m py_compile scripts/krail_issue_intake.py
git diff --check
```

## Tag

```bash
git tag -a v0.2.0-local-preview -m "KRAIL v0.2.0 local preview"
git push origin v0.2.0-local-preview
```
