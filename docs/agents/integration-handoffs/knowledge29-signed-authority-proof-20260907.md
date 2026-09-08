# Knowledge issue 29: signed connected-Git authority proof

Date: 2026-09-07
Repository: `AkeBoss-tech/knowledge`
Scope: `packages/rail-py/rail/shared_knowledge.py` and its public shared-Git tests

## Composition

`SignedSharedKnowledgeActionAuthorizer` is a verifier boundary. The embedding
service owns identity, grant issuance, resolver freshness, keys, and
revocation. The resolver returns a pair:

```python
context, request_binding = context_for(subject_id, action, resource_ref)
```

The context is verified by `AccessContextAuthority.verify`. The request binding
is verified by `verify_packet_request_binding`, including its digest binding to
the context. The caller composes its request digest over the complete action
and `ResourceRef` model:

```python
request_digest = shared_knowledge_action_request_digest(action, resource_ref)
```

That digest covers authority, resource type, resource ID, immutable version,
and content digest. The binding must also carry:

```text
purpose = shared-knowledge-action
scope = resource_ref.authority
```

The adapter then checks the configured tenant, project, capability ID,
capability version, capability digest, subject, action, exact source scope,
binding fields, current time, and revocation state. Wildcard source scope,
missing or malformed resolver output, and every mismatch fail closed as
`source grant is revoked or absent`.

## Public journey evidence

`test_signed_authority_two_user_conflict_promotion_and_other_conflict` uses two
real clones and two `SharedKnowledgeWorkspace` instances. Alice and Bob stage
against the same canonical commit. Alice's signed proposal is promoted by a
signed reviewer; Bob's competing proposal becomes `conflict`. The resulting
canonical bytes are read and searched through the same signed adapter.

`test_signed_authority_public_journey_rechecks_revoked_pending_review_after_restart`
fault-injects after the durable `review-pending` receipt, revokes the reviewer's
current signed context, and restarts the workspace. Restart cannot promote the
pending proposal. A newly issued reviewer context is required to continue. The
test then removes Alice's resolver grant after cached reads exist and verifies
that export is denied.

The direct adapter tests cover stale authority, resource type, resource ID,
version, digest, action, subject, tenant, project, capability version,
purpose, scope, wildcard source IDs, expiry, revocation, missing resolver, and
malformed resolver output. Altered contexts and bindings are re-signed where
needed, so each negative assertion reaches the intended fence.

## Regression evidence

Before the binding repair, root reproduced that a signed read grant with
`source_id=knowledge.md` was accepted for both the expected resource and a
different authority/version/digest sharing that ID. The corrected tests bind
the entire request digest and reject each mutation. The signed shared-Git plus
hosted authorization suites pass together with 57 tests.

Root then ran the full retained command:

```text
PYTHONUSERBASE=/Users/akashdubey/.codex/agent-runtime/opensaddle-20260907/knowledge-python-userbase \
PYTHONPATH=packages/rail-py:/Users/akashdubey/.codex/agent-runtime/opensaddle-20260907/python-overlay \
/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest
```

Result: `928 passed` in `75.34s`, exit code 0.

## Limits

This proves the caller-composed local/connected-Git boundary and deterministic
HMAC authority behavior. It does not prove a hosted control-plane deployment,
remote key service, production revocation transport, or hosted two-user
integration. Backup retention and canonical Git/external clone history retain
their existing explicit-owner and historical-content semantics; this change
does not claim erasure from those systems.
