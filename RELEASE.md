# Release Checklist

Target: `v0.2.3-local-preview`

Planned scope: permission-aware local file tooling, mounted/federated KRAIL
projects, software-map repo inspection commands, git listeners, and the
associated CLI/MCP/test coverage updates.

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
krail --local --path examples/minimal-project think "employment index"
krail --local --path examples/minimal-project workflow list
krail --local --path examples/minimal-project grep "employment"
krail --local --path examples/minimal-project files list topics --recursive
krail --local --path examples/minimal-project graph summary --federated
```

## Pre-Tag Checks

```bash
PYTHONPATH=packages/rail-py:packages/mcp-server pytest -q \
  packages/rail-py/tests/test_bootstrap.py \
  packages/rail-py/tests/test_markdown_graph.py \
  packages/rail-py/tests/test_cli.py \
  packages/rail-py/tests/test_issue_intake.py \
  packages/rail-py/tests/test_mounts.py \
  packages/rail-py/tests/test_source_dependencies.py \
  packages/rail-py/tests/test_think.py \
  packages/rail-py/tests/test_workflows.py \
  packages/rail-py/tests/test_permissions.py \
  packages/rail-py/tests/test_repo_tools.py \
  packages/rail-py/tests/test_listeners.py \
  packages/mcp-server/tests/test_server.py::test_mcp_graph_entities_calls_project \
  packages/mcp-server/tests/test_server.py::test_mcp_vector_search_calls_project \
  packages/mcp-server/tests/test_server.py::test_mcp_sources_affected_passes_source_ids \
  packages/mcp-server/tests/test_server.py::test_mcp_sources_check_calls_project \
  packages/mcp-server/tests/test_server.py::test_mcp_mount_list_calls_project \
  packages/mcp-server/tests/test_server.py::test_mcp_think_can_call_federated_project_think

python3 -m compileall -q packages/rail-py/rail packages/mcp-server/rail_mcp
python3 -m py_compile scripts/krail_issue_intake.py
git diff --check
```

## Tag

```bash
git tag -a v0.2.3-local-preview -m "KRAIL v0.2.3 local preview"
git push origin v0.2.3-local-preview
```
