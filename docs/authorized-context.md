# Authorized Context Packet v1

`rail.authorized_context.assemble_authorized_context` is the bounded read path
for callers that need a Context Brief under a live authorization decision. It
reuses `krail.provider.v1` exact `ResourceRef` values and the existing KRAIL
signed access authority; it does not own identity, grants, credentials,
scheduling, or OpenSaddle operational records.

The injected authorizer is checked before candidate shaping, before every exact
resource read, and immediately before returning. Search hits that fail the
decision are omitted without exposing their identity or count. The hosted
adapter requires the `context.read` action, an allowed source ID, and an exact
version-plus-digest grant. The authority is verified on every check, so context
or delegation revocation fails closed even after an earlier check succeeded.

The returned `AuthorizedContextPacket` binds the existing `ContextBrief` to the
authorizer's `authorization_digest` with `packet_digest`. OpenSaddle may carry
that packet and its KRAIL `ResourceRef` values alongside Project, Run, event,
artifact, and approval references; those operational records remain
OpenSaddle-owned. Packet construction and deserialization recompute and verify
the digest, so forged packet contents fail closed.
