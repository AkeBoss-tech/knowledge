# Company operational guidance proof

This checkpoint implements a bounded public read journey across the existing
company temporal projection and reviewed procedure authority.

## Proven journey

1. A Core provenance receipt records exact command and environment refs.
2. `ProcedureReviewService` promotes the candidate with exact ownership, policy,
   reviewer, and persisted local `test-result` evidence refs.
3. `CompanyOperationalGuidanceService` resolves current service ownership and
   effective policy through signed readers, then returns actionable reviewed
   guidance only when the stored procedure evidence contains the exact current
   ownership and policy record refs.
4. The company projection and procedure repository are both reopened from disk;
   the output and lineage remain identical.
5. Service/policy invalidation, procedure evidence invalidation, signed reader
   revocation, cross-service substitution, repository scope mismatch, subject
   refresh, and coordinated mid-read context replacement all abstain or deny
   without disclosing stale guidance.

## Evidence

The focused boundary suite reports **21 passed** across
`test_domain_extension_initial_proof.py` and
`test_actionable_guidance_dispatch.py`. `compileall` and `git diff --check`
also pass. The company guidance tests cover the signed current readers, exact
review binding, persisted local test-result digest, fresh restart of both
stores, affected source/evidence invalidation, and live authority races.

The full rail-py suite was previously recorded at 943 passed before this
narrow company-guidance change; this checkpoint reports the focused boundary
result separately.

## Boundary

Knowledge supplies evidence, reviewed procedures, current company reads, and
permission-aware explanations. It does not activate environment revisions,
own grants, execute commands, or claim completion of the entire #15 roadmap.
OpenSaddle remains authoritative for activation and operational state.
