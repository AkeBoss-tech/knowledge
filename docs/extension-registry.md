# Domain extension registry v1

`rail.extension_registry.DomainExtensionRegistry` is the minimum #21
discovery/dispatch substrate. Trusted local callers register an immutable
descriptor and already-imported in-process handlers. Registration rejects
duplicate extension/operator versions, payload-schema ownership collisions,
undeclared handlers, and non-callable handlers.

Dispatch requires an exact operator ID/version, exact input `ResourceRef`s, a
caller-supplied authorizer, and JSON-safe configuration. The authorizer is
checked for every input before the handler runs and again before exposing the
result, so revocation during a trusted handler fails closed. Results retain
exact input refs, operator descriptor/version, output schema, configuration
and output digests, and a deterministic lineage digest.
`verify_invocation_integrity` binds the result to the descriptor and detects
mutation of the shallow-frozen output mapping. The registry never
imports, evaluates, installs, sandboxes, or activates third-party code;
installation and runtime authority remain outside KRAIL.

## Declared rebuildable projection storage

`ProjectionStorageBinding` is an additive sidecar to the v1 extension
descriptor; it does not change existing extension or operator digests. A
binding names its owning extension/version and declared payload schema, the
exact canonical temporal authority, payload schema/version and writer family,
and the rebuildable projection writer. The registry rejects duplicate
projection IDs and conflicting writers for one canonical resource namespace.
Only already-imported trusted-local builders may register. The binding does
not authorize canonical writes or mint source access.

The robotics `SpatialCurrentProjection` is the first real consumer. Preparing
it verifies a bounded set of immutable canonical `TemporalRecord` inputs and
authorizes every exact source ref before and after the build. Region candidate
reads compare the current canonical input set and time-cutoff configuration to
that preparation, then reauthorize and revalidate both inputs and configuration
after the grid query. A missing,
changed, or revoked input refuses the derived read; `TabletopWorldMemory`
resolves the region from current canonical records with its existing per-record
authorization instead. New canonical writes invalidate the prepared grid until
explicit rebuild. If a second writer advances the canonical scope cursor
during a region read, that read abstains instead of publishing a candidate set
that may have omitted the new record; a fresh read can use the updated
canonical history. This is a bounded snapshot check, not a cross-process
linearizable transaction.

This governed path trades throughput for clear authority: it verifies and
authorizes at most 10,000 exact canonical inputs on each derived read and
invalidates the grid on same-process ingest instead of incrementally updating
it. It should not be presented as constant-cost or incrementally maintained
region search. The declared projection writer family is trusted local
metadata, not a cryptographic attestation of the callback. The disposable
grid is never a source of authority or a complete-history claim outside the
trusted local canonical reader. This does
not introduce a general storage plugin, sandbox, model-run artifact, or hosted
deployment adapter.

Robotics and company validators use the same registry interface in the focused
fixtures. Payload schemas are declared and discoverable, but schema-specific
validation, durable storage adapters, and sandboxed third-party extension
installation remain later work.

## Local company incident-response consumer

`rail.company_incident_response` publishes the trusted-local
`company.incident-response.guidance` operator at `1.0.0`. It consumes the
versioned `krail.procedure-explanation-request.v1` payload and returns the
published `krail.procedure-actionable-guidance-result.v1` shape. Registration
injects the existing `ProcedureExplanationService` and a caller-owned live
exact-ref authorizer; the extension does not mint authority or activate the
reviewed procedure. Registration accepts only the established
`HostedAccessContextAuthorizer`. On every invocation and immediately before
return, its trusted authority verifies the signature, time and revocation, and
binds the claims to the procedure service tenant/project plus the exact
`company.incident-response.guidance` operator ID, `1.0.0` version, and operator
descriptor digest.

```python
registry = DomainExtensionRegistry()
register_company_incident_response_extension(registry, procedure_service, reader)

result = registry.dispatch(
    "company.incident-response.guidance",
    "1.0.0",
    ((incident_evidence_ref, request.model_dump(mode="json")),),
    config={},
    authorizer=reader,
)
```

The invocation lineage includes the input incident evidence plus every exact
candidate, reviewed procedure, evidence, reviewer, review-decision,
invalidation, and current projection reference consumed by the operator.
Denial of any exact ref fails before output exposure. A stale procedure returns
the explicit actionable-guidance abstention shape with no guidance payload;
historical bytes remain available only through separately authorized
explanation reads. The handler refreshes its explanation after the actionable
decision, so an invalidation landing during that read becomes the exact
abstention lineage instead of inheriting the earlier snapshot.
