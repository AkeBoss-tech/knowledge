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
