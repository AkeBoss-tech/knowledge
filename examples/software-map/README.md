# Software Map Example

This example shows how to use KRAIL as a local software-memory layer for a
project codebase.

It includes:

- `knowledge_mode: software`
- software-native workflows for `map_codebase`, `sync_recent_changes`,
  `capture_architecture_decision`, and `dependency_review`
- a bundled fixture repo under `sources/sample-service/`
- a file listener that watches the fixture tree and triggers
  `sync_recent_changes`
- stable software topics under `topics/`

The fixture repo is intentionally small and synthetic. It is designed to
exercise repo inventory, dependency extraction, CODEOWNERS parsing, endpoint
detection, and software-topic promotion without needing private code.

## Walkthrough

From the repository root:

```bash
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map doctor
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map repo inventory sources/sample-service
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map repo symbols sources/sample-service
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map repo owners sources/sample-service
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map repo dependencies sources/sample-service
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map workflow show map_codebase
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map listener list
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map listener poll sample_repo_tree --no-execute
```

## Optional Git Demo

The fixture lives inside the main KRAIL repository, so `repo snapshot` will use
the enclosing worktree unless you initialize a nested repository for the
fixture. If you want isolated `git` listener output, initialize one inside the
fixture:

```bash
cd examples/software-map/sources/sample-service
git init
git config user.name "Demo User"
git config user.email "demo@example.com"
git add .
git commit -m "initial fixture"
```

Then create a listener with the built-in template:

```bash
cd ../../..
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/software-map \
  listener init git_change_monitor --id sample_repo_git
```
