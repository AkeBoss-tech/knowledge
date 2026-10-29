"""K-REVIEWED-RUN-PROCEDURE-1: real Core HTTP review identity fences reuse."""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip("opensaddle.control_plane.api", reason="requires actual optional Core runtime")
from opensaddle.control_plane.auth import LocalBootstrapAuthenticator
from opensaddle.control_plane.commands import Command
from opensaddle.control_plane.local_project_bridge import bridge_registered_local_project
from opensaddle.recipes.invoice_reconciliation import reconcile_csv
from krail.provider.v1 import ResourceRef
from rail.authorized_context import HostedProcedureReviewAuthorizer, PROCEDURE_REVIEW_CAPABILITY_ID, PROCEDURE_REVIEW_CAPABILITY_VERSION
from rail.core_provenance import CoreProvenanceReceipt, CoreProvenanceRepository, CoreProvenanceService, ObservedProcedureCandidateService, ProcedureExplanationRequest, ProcedureExplanationService, ProcedureReviewService, create_core_provenance_receipt
from rail.hosted.access import AccessClaims, AccessContextAuthority
from rail.observed_run_artifact import ObservedRunArtifactRepository
from rail.opensaddle_reviewed_run_artifact import OpenSaddleReviewedRunArtifactSource

# Reuse the existing real-HTTP qualification fixture, not a second Core mock.
_spec = importlib.util.spec_from_file_location("reviewed_core_fixture", Path(__file__).parents[3] / "scripts/qualify_reviewed_source_reuse.py")
fixture_api = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fixture_api
_spec.loader.exec_module(fixture_api)


