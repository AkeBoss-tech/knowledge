# Knowledge #22 design evidence audit

This maps the original issue #22 design deliverables and acceptance criteria to
the proposed artifacts. It does not claim a provider runtime, API, MCP adapter,
or asset service has been implemented.

| Issue requirement | Evidence | Audit |
| --- | --- | --- |
| Decide provider v2 versus opt-in contracts | [rich-domain proposal](../provider-rich-domain-read-v1.md) selects separate `krail.rich-domain-read.v1` and defers provider v2 | Met as a design decision. |
| Immutable asset reference without provider JSON bytes | [asset-read proposal](../provider-asset-read-v1.md), `ExactRef` schemas | Met. Provider v1 remains unchanged; bounded base64 is only in the separate asset-read contract. |
| Bounded asset fetch semantics | `krail.asset-read.v1` range max 65,536; semantic conformance validates base64, byte count, range, chunk hash, complete hash, and zero-byte behavior | Met as a wire design and executable fixtures. Transport/source proof is explicitly future runtime work. |
| Valid-time versus recorded-time | rich temporal query requires `valid_at` and `known_at`; returned records include `valid_from` and `recorded_at`; company Tuesday/Thursday fixtures | Met as wire design. |
| Spatial frame/revision/units/bounds | rich spatial query schema requires `frame_id`, `map_revision`, `metres`, and 3D bounds; abstention response is explicit | Met as wire design. |
| Capability negotiation | optional descriptor digest pin, bounded diagnostics, [pure decision table](../contracts/krail.rich-domain-read.v1/fixtures/negotiation-decision-table.json), and `conformance.py` | Met as a deterministic design evaluator; not a provider registration/runtime claim. |
| Authorization and lineage | signed-context exact ref plus authorization digest in requests; successful result snapshot and exact lineage; errors hide those fields | Met as wire design. Live signature/revocation enforcement remains owned by existing authority adapters. |
| Read-only versus mutation/subscription | both proposals limit operations to reads; docs explicitly defer ingest/write/subscription | Met as explicit boundary. |
| Architecture/design doc | `docs/provider-rich-domain-read-v1.md` and `docs/provider-asset-read-v1.md` | Met. |
| Wire model and JSON fixtures | two Draft 2020-12 schemas and valid/invalid/semantic fixture sets under `docs/contracts/` | Met. |
| Provider-v1 compatibility matrix | rich proposal compatibility table plus unchanged provider-v1 suite | Met for design. |
| Robotics and company examples | robotics image/spatial/abstention and company bitemporal ownership fixtures | Met. |

## Acceptance evidence

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Existing provider v1 tests unchanged/passing | `test_provider_v1.py` and `test_capability_publication.py` | 56 passed with the Python 3.13 project interpreter. |
| Image/mesh-like asset reference without text embedding | asset-read and rich asset fixtures use exact image refs; invalid fixture rejects embedded content | Met in design fixtures. |
| Tuesday-effective/Thursday-learned temporal example | company temporal request/response fixture | Met. |
| Spatial example cannot omit frame/revision semantics | invalid spatial fixture and schema | Met. |
| Deterministic bounded negotiation | pure decision-table evaluator, requested-operation limit, digest pin, fixed diagnostics | Met as conformance design, not runtime. |
| Authority/writer/rebuildability/retention/identifier rules documented | asset-read authority table; rich-domain authority section and page-binding rules | Met for proposed ref types. |

## Validation and current boundary

`/opt/homebrew/Caskroom/miniconda/base/bin/python` is a compatible Python 3.13
interpreter with the JSON Schema dependency used by existing schema tests. It
runs `packages/rail-py/tests/test_rich_domain_read_contract.py`. The temporary
project interpreter used earlier lacks `jsonschema`; no repository dependency
or lockfile was changed for local convenience.

The package's provider-v1 models, API/MCP adapters, signed authorization
authority, cursor signer, storage adapters, and transport are intentionally not
part of this design issue's completed evidence. They remain implementation work
only if later explicitly scoped. The #19 audit's live robotics, binary-store,
and indexing gaps remain unchanged. With the design properties reviewed here,
the next bounded implementation candidate is #20's rebuildable local
current-state/spatial projection, not provider production wiring.
