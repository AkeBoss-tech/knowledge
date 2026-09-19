# Company guidance packet v1 handoff

Knowledge publishes the separately negotiated `krail.company-guidance-packet`
capability at semantic version `1.0.0`, schema
`krail.company-guidance-packet.v1`. The current descriptor digest is
`sha256:b03f572cab08637d018722a09441b8ac7d0def0a94ad6be7bb27081d879255b6`.
It exposes `create_company_guidance_packet` and
`read_company_guidance_packet`, with a 524,288 UTF-8 byte packet bound and 256
exact lineage refs. It does not mutate ContextBrief v1 or claim environment
activation.

The packet contains the original signed authorization digest, request and
descriptor digests, purpose/scope, typed `CompanyOperationalGuidance`, and the
complete exact lineage: current owner/policy rows, reviewed candidate and
promotion, command/environment refs, test evidence, review decisions and
reviewer refs, dependencies, and invalidation refs. The packet digest is a
canonical content digest and is deterministic for the same accepted read.

Creation and reads use the existing `AccessContextAuthority` and signed packet
request binding. A fresh read must present a current grant and exact refs; the
read result carries a separate reauthorization timestamp/digest while keeping
the packet's original authorization digest unchanged. Missing, expired, stale,
revoked, scope-mismatched, or incomplete grants return the coarse
`packet_unavailable` result without packet metadata.

Packets use the existing `.krail/authorized-context-packets` cache and shared
tombstone journal. `invalidate_for_sources` removes only matching company
packet files and journals durable tombstones; a restart cannot resurrect one.
The public focused journey proves signed creation, deterministic digest,
persisted local test-result bytes, fresh-reader reauthorization, disk restart,
and exact source invalidation.

## Core and Interface handoff

Core should negotiate this capability by ID and descriptor digest before using
the provider operations. Its adapter should pass the exact source refs and
signed request binding supplied by the caller, retain the packet digest and
original authorization digest on the Run handle, and clear the handle on
`packet_unavailable` or failed reauthorization. It must keep this packet route
separate from the existing ContextBrief/authorized-context packet v2 route.

Interface should render the negotiated packet schema through a capability
specific Inspector model, show only the typed guidance and exact lineage the
packet discloses, and clear the view on coarse unavailability. It should not
reinterpret this packet as ContextBrief v2 or infer activation state from the
guidance text. Both adapters must preserve the original packet digest across
fresh grants and record the separate reauthorization receipt.

## Validation boundary

Focused validation: `43 passed` across the company guidance, packet, capability
publication, and existing authorized-context suites. `compileall` also passes.
The packet test is a local public-boundary fixture; it proves provider-side
durability and authority behavior, not hosted Core/Interface deployment.
