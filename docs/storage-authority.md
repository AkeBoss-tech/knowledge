# Storage Authority and Resource Identity

This document is the Phase 1 authority map for KRAIL. KRAIL is standalone and
local-first: a project repository is authoritative for durable project truth.
The optional API, MCP server, generated indexes, hydrated databases, caches,
and client integrations are projections or adapters, never a second authority.

## Authority rules

1. A record has exactly one canonical authority and one canonical writer family.
   Adapters may invoke that writer; they may not create a parallel durable form.
2. Git-tracked project files are identified at an exact Git revision and content
   digest when crossing the provider boundary. Dirty working-tree content uses an
   implementation-defined immutable revision token plus the digest; the token may
   not be the mutable word `latest`.
3. Generated data is authoritative only when the table below says so. Otherwise it
   must be reproducible from authoritative inputs or treated as an expendable cache.
4. `.krail/store.json` belongs to the optional API runtime. It is not project truth,
   must not be required by local SDK consumers, and cannot override repo records.
5. A consumer, including OpenSaddle, receives only `krail.provider.v1` values. It
   does not read KRAIL paths, DuckDB/SQLite/OWL stores, or runtime objects directly.

## Durable record inventory

“Rebuildable” means the record can be deleted and deterministically regenerated
from the listed authority without losing project decisions or knowledge.

| Record type / path | Canonical authority | Canonical writer | Rebuildable | Stable identifier |
| --- | --- | --- | --- | --- |
| Project manifest (`rail.yaml`) | project Git repo | project author/bootstrap and manifest tooling | no | project slug + repo-relative path |
| Ontology/source/pipeline configuration (`.ontology/**/*.yaml`) | project Git repo | project author/bootstrap | no | project slug + repo-relative path |
| Durable topic pages (`topics/**/*.md`, excluding inbox) | project Git repo | `KnowledgeRuntime.topic_upsert` / inbox promotion | no | normalized topic slug or repo-relative path |
| Raw captures (`topics/inbox/*.md`) | project Git repo | `KnowledgeRuntime.capture` | no; disposable only by explicit triage | capture filename / repo-relative path |
| Source declarations and dependency metadata (`sources/**`) | project Git repo | source dependency and project authoring tools | no | source key or repo-relative path |
| Specs, agent roles, skills (`specs/**`, `agents/**`, `skills/**`) | project Git repo | project authoring tools | no | declared slug or repo-relative path |
| Tasks, workflows, decisions, approvals, work orders, schedules (`research_plan/**`) | project Git repo | `KnowledgeRuntime` workflow/planning operations | no | declared ID/slug; path is fallback |
| Integrity records (`research_plan/state/*.json`) | project Git repo | `ResearchIntegrityRepo` | no | typed record key defined by `rail.integrity` |
| Access and dispatch audit records (`research_plan/audit/**`, `research_plan/dispatch_log/**`) | project Git repo | permissions and dispatch services | no | record ID, otherwise append position + digest |
| Workflow and agent run/session records (`research_plan/runs/**`, `research_plan/sessions/**`) | project Git repo | `KnowledgeRuntime` runners/workflows | no for observations; derived summaries are rebuildable | run/session ID + artifact name |
| Published or promoted artifacts (`artifacts/**`) | project Git repo | producing workflow, then explicit verification/promotion | no once promoted | artifact ID or repo-relative path |
| Hydrated ontology/relational outputs (`.ontology/onto.db`, `onto.duckdb`, populated OWL, entity-resolution store) | authoritative configs and upstream source versions | `packages/engine` hydration pipeline via local SDK | yes when inputs remain available | pipeline + input revision set + content digest |
| Markdown graph, wiki, search and vector indexes | repo topics/plans plus active configuration | `KnowledgeRuntime` graph/site/index builders | yes | build configuration + source revision + digest |
| Active pack and local caches (`.krail/**`) | referenced repo records and local operator selection | local SDK/CLI | yes, except the operator’s current selection is local state | project + local state name |
| Optional API store (`.krail/store.json`) | optional API process only | `packages/api` `LocalStore` | not necessarily; operational compatibility state only | collection + record ID |
| Temporary engine/API files (`/tmp/rail_*`, download caches) | their repo/upstream inputs | API or engine adapter | yes | none promised |

If a record does not fit this inventory, its writer must declare authority,
rebuildability, and identifier before the record becomes a supported durable type.

## `ResourceRef` identity

The executable definition lives in `krail.provider.v1.ResourceRef`.

- `authority` is an absolute URI without query or fragment, for example
  `git+file:///workspace/my-project`. It names the system entitled to assign IDs.
- `resource_type` is a lower-case, URI-safe type token.
- `resource_id` is an opaque ID assigned by that authority. Consumers must not
  parse it as a KRAIL filesystem path even when a local provider uses a path.
- Identity is the tuple `(authority, resource_type, resource_id)`.
- `version` is a non-empty, immutable authority revision. It selects exact source
  state; it is not a freshness label.
- `digest` is `sha256:` followed by 64 lowercase hexadecimal characters and
  verifies the returned canonical bytes.
- Exact reference equality uses identity plus `version` and `digest`. The same
  identity/version with a different digest is an integrity failure, not a new ID.

## Legacy capability disposition

These are migration decisions, not instructions to rewrite the packages in Phase 1.

### `packages/api`

| Disposition | Capabilities | Decision |
| --- | --- | --- |
| Retain adapter | FastAPI boot/routing, request authentication/configuration, bounded HTTP translation to SDK/provider operations, local health | Keep optional and thin; it may expose the public contract but owns no project truth. |
| Migrate | ontology reads, retrieval, integrity, artifact resolution, repo contract checks that duplicate local SDK behavior | Move reusable domain logic into local core/SDK incrementally; leave compatibility shims in API. |
| Archive | planner/autopilot, agent orchestration, scheduler, command-center/dashboard, role runtime, hosted-style device/liveness control | Exclude from the standalone foundation and preserve only for legacy compatibility until separately retired. |
| Remove | arbitrary SQL and code-execution endpoints, S3-as-authority paths, duplicate project mutation/storage abstractions | Remove from the future product surface after callers migrate; do not carry them into provider v1. |

### `packages/engine`

| Disposition | Capabilities | Decision |
| --- | --- | --- |
| Retain adapter | YAML-driven fetching, hydration pipeline entrypoint, format handlers, deterministic artifact emission | Keep as a local implementation behind SDK calls. None is part of provider v1. |
| Migrate | ontology building, entity resolution, lineage emission, reusable transforms | Move stable implementation logic toward a cohesive local core as later slices require it. |
| Archive | Streamlit app, benchmark/demo scripts, economics-specific configs and sample data | Keep out of the foundation contract; relocate to examples or history in a later cleanup. |
| Remove | generic subprocess/code execution, LLM analysis hooks, universal SQL-mirror aspirations | Do not expose as core KRAIL capabilities; delete only in a separately verified cleanup. |

No disposition introduces enterprise execution, autonomous planning, arbitrary
SQL/SPARQL, a graph database, hosted Postgres, or a universal event ledger.
