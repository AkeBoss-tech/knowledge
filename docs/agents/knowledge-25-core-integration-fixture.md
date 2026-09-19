# Knowledge 25 real-provider integration fixture

Status: Knowledge-side local integration bootstrap implemented. This does not
complete issue 25 until Core Run and Interface Inspector consume the same
immutable packet in their own executable paths.

## Composition contract

Core can import `bootstrap_authorized_context_integration` from
`rail.authorized_context_integration`. The caller supplies its
`AccessContextAuthority`, revocation control, tenant ID, project ID, signing key
ID, and live clock. KRAIL does not create a second identity store or infer
project membership.

The returned fixture exposes the real `Project` and `Project.provider`.
Consumers create and read packets only through:

```python
packet = fixture.provider.create_authorized_context_packet(
    grant.create_request
)
result = fixture.provider.read_authorized_context_packet(
    fixture.read_request(packet, fresh_grant)
)
```

`alice_refs` is the explicit wide set of repository, issue, and private source.
`bob_refs` is the narrow repository and issue set. `source_bindings` maps every
exact provider `ResourceRef` key to a caller-owned Core source ID plus tenant,
project, immutable version, digest, and classification. Core must evaluate
these source-specific bindings; an owner or member role alone is not the source
grant.

The fixture `grant` and `reauthorize_packet` methods are deterministic issuer
helpers for local acceptance only. Core supplies the classifications and policy
digest from its live source decisions. Production integration must use actual
Core create-source receipts in `core_source_ids`; the built-in
`core-source/...` values are disposable demo placeholders. A fresh read uses
`reauthorize_packet`, which binds the new authority context to the original
immutable `packet.request_digest` even when the live clock has advanced.

`set_current_head` accepts the caller/provider freshness decision for one
source identity. It does not delete or rewrite the retained exact source, so
`read_retained_source` can still prove historical provenance while packet read
returns the coarse unavailable union. `revoke_grant` delegates to the supplied
revocation control and exists for deterministic acceptance runs.

This is an in-process local provider composition. It does not claim a KRAIL
HTTP service. A later process transport can carry the same published packet
schemas and provider operations without changing this authorization boundary.

## Behavioral evidence

`test_authorized_context_integration.py` covers:

- Alice-wide and Bob-narrow grants against one real provider and one purpose;
- exact Core source bindings for every packet source;
- omission of the private source ID and content from Bob's packet;
- a retained exact source that remains provider-readable after current head
  advances while the cached packet becomes unavailable;
- Bob's fresh wide reauthorization of Alice's immutable packet; and
- caller-controlled revocation returning only the coarse unavailable shape.

The fixture tests and the owning packet service suite pass together: 21 tests.
