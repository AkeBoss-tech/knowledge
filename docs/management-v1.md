# KRAIL Management Protocol v1

`krail.management.v1` is the stable machine-facing lifecycle surface used by an
OpenSaddle-managed KRAIL process. It is separate from the human-oriented
`rail`/`krail` CLI and from the read-only `krail.provider.v1` evidence contract.
The implementation calls KRAIL domain functions directly; it does not invoke or
parse `rail.cli`.

## Process and transport

Install `packages/rail-py`, then launch one isolated process bound to one
workspace:

```bash
krail-managed --workspace /absolute/path/to/project
```

The transport is newline-delimited JSON over standard input and standard
output. Each input line produces exactly one compact JSON response line and is
flushed immediately. Requests are limited to 1 MiB.

OpenSaddle uses the public compatibility adapter instead of speaking this
workspace-bound protocol directly:

```bash
krail-admin --contract krail.admin.v1 exchange
```

That adapter accepts one `krail.admin.v1` request on standard input, binds the
request's `project_root` to an internal management engine, translates
`init_plan`, `init`, `doctor`, and `reindex`, and emits one digest-bound receipt.
It never delegates to or parses the human CLI. `init` requires the complete
previously reviewed plan; changed or unreviewed plans fail closed.

Every request has `contract: "krail.management.v1"`, a caller-chosen
`request_id`, and one of these operations:

- `inspect`: return initialization status and the current workspace version and digest.
- `plan_init`: preview every directory and file initialization will create, including file sizes and SHA-256 content digests, without writing the workspace.
- `apply_init`: verify and atomically apply the complete returned plan with expected state and an idempotency key.
- `doctor`: call the doctor domain operation without CLI-output parsing or the human CLI executable-alignment subprocess check.
- `reindex`: rebuild only configured graph JSON, Mermaid, summary, and optional documentation exports with expected state and an idempotency key.

The process binding is the authority boundary: requests cannot select another
path. There are no shell, SQL, general graph-mutation, general file, or arbitrary
command operations.

## Initialization lifecycle

First send `inspect`, then copy its version and digest into `plan_init`:

```json
{"contract":"krail.management.v1","request_id":"2","operation":"plan_init","expected_workspace_version":"1:<hex>","expected_workspace_digest":"sha256:<hex>","options":{"name":"Example","mode":"markdown_graph","knowledge_mode":"project"}}
```

Send the full returned `plan` object back unchanged:

```json
{"contract":"krail.management.v1","request_id":"3","operation":"apply_init","idempotency_key":"opensaddle-init-123","expected_workspace_version":"1:<hex>","expected_workspace_digest":"sha256:<hex>","plan":{"contract":"krail.management.v1","plan_version":"1.0","operation":"init","workspace_version":"1:<hex>","workspace_digest":"sha256:<hex>","options":{"name":"Example","default_branch":"main","mode":"markdown_graph","knowledge_mode":"project"},"effects":[],"effect_count":0,"total_write_bytes":0,"plan_digest":"sha256:<hex>"}}
```

The example abbreviates `effects`; callers must return the full array supplied
by KRAIL. Apply rebuilds the preview in staging, compares every effect and
digest, then atomically installs it. It refuses a non-empty target, modified
plan, stale state, symlink effects, more than 512 effects, or more than 4 MiB of
initialized file content.
Reindex is limited to five configured output paths and 16 MiB of rendered
output, checked before any graph artifact is written.

## Versions, receipts, and errors

Workspace versions are content-derived (`1:<sha256 hex>`); the companion digest
is `sha256:<hex>`. Git internals and the management receipt directory are
excluded. Mutating requests check both values and stale requests return
`STALE_WORKSPACE` with expected and actual values.

`apply_init` and `reindex` require idempotency keys. Receipts live under
`.krail/management/v1/receipts/`, with hashed filenames. Repeating the exact
request returns the stored result with `replayed: true`; reusing a key for a
different request returns `IDEMPOTENCY_CONFLICT`.
Receipt files are protocol bookkeeping outside the previewed domain effects and
are excluded from workspace versioning.

Success envelopes contain `ok: true` and `result`. Failures contain `ok: false`
and a stable `error` with `code`, `message`, and `details`. V1 codes include
`INVALID_JSON`, `INVALID_REQUEST`, `UNSUPPORTED_OPERATION`,
`REQUEST_TOO_LARGE`, `NOT_INITIALIZED`, `WORKSPACE_NOT_EMPTY`,
`STALE_WORKSPACE`, `PLAN_DIGEST_MISMATCH`, `PLAN_PRECONDITION_MISMATCH`,
`PLAN_DRIFT`, `IDEMPOTENCY_CONFLICT`, `CORRUPT_RECEIPT`, `UNBOUNDED_PATH`,
`UNBOUNDED_EFFECT`, `RESOURCE_LIMIT`, and `INTERNAL_ERROR`.

## Packaging compatibility

The `krail` distribution retains `rail` and `krail`, adds the internal
`krail-managed` process, and exposes `krail-admin` as the stable OpenSaddle
adapter.
The separate MCP package retains `rail-mcp` and adds the equivalent `krail-mcp`
alias. MCP remains a different protocol and is not the management transport.

Reviewed OpenSaddle run output uses the separate `krail.mutation.v1` boundary:

```bash
krail-mutate --contract krail.mutation.v1 exchange
```

Its only v1 operation is `capture_candidate`. It verifies the reviewed
candidate digest, expected workspace digest, byte and source-count limits, and
an idempotency key before calling KRAIL's typed capture operation. The result is
always a raw `topics/inbox/` capture; this contract cannot create stable topics
or assert that candidate knowledge is trusted.
