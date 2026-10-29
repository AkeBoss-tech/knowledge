"""K-GOAL-REVIEWED-RUN-PROCEDURE-1: actual PG/HTTP/Seatbelt Goal, exact review, reuse.

Synthetic invoices, Google certificate response and hardware readiness only are
fixtures. No model/provider/account or financial action is performed.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
pytest.importorskip("opensaddle.control_plane.api", reason="requires optional actual Core runtime")
import opensaddle

CORE_TESTS = Path(os.environ.get("OPENSADDLE_TEST_SOURCE_ROOT", Path(opensaddle.__file__).resolve().parents[2])) / "tests"
if not (CORE_TESTS / "test_work_placement.py").is_file():
    pytest.skip("requires Core source test harness; set OPENSADDLE_TEST_SOURCE_ROOT for installed modules", allow_module_level=True)
sys.path.insert(0, str(CORE_TESTS))
sys.path.insert(0, str(Path(__file__).parent))
from test_postgres_control_plane import isolated_capacity_postgres_url  # noqa: E402,F401
from test_work_placement import placement  # noqa: E402,F401
from test_project_goal_tick import isolated_goals, scheduler, PATH  # noqa: E402,F401
from test_noncoding_goal_journey import NeverStartModel, client_config, due, sandbox_goal  # noqa: E402
from test_project_goal_loop import root  # noqa: E402
from test_reconciliation_review import case, invoke  # noqa: E402
from opensaddle.control_plane.native_worker import OutboundCodexWorkerExecutor
from opensaddle.control_plane.work_session_worker import WorkSessionWorker
from opensaddle.control_plane.worker_client import OutboundWorkerClient
from opensaddle.control_plane.worker_loop import FencedWorkerLoop
from opensaddle.execution.local_sandbox import LocalSandboxProvider
from opensaddle.storage.runtime_store import RuntimeStore
from krail.provider.v1 import ResourceRef
from rail.observed_run_artifact import ObservedRunArtifactRepository
from rail.opensaddle_goal_run_artifact import OpenSaddleGoalReviewedRunArtifactSource
from rail.core_provenance import CoreProvenanceRepository, CoreProvenanceService, ObservedProcedureCandidateService, ProcedureExplanationRequest, ProcedureExplanationService, ProcedureReviewService, create_review_decision, _decision_ref, procedure_record_ref
from test_opensaddle_reviewed_run_artifact import fixture_api, LiveCore, signed_review

pytestmark = pytest.mark.skipif(not os.environ.get('OPENSADDLE_TEST_POSTGRES_URL') or not LocalSandboxProvider.available(),
    reason='requires owned disposable UTF8 PostgreSQL and macOS Seatbelt')


class Inputs:
    def __init__(self): self.refs = set()
    def grant(self, *refs): self.refs.update(ref.exact_key for ref in refs)
    def authorize(self, ref, *, at=None):
        if ref.exact_key not in self.refs:
            raise PermissionError('exact fixture delegation is absent: ' + ref.resource_type)


def assert_live_audit_response_faults_deny(source, monkeypatch):
    """Mutate responses only after actual authenticated PG HTTP reads."""
    import copy
    import httpx
    for fault in ('missing_pinned','sequence','snapshot','ordering','cursor','replaced_review'):
        original_send = httpx.Client.send
        def changed(client, request, **options):
            response = original_send(client,request,**options)
            if request.url.path.endswith('/audit') and response.status_code == 200:
                value = json.loads(response.read())
                first = value['items'][0]
                if fault == 'missing_pinned': value['items'] = []
                elif fault == 'sequence': first['sequence'] += 1
                elif fault == 'snapshot': first['snapshot_digest'] = '0'*64
                elif fault == 'ordering': value['items'].append(copy.deepcopy(first))
                elif fault == 'cursor': value['next_cursor'] = first['sequence'] - 1
                elif fault == 'replaced_review':
                    later = copy.deepcopy(first)
                    later['sequence'] += 1; later['revision'] += 1
                    later['payload']['runs'][-1]['result_review']['artifact_digest'] = '0'*64
                    value['items'].append(later)
                response.close()
                return httpx.Response(200,stream=httpx.ByteStream(json.dumps(value).encode()),request=request)
            return response
        with monkeypatch.context() as patch:
            patch.setattr(httpx.Client,'send',changed)
            with pytest.raises((PermissionError,ValueError)):
                source.identify(source.binding.run_id)


@pytest.mark.parametrize("configuration_deployment", ["separate-local", "same-hosted"])
def test_real_goal_verified_download_exact_human_review_and_withdrawal(placement, scheduler, tmp_path, monkeypatch, configuration_deployment):
    repo, api, call, project, source_metadata, workers, *_ = placement
    headers, _ = scheduler
    worker = workers[0]
    with OutboundWorkerClient(client_config(worker), transport=api._transport) as client:
        assert WorkSessionWorker(client, {'project_id':project}, worktree=tmp_path, state_dir=tmp_path/'state').attest()['accepted']
        state = call('POST', root(project), {**sandbox_goal(source_metadata, tmp_path),
            'expected_source_revision':source_metadata['revision'],
            'expected_source_digest':source_metadata['snapshot_digest']}, status=201)
        assert api.post(PATH, headers=headers).status_code == 200
        with RuntimeStore(tmp_path/'journal.db') as journal:
            execution = FencedWorkerLoop(client, OutboundCodexWorkerExecutor(client, NeverStartModel(), journal, workspace=tmp_path)).run_once()
        assert execution.status == 'completed'
    due(repo, state['agent_id'])
    assert api.post(PATH, headers=headers).status_code == 200
    state = api.app.state.project_goal_loop.read(state['agent_id'])
    page = call('GET', f'runs/{execution.run_id}/work-execution')
    artifact = page['artifacts'][-1]
    history_pairs = 40 if configuration_deployment == 'same-hosted' else 0
    for index in range(history_pairs):
        for action in ('pause','resume'):
            state = call('POST',root(project)+'/'+state['agent_id']+'/control', {
                'action':action,'expected_revision':state['revision'],
                'idempotency_key':f'history-{index}-{action}'})
    token = api.app.state.control_plane_settings.authenticator.verifier.token
    downloaded = api.get(f'/api/v2/runs/{execution.run_id}/artifacts/{artifact["artifact_id"]}/content', headers={'Authorization':'Bearer '+token})
    assert downloaded.status_code == 200
    value = case(tmp_path)
    value['raw'], value['snapshot'] = downloaded.content, page
    value['selectors'] = {'project-id':project,'run-id':execution.run_id,'attempt':str(page['attempt']),
        'artifact-id':artifact['artifact_id'],'artifact-digest':artifact['content_digest']}
    checked = invoke(value)
    assert checked.returncode == 0, checked.stderr
    verification_json = (tmp_path/'review/verification.json').read_bytes()
    assert json.loads(verification_json)['verified'] is True
    body = {'action':'accept_result','expected_revision':state['revision'],'idempotency_key':'explicit-exact-human',
        'run_id':execution.run_id,'artifact_id':artifact['artifact_id'],
        'expected_artifact_digest':artifact['content_digest'],'expected_lease_epoch':page['attempt']}
    accepted = call('POST', root(project)+'/'+state['agent_id']+'/control', body)
    assert accepted['human_acceptance'] is True
    loopback = fixture_api.LoopbackClient(api.app)
    seen, audit_responses, measurements = [], [], []
    import httpx
    original_send = httpx.Client.send
    def record_reads(client, request, **options):
        seen.append((request.method, request.url.path, request.url.netloc))
        response = original_send(client, request, **options)
        if request.url.path.endswith('/audit') and request.url.netloc == loopback.client.base_url.netloc:
            audit_responses.append((request.url.query.decode(),response))
        return response
    monkeypatch.setattr(httpx.Client, 'send', record_reads)
    def measured(name, operation):
        start, before = time.monotonic(),len(audit_responses)
        try: return operation()
        finally:
            rows = audit_responses[before:]
            measurements.append({'operation':name,'elapsed_seconds':time.monotonic()-start,
                'audit_pages':len(rows),'audit_bytes':sum(response.num_bytes_downloaded for query,response in rows),
                'queries':[query for query,response in rows]})
    kwargs = dict(goal_id=state['agent_id'], run_id=execution.run_id, observer_subject=state.get('owner',state.get('owner_subject')),
        expected_review_revision=accepted['revision'], expected_artifact_id=artifact['artifact_id'],
        expected_artifact_digest=artifact['content_digest'], base_url=str(loopback.client.base_url).rstrip('/'),
        bearer_token=token, authority='opensaddle://goal-review-fixture', project_id=project)
    try:
        for patch in ({'expected_artifact_digest':'0'*64},{'expected_review_revision':state['revision']},
                      {'expected_artifact_id':'art_other'},{'observer_subject':'other'},
                      {'bearer_token':'invalid-fixture'},{'project_id':'other-project'}):
            with pytest.raises((PermissionError, ValueError)):
                OpenSaddleGoalReviewedRunArtifactSource.pin(**{**kwargs, **patch})
        pin_start = len(audit_responses)
        source = OpenSaddleGoalReviewedRunArtifactSource.pin(**kwargs)
        assert audit_responses[pin_start][0] == 'after=0'
        assert audit_responses[-1][0] == 'after=' + str(source.binding.audit_sequence-1)
        if configuration_deployment == 'same-hosted':
            assert_live_audit_response_faults_deny(source,monkeypatch)
        access = Inputs()
        observation = measured('identify-1',lambda:source.identify(execution.run_id))
        assert measured('identify-2',lambda:source.identify(execution.run_id)) == observation
        assert measured('read-artifact',lambda:source.read_artifact(observation.artifact_ref,max_bytes=512000)) == downloaded.content
        access.grant(observation.run_ref, observation.artifact_ref)
        path = str(tmp_path/'knowledge.json')
        observations = ObservedRunArtifactRepository(path, tenant_id='personal', project_id=project)
        evidence = measured('observe',lambda:source.observe(observations, authorizer=access, at=datetime.now(timezone.utc)))
        assert evidence.evidence_state == 'caller_observed_unverified'
        assert source.binding.review_ref in evidence.input_refs
        access.grant(evidence.exact_ref())
        if configuration_deployment == 'separate-local':
            # Genuine separate origin/authority, explicitly delegated local
            # Project configuration; not evidence of the PG Goal's dispatch.
            local_root = tmp_path/'local-configuration-core'; local_root.mkdir()
            local_core = LiveCore(local_root)
            receipt = local_core.configuration()
            configuration_trust = local_core.pin(local_core.review())
            assert configuration_trust.authority != source.authority
        else:
            # Genuine PostgreSQL configuration lifecycle on this Goal's origin.
            # Registering a command is configuration only, not invocation proof.
            facade = SimpleNamespace(app=api.app,project=project,invoices=value['invoices'],bank=value['bank'])
            def configuration_call(method, route, status=200, body=None):
                return fixture_api.checked(loopback,method,route,status,
                    headers={'Authorization':'Bearer '+token},**({'json':body} if body is not None else {}))
            facade.call = configuration_call
            receipt = LiveCore.configuration(facade)
            configuration_trust = source
            assert receipt.environment_ref.resource_id == 'opensaddle/environment-revision/' + project
        assert receipt.execution_verified is False and receipt.activation_observed is False
        configuration_trust.verify(receipt)
        access.grant(receipt.command_ref, receipt.environment_ref)
        repository = CoreProvenanceRepository(path, tenant_id='personal', project_id=project,
            observed_source=source, observed_authorizer=source.authorizer(access))
        initial = CoreProvenanceService(repository=repository).ingest(receipt, authorizer=access, trust=configuration_trust).record
        verification = ResourceRef(authority='krail://fixture-verification', resource_type='test',
            resource_id='independent-exact-invoice-check',version='1',digest='sha256:'+sha256(verification_json).hexdigest())
        access.grant(verification)
        reviewer, action = signed_review(initial, verification, project, 'goal-configuration')
        access.grant(reviewer)
        reviewed_at = datetime.now(timezone.utc)
        access.grant(_decision_ref(create_review_decision(decision_id='configuration-review',candidate_digest=initial.record_digest,
            outcome='accepted',reviewer_ref=reviewer,evidence_refs=(verification,),recorded_at=reviewed_at)))
        ProcedureReviewService(repository=repository,clock=lambda:reviewed_at).review(initial.record_digest, decision_id='configuration-review',
            reviewer_ref=reviewer,evidence_refs=(verification,),accepted=True,authorizer=access,review_authorizer=action)
        candidate = ObservedProcedureCandidateService(repository=repository).propose(parent_decision_id='configuration-review',
            evidence_ref=evidence.exact_ref(),procedure_version='goal-invoice-1',rationale='Exact accepted synthetic invoice result, separate review required')
        assert candidate.lifecycle == 'desired' and candidate.activation_ref is None
        access.grant(procedure_record_ref(candidate))
        request = ProcedureExplanationRequest(candidate_digest=candidate.record_digest)
        assert ProcedureExplanationService(repository=repository).actionable_guidance(request,authorizer=source.authorizer(access)) is None
        reviewer, action = signed_review(candidate, verification, project, 'goal-observed')
        access.grant(reviewer)
        reviewed_at = datetime.now(timezone.utc)
        access.grant(_decision_ref(create_review_decision(decision_id='goal-observed-review',candidate_digest=candidate.record_digest,
            outcome='accepted',reviewer_ref=reviewer,evidence_refs=(verification,),recorded_at=reviewed_at)))
        promoted = ProcedureReviewService(repository=repository,clock=lambda:reviewed_at).review(candidate.record_digest,decision_id='goal-observed-review',
            reviewer_ref=reviewer,evidence_refs=(verification,),accepted=True,authorizer=source.authorizer(access),review_authorizer=action)
        access.grant(procedure_record_ref(promoted.promoted_record))
        reopened_source = OpenSaddleGoalReviewedRunArtifactSource(binding=source.binding, **{k:kwargs[k] for k in ('base_url','bearer_token','authority','project_id')})
        reopened = CoreProvenanceRepository(path,tenant_id='personal',project_id=project,
            observed_source=reopened_source,observed_authorizer=reopened_source.authorizer(access))
        assert measured('reopened-guidance',lambda:ProcedureExplanationService(repository=reopened).actionable_guidance(request,authorizer=reopened_source.authorizer(access))) is not None
        pg_reads = [(method, route) for method, route, host in seen if host == loopback.client.base_url.netloc]
        configuration_path = '/api/v2/projects/' + project + '/environment/changes'
        assert [(method, route) for method, route in pg_reads if method != 'GET'] == (
            [('POST', configuration_path)] if configuration_deployment == 'same-hosted' else [])
        assert all(route.endswith(('/audit','/capabilities','/content','/provenance-observation'))
            for method, route in pg_reads if method == 'GET')
        # Actual current membership revocation must deny even a pinned event.
        with repo._connect() as connection:
            connection.execute('UPDATE control_plane_memberships SET role=%s WHERE project_id=%s AND subject=%s',
                ('auditor',project,kwargs['observer_subject']))
        try:
            with pytest.raises(PermissionError): source.identify(execution.run_id)
        finally:
            with repo._connect() as connection:
                connection.execute('UPDATE control_plane_memberships SET role=%s WHERE project_id=%s AND subject=%s',
                    ('owner',project,kwargs['observer_subject']))
        # Deliberate authority fault injection models selected source withdrawal.
        with repo._connect() as connection:
            connection.execute('UPDATE control_plane_sources SET revision=%s WHERE source_id=%s', ('c'*40,source_metadata['source_id']))
        with pytest.raises(PermissionError):
            ProcedureExplanationService(repository=reopened).actionable_guidance(request,authorizer=reopened_source.authorizer(access))
    finally:
        output = os.environ.get('FK_GOAL_AUDIT_METRICS')
        if output:
            directory = Path(output);directory.mkdir(parents=True,exist_ok=True)
            import rail.opensaddle_goal_run_artifact as implementation
            (directory/(configuration_deployment+'.json')).write_text(json.dumps({
                'implementation_sha256':sha256(Path(implementation.__file__).read_bytes()).hexdigest(),
                'history_controls':history_pairs*2,'deployment':configuration_deployment,'measurements':measurements},indent=2)+'\n')
        if 'local_core' in locals(): local_core.close()
        loopback.close()


@pytest.mark.parametrize('fault', ['redirect', 'compression', 'oversize', 'not_object'])
def test_audit_transport_fails_closed_before_evidence(fault):
    import httpx
    from rail.opensaddle_run_artifact import OpenSaddleRunArtifactSource
    probe = OpenSaddleRunArtifactSource(base_url='https://fixture.example',bearer_token='fixture-token',
        authority='opensaddle://bounded-audit',project_id='fixture')
    def respond(request):
        if fault == 'redirect': return httpx.Response(302,headers={'Location':'https://other.example'})
        if fault == 'compression': return httpx.Response(200,headers={'Content-Encoding':'gzip'},content=b'invalid')
        if fault == 'oversize': return httpx.Response(200,stream=httpx.ByteStream(b' ' * 1_000_001))
        return httpx.Response(200,stream=httpx.ByteStream(b'[]'))
    with httpx.Client(transport=httpx.MockTransport(respond),base_url='https://fixture.example',follow_redirects=False) as client:
        with pytest.raises((PermissionError, ValueError)):
            OpenSaddleGoalReviewedRunArtifactSource._audit_page(probe,client,'/audit')


def test_real_legacy_run_only_acceptance_cannot_pin_artifact_review(placement):
    from test_project_goal_result_review import completed
    repo, api, call, project, _, state, _, run_id, _, _, _, artifact = completed(placement)
    accepted = call('POST',root(project)+'/'+state['agent_id']+'/control', {
        'action':'accept_result','expected_revision':state['revision'],
        'idempotency_key':'legacy-lower-assurance','run_id':run_id})
    assert accepted['human_acceptance'] is True
    assert 'result_review' not in accepted['runs'][-1]
    listener = fixture_api.LoopbackClient(api.app)
    try:
        with pytest.raises(PermissionError,match='exact human-accepted Goal artifact review'):
            OpenSaddleGoalReviewedRunArtifactSource.pin(goal_id=state['agent_id'],run_id=run_id,
                observer_subject=accepted['owner_subject'],expected_review_revision=accepted['revision'],
                expected_artifact_id=artifact['artifact_id'],expected_artifact_digest=artifact['content_digest'],
                base_url=str(listener.client.base_url).rstrip('/'),bearer_token=api.app.state.control_plane_settings.authenticator.verifier.token,
                authority='opensaddle://legacy-goal',project_id=project)
    finally:
        listener.close()
