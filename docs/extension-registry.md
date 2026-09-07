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
