# Release Checklist

Target: `v0.2.4`

Release train: pre-v1 packaging and automation hardening for `krail` and
`rail-mcp`.

Do not tag `1.0.0` yet. The release process is intended to be v1-ready, but the
remaining experimental surfaces are tracked in
`docs/v1-gap-closure-plan.md`.

## Fresh Clone Smoke

```bash
git clone https://github.com/AkeBoss-tech/knowledge.git
cd knowledge
python -m pip install --upgrade pip
pip install krail rail-mcp
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
rail-mcp --help
```

## Clean Checkout Build

```bash
python -m pip install --upgrade build twine
rm -rf packages/rail-py/dist packages/mcp-server/dist
python -m build packages/rail-py
python -m build packages/mcp-server
twine check packages/rail-py/dist/* packages/mcp-server/dist/*
```

## Fresh Wheel Install Smoke

```bash
python -m venv .venv-release
. .venv-release/bin/activate
python -m pip install --upgrade pip
pip install packages/rail-py/dist/*.whl
krail --version
pip install --find-links packages/rail-py/dist packages/mcp-server/dist/*.whl
rail-mcp --help
deactivate
rm -rf .venv-release
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

## CI Expectations

- GitHub Actions `CI` passes on Python 3.11, 3.12, and 3.13
- GitHub Actions `Release Packages` verifies tests on Python 3.11, 3.12, and
  3.13 before publishing
- Release workflow builds and publishes both `krail` and `rail-mcp`

## Tag

```bash
git tag -a v0.2.4 -m "KRAIL v0.2.4"
git push origin v0.2.4
```