class LiveCore:
    def __init__(self, root):
        self.root, self.project, self.subject = root, "project-a", "fixture-owner"
        self.token = "local-fixture-human-token-not-a-user-credential"
        self.headers = {"authorization": "Bearer " + self.token}
        self.workspace = fixture_api.git_repository(root, "workspace", "policy.md", "Explicit selected invoice policy.\n")
        self.app, registry = fixture_api.compose(root, self.project, self.workspace)
        self.settings = self.app.state.control_plane_settings
        self.settings.authenticator = LocalBootstrapAuthenticator(subject=self.subject, token=self.token,
            roles=frozenset({"owner", "admin", "member", "requester", "approver", "auditor"}))
        self.store = self.app.state.run_store
        self.store.create_project(self.project, self.subject)
        source = bridge_registered_local_project(registry=registry, run_store=self.store,
            local_project_id=self.project, project_id=self.project, created_by=self.subject)["source"]
        self.reject_after_content = False
        @self.app.middleware("http")
        async def review_fault(request, call_next):
            response = await call_next(request)
            if self.reject_after_content and request.url.path.endswith("/content"):
                self.reject_after_content = False
                state = self.store.agent_result_review_state(self.run_id)
                self.store.record_agent_result_review(run_id=self.run_id, artifact_id=state["artifact_id"],
                    artifact_digest=state["artifact_digest"], decision="rejected", note="in-read fault injection",
                    reviewed_by=self.subject, idempotency_key="in-read-reject", expected_review_revision=state["review_revision"])
            return response
        self.client = fixture_api.LoopbackClient(self.app)
        self.origin = str(self.client.client.base_url).rstrip("/")
        prefix = f"/api/v2/projects/{self.project}/retained-evidence"
        self.call("POST", prefix + "/setup", 201, {"setup_id": "initial"})
        self.capture = self.call("POST", prefix + "/captures", 201,
            {"capture_id": "policy", "path": "policy.md", "commit": source["revision"]}).json()
        self.call("POST", prefix + "/captures/policy/review", 201,
            {"review_id": "policy-review", "expected_content_digest": self.capture["content_digest"]})
        definition = {"title": "Invoice reconciliation", "objective": "Reconcile selected CSV bytes",
            "instructions": "Use only selected authorized evidence; do not transfer money.",
            "source_id": source["source_id"], "harness": "codex-app-server", "grants": [],
            "memory_source_ids": [self.capture["source_id"]], "assumptions": ["Selected invoice policy applies."], "evidence": []}
        proposal = self.call("POST", f"/api/v2/projects/{self.project}/agent-proposals", 201, definition).json()
        self.document = ResourceRef.model_validate(proposal["memory_bindings"][self.capture["source_id"]]["resource_ref"])
        agent = self.call("POST", f"/api/v2/agent-proposals/{proposal['proposal_id']}/publish", 200,
            {"expected_digest": proposal["definition_digest"], "acknowledge_assumptions": True}).json()
        task = self.call("POST", f"/api/v2/agents/{agent['participant_id']}/tasks", 202,
            {"expected_participant_revision": 0, "task": "Reconcile the explicitly selected fixture CSVs.",
             "authorized_context_source_ids": [self.capture["source_id"]]}, extra={"idempotency-key": "invoice-task"}).json()
        self.run_id = task["run_id"]
        self.store.register_worker(worker_id="fixture-worker", organization_id="local", project_ids=frozenset({self.project}),
            runtime_kind="remote_worker", registered_by=self.subject)
        credential = self.store.issue_worker_credential("fixture-worker", issued_by=self.subject, pepper=fixture_api.PEPPER)
        now = datetime.now(timezone.utc)
        self.store.report_native_adapter_readiness("fixture-worker", project_id=self.project, payload={
            "adapter_id": "codex-app-server", "source_id": source["source_id"], "revision": source["revision"],
            "digest": source["snapshot_digest"], "executable_state": "installed", "executable_version": "fixture",
            "authentication_state": "authenticated", "account_mode": "fixture", "protocol_state": "compatible",
            "protocol_version": "app-server-v1", "workspace_state": "configured", "ready": True, "reason": None,
            "observed_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat()})
        worker = {"authorization": "Bearer " + credential["token"]}
        claim = fixture_api.checked(self.client, "POST", "/api/v2/workers/fixture-worker/claim", headers=worker).json()
        path = f"/api/v2/workers/fixture-worker/runs/{self.run_id}"
        fixture_api.checked(self.client, "POST", path + "/start", headers=worker, json={"lease_epoch": claim["lease_epoch"]})
        fixture_api.checked(self.client, "GET", path + "/authorized-context-packet", headers=worker, params={"lease_epoch": claim["lease_epoch"]})
        self.invoices = b"invoice_id,customer,amount,currency\nINV1,Alice,12.00,USD\n"
        self.bank = b"transaction_id,invoice_id,amount,currency\nTX1,INV1,12.00,USD\n"
        self.report = json.dumps(reconcile_csv(self.invoices, self.bank, "USD"), sort_keys=True).encode()
        self.artifact = fixture_api.checked(self.client, "POST", path + "/result", 201, headers=worker, json={
            "lease_epoch": claim["lease_epoch"], "idempotency_key": "invoice-result", "content_digest": sha256(self.report).hexdigest(),
            "content_base64": base64.b64encode(self.report).decode()}).json()
        fixture_api.checked(self.client, "POST", path + "/complete", headers=worker,
            json={"lease_epoch": claim["lease_epoch"], "succeeded": True})

    def call(self, method, path, status=200, body=None, extra=None):
        kwargs = {"headers": {**self.headers, **(extra or {})}}
        if body is not None: kwargs["json"] = body
        return fixture_api.checked(self.client, method, path, status, **kwargs)

    def review(self, decision="accepted"):
        state = self.call("GET", f"/api/v2/runs/{self.run_id}/agent-result/review").json()
        return self.call("POST", f"/api/v2/runs/{self.run_id}/agent-result/review", body={
            "artifact_id": state["artifact_id"], "expected_artifact_digest": state["artifact_digest"],
            "decision": decision, "note": "Explicit fixture judgment, not a real user's acceptance.",
            "idempotency_key": "review-" + str(state["review_revision"]),
            "expected_review_revision": state["review_revision"]}).json()

    def pin(self, state=None, **changes):
        state = state or self.call("GET", f"/api/v2/runs/{self.run_id}/agent-result/review").json()
        selectors = dict(run_id=self.run_id, observer_subject=self.subject,
            expected_review_id=state["review"]["review_id"], expected_review_revision=state["review_revision"],
            expected_artifact_id=state["artifact_id"], expected_artifact_digest=state["artifact_digest"],
            base_url=self.origin, bearer_token=self.token, authority="opensaddle://reviewed-fixture", project_id=self.project)
        selectors.update(changes)
        return OpenSaddleReviewedRunArtifactSource.pin(**selectors)

    def withdraw(self):
        self.call("POST", f"/api/v2/projects/{self.project}/retained-evidence/captures/policy/availability", body={
            "state": "withdrawn", "expected_revision": 0, "expected_content_digest": self.capture["content_digest"]})

    def configuration(self):
        descriptor = {"schema_version": "opensaddle.command.v1", "command_id": "local.invoice.reconcile", "version": 1,
            "title": "Reconcile selected invoices", "description": "Deterministic fixture recipe", "effect": "read",
            "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "required_actions": [],
            "available": {"available": True, "reason": None}}
        descriptor["descriptor_digest"] = sha256(json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.app.state.command_registry.register(Command(descriptor,
            lambda _store, _resource, _value: reconcile_csv(self.invoices, self.bank, "USD")))
        self.call("POST", f"/api/v2/projects/{self.project}/environment/changes", 201, {
            "expected_revision": 0, "definition": {"commands": [{k: descriptor[k] for k in ("command_id", "version", "descriptor_digest")}],
            "bindings": [], "services": [], "packages": []}, "reason": "Pin actual registered invoice recipe configuration"})
        self.provenance_path = f"/api/v2/projects/{self.project}/provenance-observation?command_id=local.invoice.reconcile&environment_revision=1"
        return CoreProvenanceReceipt.model_validate(self.call("GET", self.provenance_path).json()["observation"])

    def close(self): self.client.close()


@pytest.fixture
def core(tmp_path):
    root = tmp_path / "core"; root.mkdir()
    value = LiveCore(root)
    try: yield value
    finally: value.close()


class ExactInputs:
    """Test-owned read grant; real Core still gates every protected artifact read."""
    def __init__(self, core): self.core, self.denied = core, set()
    def authorize(self, ref, *, at=None):
        if ref.exact_key in self.denied: raise PermissionError("exact input grant revoked")
        if ref == self.core.document:
            self.core.call("GET", f"/api/v2/projects/{self.core.project}/retained-evidence/captures/policy/content")


def test_pin_requires_real_current_acceptance_and_exact_inspected_target(core):
    with pytest.raises(PermissionError):
        core.pin({"review": {"review_id": "review_missing"}, "review_revision": 1,
            "artifact_id": core.artifact["artifact_id"], "artifact_digest": core.artifact["content_digest"]})
    state = core.review()
    source = core.pin(state)
    access = ExactInputs(core)
    repository = ObservedRunArtifactRepository(str(core.root / "semantic.json"), tenant_id="personal", project_id=core.project)
    evidence = source.observe(repository, authorizer=access, at=datetime.now(timezone.utc), input_refs=(core.document,))
    assert evidence.evidence_state == "caller_observed_unverified"
    assert evidence.observation.provider_metadata_state == "not_attested"
    assert source.binding.review_ref in evidence.input_refs
    for changes in ({"expected_review_revision": 0}, {"expected_artifact_digest": "f" * 64},
                    {"expected_review_id": "review_wrong"}, {"project_id": "other"},
                    {"observer_subject": "other"}, {"bearer_token": "invalid-fixture-token"}):
        with pytest.raises((PermissionError, ValueError)): core.pin(state, **changes)
    core.review("rejected")
    with pytest.raises(PermissionError): source.identify(core.run_id)
    with pytest.raises(PermissionError): core.pin()


def test_replaced_review_and_in_read_rejection_fence_retained_evidence(core):
    source = core.pin(core.review())
    access = ExactInputs(core)
    repo = ObservedRunArtifactRepository(str(core.root / "semantic.json"), tenant_id="personal", project_id=core.project)
    evidence = source.observe(repo, authorizer=access, at=datetime.now(timezone.utc), input_refs=(core.document,))
    core.review("accepted")  # Even a new positive judgment is a different exact review.
    with pytest.raises(PermissionError): repo.read_exact(evidence.exact_ref(), source=source, authorizer=source.authorizer(access))
    current = core.pin()
    core.reject_after_content = True  # Fault injection through real Core middleware.
    with pytest.raises(PermissionError): current.observe(repo, authorizer=access, at=datetime.now(timezone.utc))


def test_identity_and_source_withdrawal_deny_after_repository_reopen(core):
    source = core.pin(core.review()); access = ExactInputs(core)
    path = str(core.root / "semantic.json")
    repo = ObservedRunArtifactRepository(path, tenant_id="personal", project_id=core.project)
    evidence = source.observe(repo, authorizer=access, at=datetime.now(timezone.utc), input_refs=(core.document,))
    reopened = ObservedRunArtifactRepository(path, tenant_id="personal", project_id=core.project)
    bound = OpenSaddleReviewedRunArtifactSource(binding=source.binding, base_url=core.origin,
        bearer_token=core.token, authority=source.authority, project_id=core.project)
    assert reopened.read_exact(evidence.exact_ref(), source=bound, authorizer=bound.authorizer(access)) == evidence
    original = core.settings.authenticator
    core.settings.authenticator = replace(original, subject="another-authenticated-user")
    with pytest.raises(PermissionError): bound.identify(core.run_id)
    core.settings.authenticator = original
    core.withdraw()
    with pytest.raises(PermissionError): reopened.read_exact(evidence.exact_ref(), source=bound, authorizer=bound.authorizer(access))


def signed_review(candidate, evidence, project, nonce):
    now = datetime.now(timezone.utc)
    reviewer = ResourceRef(authority="https://fixture-review.example", resource_type="reviewer", resource_id="fixture-human",
        version="1", digest="sha256:" + sha256(b"explicit fixture review identity").hexdigest())
    refs = tuple(dict.fromkeys((*candidate.package_refs, *candidate.command_refs, *candidate.environment_refs,
                              *candidate.dependency_refs, reviewer, evidence)))
    authority = AccessContextAuthority({"fixture": b"fixture-review-key-never-a-user-key"}, issuer=reviewer.authority,
        required_capability_id=PROCEDURE_REVIEW_CAPABILITY_ID)
    claims = AccessClaims(issuer=reviewer.authority, tenant_id="personal", project_id=project, subject=reviewer.resource_id,
        delegator=reviewer.resource_id, delegation_id="review/" + nonce, capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
        capability_version=PROCEDURE_REVIEW_CAPABILITY_VERSION, capability_digest="sha256:" + "a" * 64,
        actions=("procedure.review",), source_ids=tuple(dict.fromkeys(ref.resource_id for ref in refs)),
        classifications=("internal",), policy_digest="sha256:" + "b" * 64,
        issued_at=now - timedelta(seconds=1), not_before=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5), nonce=nonce)
    action = HostedProcedureReviewAuthorizer(authority, authority.issue(claims, key_id="fixture"), tenant_id="personal",
        project_id=project, exact_refs=refs, allowed_candidate_digests=(candidate.record_digest,), capability_digest="sha256:" + "a" * 64)
    return reviewer, action


def test_real_core_configuration_and_accepted_result_seed_only_desired_successor(core):
    receipt = core.configuration()
    source = core.pin(core.review()); access = ExactInputs(core)
    path = str(core.root / "semantic.json")
    repository = CoreProvenanceRepository(path, tenant_id="personal", project_id=core.project,
        observed_source=source, observed_authorizer=source.authorizer(access))
    forged = create_core_provenance_receipt(**{
        **receipt.model_dump(mode="python", exclude={"receipt_digest"}), "receipt_id": "receipt:caller-forged"})
    with pytest.raises(PermissionError): source.verify(forged)
    initial = CoreProvenanceService(repository=repository).ingest(receipt, authorizer=access, trust=source).record
    assert receipt.execution_verified is False and receipt.activation_observed is False
    report = json.loads(core.report)
    assert report["totals"]["invoiced"] == report["totals"]["bank_received"] == "12.00"
    assert report["totals"]["net_unsettled"] == "0.00" and report["financial_actions_performed"] is False
    verification = ResourceRef(authority="krail://fixture-verification", resource_type="test",
        resource_id="invoice-exact-totals", version="1", digest="sha256:" + sha256(core.report).hexdigest())
    reviewer, action = signed_review(initial, verification, core.project, "configuration")
    parent = ProcedureReviewService(repository=repository).review(initial.record_digest, decision_id="configuration-review",
        reviewer_ref=reviewer, evidence_refs=(verification,), accepted=True, authorizer=access, review_authorizer=action)
    assert parent.promoted_record.lifecycle == "reviewed"
    observations = ObservedRunArtifactRepository(path, tenant_id="personal", project_id=core.project)
    evidence = source.observe(observations, authorizer=access, at=datetime.now(timezone.utc), input_refs=(core.document,))
    candidate = ObservedProcedureCandidateService(repository=repository).propose(parent_decision_id="configuration-review",
        evidence_ref=evidence.exact_ref(), procedure_version="invoice-observed-1", rationale="Exact accepted fixture result suggests a reusable procedure; separate review required.")
    assert candidate.lifecycle == "desired" and candidate.activation_ref is None
    assert candidate.review_ref is None and candidate.test_evidence_refs == ()
    assert source.binding.review_ref in candidate.dependency_refs
    request = ProcedureExplanationRequest(candidate_digest=candidate.record_digest)
    assert ProcedureExplanationService(repository=repository).actionable_guidance(request, authorizer=source.authorizer(access)) is None
    reviewer, action = signed_review(candidate, verification, core.project, "observed")
    reviewed = ProcedureReviewService(repository=repository).review(candidate.record_digest, decision_id="observed-review",
        reviewer_ref=reviewer, evidence_refs=(verification,), accepted=True,
        authorizer=source.authorizer(access), review_authorizer=action)
    assert reviewed.promoted_record.activation_ref is None
    reopened = CoreProvenanceRepository(path, tenant_id="personal", project_id=core.project,
        observed_source=source, observed_authorizer=source.authorizer(access))
    assert ProcedureExplanationService(repository=reopened).actionable_guidance(request, authorizer=source.authorizer(access)) is not None
    core.withdraw()
    with pytest.raises(PermissionError): ProcedureExplanationService(repository=reopened).actionable_guidance(request, authorizer=source.authorizer(access))
