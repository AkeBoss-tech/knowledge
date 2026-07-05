# Contributing to KRAIL

KRAIL is early and intentionally local-first. Small, well-scoped changes are
the most useful.

## Good Contributions

- Fix install or setup issues.
- Improve docs that were confusing during a first run.
- Add focused tests around `packages/rail-py`, `packages/mcp-server`,
  `packages/engine`, or the local API adapter.
- Add compact public examples under `examples/`.
- Improve `doctor`, validation, search, capture, packs, task dispatch, or
  integrity checks.
- Port upstream RAIL improvements only when they preserve the local-first,
  headless shape.

## Repository Hygiene

KRAIL produces local project state. Keep commits focused on source code, docs,
tests, and curated examples.

Do not commit:

- `.env` files or provider keys
- private keys, deploy keys, API tokens, or webhook secrets
- generated ontology databases such as `*.duckdb` or `*.sqlite`
- runtime sessions, audit traces, local runner directories, or cache folders
- large generated research outputs, PDFs, NDJSON dumps, or private project
  workspaces

Use `pilots/` or `generated_projects/` for local experiments. Use `examples/`
for curated public fixtures.

## Development Setup

```bash
./scripts/install-rail.sh
source .venv/bin/activate
krail --help
```

Optional local API:

```bash
make run
```

## Tests and Smoke Checks

Run the smallest relevant checks:

```bash
python -m compileall -q packages/rail-py/rail packages/mcp-server/rail_mcp
PYTHONPATH=packages/rail-py python -m rail.cli pack list
```

For a local project smoke test:

```bash
tmp=$(mktemp -d)
PYTHONPATH=packages/rail-py python -m rail.cli init "$tmp/krail-smoke" --pack research-intelligence
cd "$tmp/krail-smoke"
PYTHONPATH=/path/to/knowledge/packages/rail-py python -m rail.cli --local doctor
PYTHONPATH=/path/to/knowledge/packages/rail-py python -m rail.cli --local workflow init weekly_literature_refresh
PYTHONPATH=/path/to/knowledge/packages/rail-py python -m rail.cli --local workflow execute weekly_literature_refresh --dry-run
```

## Pull Request Expectations

- Keep changes scoped.
- Explain user-facing behavior changes.
- Include tests or smoke checks when practical.
- Mention migration or configuration changes.
- Avoid unrelated formatting churn.

## Porting From Upstream RAIL

Prefer hand-porting over broad merges. Good candidates are local validation,
quality gates, runner lifecycle hardening, repo-backed control-plane ideas,
and small examples.

Avoid reintroducing bundled frontend code, Convex, Jules, Railway/Vercel
assumptions, or generated validation blobs.
