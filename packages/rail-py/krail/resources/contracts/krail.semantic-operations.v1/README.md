# krail.semantic-operations.v1

This package-data contract publishes strict Python-model JSON requests and
results for the fixed, read-only semantic operations described by the
`krail.semantic-operations` capability descriptor. The descriptor is the schema
authority; `fixtures/software-change.json` is the deterministic repository →
issue → change → pull request → CI request set used for client conformance.

Cursors are provider-issued opaque values and therefore are intentionally not
embedded in static fixtures. No operation accepts a graph query string, mutation,
publish command, agent command, or external effect.
