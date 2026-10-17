# KRAIL documentation map

Start with [the vision](vision.md): an adaptable and extensible knowledge and
data backbone. Akash's explicit project-chat statements and newer corrections
outrank generated plans. Current code, contracts, and scoped evidence establish
supported behavior; the vision establishes direction.

The [root README](../README.md#release-and-support-status) records release lanes.
Do not infer that a source-only extension ships in a published wheel. This
cleanup did not independently reverify package publication or hosted operation.

## Find the owning reference

| Area | References | Interpretation |
| --- | --- | --- |
| Architecture and storage | [Architecture](architecture.md), [storage authority](storage-authority.md), [hosted composition](hosted-storage.md) | Preserve declared writers and deployment boundaries while allowing deliberate evolution. |
| Structured data | [Dataset catalog](dataset-catalog.md), [project layout](project-layout.md) | Data has source, schema, version, and cache semantics; not everything is a Markdown memory. |
| Extensibility | [Extension registry](extension-registry.md), [knowledge modes](knowledge-modes.md), [capability publication](capability-publication.md) | Different domains and operators share explicit contracts; current trusted-local limits remain scoped. |
| Semantics and time | [Semantic foundation](semantic-foundation.md), [semantic operations](semantic-operations.md), [temporal records](temporal-records.md) | Existing domain slices are foundations, not a closed set of possible domains. |
| Evidence and learning | [Knowledge operations](knowledge-operations.md), [verification/outcomes](verification-and-outcome-evidence.md), [procedural memory](procedural-memory.md), [Core provenance](core-provenance.md) | Keep candidates, observed results, review, and supported knowledge distinguishable. |
| Consumer interfaces | [Provider v1](provider-v1.md), [authorized context](authorized-context.md), [context briefs](context-briefs.md), [API](api-runtime.md) | KRAIL can serve different consumers; neither an API nor a UI becomes the sole knowledge authority. |
| Additional provider surfaces | [Rich domain reads](provider-rich-domain-read-v1.md), [asset reads](provider-asset-read-v1.md), [observed artifacts](observed-run-artifact.md) | Check each document's proposed/implemented scope and negotiation requirements. |
| Ingestion and operation | [Ingestion](ingestion-os.md), [listeners](listeners.md), [management v1](management-v1.md), [retention](shared-knowledge-retention.md) | Existing knowledge workflows remain supported; do not assume a universal execution engine. |
| Getting started | [Examples](../examples/README.md), [use cases](use-cases.md), [local onboarding](local-project-onboarding.md), [demo](demo-script.md) | Examples demonstrate bounded workflows; their repeated prompts and fixtures are intentional resources. |
| Integrations | [Integration index](integrations/README.md) | CLI, SDK, MCP and client guides. |
| Verification and releases | [Invariants](testing/invariants.md), [release checklist](../RELEASE.md), [release notes](releases/), [migration](migrations/1.1.md) | Version-scoped evidence, not whole-product acceptance. |
| Repository hygiene | [Generated-project hygiene](generated-project-hygiene.md) | Source knowledge and reusable fixtures are not disposable just because generated. |
| Historical plans | [Archive manifest](archive/manifest.json) | Prior growth/launch plans, machine handoffs, and planner logs; not an active queue. |

## What changed in the October 17 cleanup

Inventoried 261 nonignored Markdown files and scanned authority/status cues.
Substantive review focused on direction, planning, and relevant architecture.
Archived nine old growth/launch, readiness, and handoff/planner documents. The
old launch-kit path remains as a short navigation pointer for the issue template.

Retained data/semantic/provider contracts, release records, examples, tests,
source knowledge, and implementation evidence. Repeated example prompts are
working project resources, not duplicate product plans. Dated agent proof reports
are historical evidence, not current assignments or deployment claims.

The [manifest](archive/manifest.json) records all original paths and hashes and
a recovery backup. Existing robotics-example work and other uncommitted changes
were excluded. This was not an exhaustive review of all historical chats or a
fresh runtime qualification. Update owning references instead of creating
another broad roadmap or generated product brief.
