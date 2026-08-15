# krail.semantic-operations.v1

This package-data contract publishes strict Python-model JSON requests and
results for the fixed, read-only semantic operations described by the
`krail.semantic-operations` capability descriptor. The descriptor is the schema
authority; `fixtures/software-change.json` is the deterministic repository →
issue → change → pull request → CI request set used for client conformance.

Cursors are provider-issued opaque values and therefore are intentionally not
embedded in static fixtures. No operation accepts a graph query string, mutation,
publish command, agent command, or external effect.

Every read is shaped by exact versioned source grants and classification, not
authority alone. Cursors bind only visible state. Returned entities, aliases,
facts, ontology versions, and packs retain exact provenance and reviewed type-pack
lineage; hidden references are omitted whole. Serialized byte and elapsed-time
ceilings apply to the complete response and produce typed truncation gaps.
