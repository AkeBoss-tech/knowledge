# Release checklist

## Current candidate: 1.2.0rc3

The SDK (`krail`) and MCP adapter (`rail-mcp`) now declare the coordinated next
prerelease `1.2.0rc3`; their public module versions match. The adapter requires
`krail>=1.2.0rc3,<2.0.0`. This is source metadata for an unpublished candidate,
not evidence of PyPI publication, a tag, a desktop bundle or hosted deployment.
The last published lanes remain recorded in [README](README.md#release-and-support-status).
Do not silently reuse the `1.2.0rc2` identity for current source wheels.

### Candidate gates

1. Record the exact Knowledge and Core source revisions, dirty state and source
   package bytes; build SDK, MCP and Core wheels from those inputs. Use a fresh
   output directory and retain each wheel's SHA-256. A dirty source candidate
   must remain labelled dirty until a clean committed build is separately run.
2. Check the local lockfile without resolving dependencies. SDK/MCP metadata,
   `rail.__version__`, `rail_mcp.__version__` and the MCP SDK requirement must
   agree. The SDK lockfile's editable project version is `1.2.0rc3`; dependencies
   are unchanged by this candidate version update.
3. Install the exact wheels into a fresh environment outside all source roots.
   Never use editable installs or `PYTHONPATH` to manufacture an installed pass.
   Run the existing [reviewed-source reuse qualifier](docs/agents/knowledge-31-reviewed-source-reuse.md)
   with all three wheel files and both source roots. It validates version and
   byte parity before and after the real local Core HTTP lifecycle. It refuses
   old/mutated wheels, source drift, missing packaged Python files and changed
   installed bytes before issuing the v2 receipt.
4. The lifecycle must preserve two independent Runs and their exact reviewed
   source provenance across Core/KRAIL recomposition, then deny source and
   derived artifact reads after withdrawal, including another recomposition.
   Unreviewed and wrong-project sources must be denied. This uses a synthetic
   deterministic worker; it does not establish model quality, crash recovery,
   provider-side erasure or deployed multiplayer.
5. Run the owning context/provider and MCP suites plus the stable local smoke
   below. Retain raw results and qualify each supported Python version before
   advertising that environment. Passing source tests alone is insufficient.
6. Only after those gates pass, prepare the matching immutable release tag and
   use the existing trusted-publishing workflow. No tag or publication is
   created merely by editing these files or running local qualification.

Build commands from the Knowledge root, using an already provisioned builder:

```sh
python -m build --wheel --outdir /absolute/fresh-artifacts packages/rail-py
python -m build --wheel --outdir /absolute/fresh-artifacts packages/mcp-server
# Build the selected Core checkout into that same directory separately.
```

The qualifier records local source/package parity, not a signed build
attestation or reproducible-build guarantee. It never installs dependencies,
publishes packages, changes a user's project or grants source access.

## Stable local-runtime baseline

The following checks describe the existing v1 local-runtime contract. They remain
useful regression checks but are not the complete 1.2.0rc3 acceptance gate.
`packages/api/` and `packages/engine/` retain independent development versions;
this checklist does not publish them.

## Contract

The v1 release covers:

- local project scaffolding with `krail init`
- `rail.yaml`-backed projects with `.ontology/`, `topics/`, `sources/`,
  `research_plan/`, `research_plan/state/`, and `artifacts/`
- `doctor`, `mode active`, and `pack active`
- `capture`, `inbox list`, `inbox promote`, and `topic upsert`
- deterministic `search`, typed `find`, optional `vector build` and
  `vector search`
- deterministic `think` envelopes with citations, gaps, conflicts, freshness,
  and next actions
- repo-backed tasks, workflow templates, materialized workflow execution, and
  dry-run dispatch
- `integrity status` and related ledger views
- MCP access to the stable local project subset
- additive typed actions, read-only retrievers, retrieval-v2 evidence packets,
  trigger vocabulary, and unified run inspection

The v1 release does not promise:

- hosted platform behavior
- host-level sandbox isolation
- autonomous agent execution without review
- model-backed synthesis as the default `think` path
- mature external pack registries
- perfect semantic retrieval or reranking

## Stable smoke reference

The packages require Python 3.11 or newer. The release gate explicitly tests
Python 3.11, 3.12, and 3.13; newer compatible interpreters may be used but are
not part of this release's CI matrix. Use the `local` extra so ontology-backed
integrity surfaces are available in the same environment as the release smokes
and focused tests:

```bash
git clone https://github.com/AkeBoss-tech/knowledge.git
cd knowledge
python -m pip install --upgrade pip
pip install 'krail[local]' rail-mcp
krail --version
krail init /tmp/krail-v1-smoke --pack research-intelligence --mode markdown_graph
krail --local --path /tmp/krail-v1-smoke mode active
krail --local --path /tmp/krail-v1-smoke pack active
krail --local --path /tmp/krail-v1-smoke doctor
krail --local --path examples/minimal-project doctor
krail --local --path examples/minimal-project search "employment index" --explain
krail --local --path examples/minimal-project think "employment index"
krail --local --path examples/minimal-project permissions doctor
krail --local --path examples/minimal-project integrity status
krail --local --path examples/minimal-project action list
krail --local --path examples/minimal-project retriever list
krail --local --path examples/minimal-project trigger list
krail --local --path examples/minimal-project run list
krail docs query knowledge-operations
krail --local --path examples/minimal-project workflow list
krail --local --path examples/minimal-project workflow execute source_refresh --dry-run
krail --local --path examples/minimal-project workflow execute weekly_research_review --dry-run
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
krail --local --path examples/minimal-project integrity status
pip install --find-links packages/rail-py/dist packages/mcp-server/dist/*.whl
rail-mcp --help
deactivate
rm -rf .venv-release
```

Run the offline end-to-end trust-lifecycle smoke from the repository root. It
creates a temporary project and proves `init -> capture -> inbox promote/topic
update -> think registration -> integrity status`, including the expected
pending-evidence gate:

```bash
bash scripts/trust-lifecycle-smoke.sh
```

Run the separate offline nested-project smoke to verify parent/child init,
healthy mount resolution, federated search/find/think provenance, mount access
result shaping, mounted workflow dry-runs, and mounted child Codex CLI task
dispatch dry-runs:

```bash
bash scripts/nested-project-smoke.sh
```

The mount access checks cover KRAIL retrieval and proxy behavior only. They do
not claim host-level filesystem or process sandbox isolation.

## Focused Test Gate

At minimum, the docs-adjacent and contract-adjacent tests should pass from that
same environment:

```bash
PYTHONPATH=packages/rail-py:packages/mcp-server pytest -q \
  packages/rail-py/tests/test_bootstrap.py \
  packages/rail-py/tests/test_cli.py \
  packages/rail-py/tests/test_knowledge_modes.py \
  packages/rail-py/tests/test_think.py \
  packages/mcp-server/tests/test_server.py
```

## CI Expectations

- GitHub Actions `CI` passes on Python 3.11, 3.12, and 3.13
- GitHub Actions `Release Packages` verifies tests on Python 3.11, 3.12, and
  3.13 before publishing
- Release workflow builds and publishes both `krail` and `rail-mcp`
- The checked-in repo CI extends generated `krail ci init` project smoke with
  source-tree package tests, local extras, and distribution checks
- Manual `workflow_dispatch` runs may verify a ref, but publish jobs only run
  from `v*` tag refs

## Publication boundary

The existing release workflow publishes both distributions from matching `v*`
tag refs after verification. A manual workflow dispatch only verifies its ref.
Check publication permissions and approvals in the configured release environment
at action time. This document does not authorize a tag or publish an artifact.
