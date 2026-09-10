"""Production reader acceptance through retained reviewed Git evidence."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import subprocess

import pytest
from krail.provider.v1 import GetResourceRequest, GetResourceResult, ResourcePayload, ResourceRef, SearchRequest
from rail.context_brief import ContextBriefRequest, ContextBriefService
from rail.registered_evidence_context import RegisteredEvidenceContextReader, RegisteredEvidenceSelection
from rail.registered_git_evidence import RegisteredGitEvidenceBridge, registered_git_evidence_request_digest
from rail.hosted.access import AccessClaims, AccessContextAuthority, MemoryRevocationRegistry, PacketRequestBinding

NOW = datetime(2026, 9, 9, tzinfo=UTC)
DIGEST = 'sha256:' + 'a' * 64


def ref(kind, identity, content, authority='opensaddle://core'):
    return ResourceRef(authority=authority, resource_type=kind, resource_id=identity, version='1', digest='sha256:' + sha256(content).hexdigest())


@pytest.fixture
def composition(tmp_path, request):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'guide.md').write_bytes(getattr(request, 'param', 'Fix Unicode clipping: café ☕\n'.encode()))
    subprocess.run(['git', '-C', str(repo), 'add', 'guide.md'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'guide'], check=True)
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority({'core': b'test-only-authority'}, issuer='opensaddle://core', revocations=revocations)
    state = tmp_path / 'state'
    target = ['setup:setup']
    def context_for(subject, action, resource, request_target=None):
        claims = AccessClaims(issuer=authority.issuer, tenant_id='tenant', project_id='project', subject=subject, delegator=subject, delegation_id='selected-doc', capability_id='evidence', capability_version='1', capability_digest=DIGEST, actions=(action,), source_ids=(resource.resource_id,), classifications=('internal',), policy_digest=DIGEST, issued_at=NOW-timedelta(minutes=1), not_before=NOW-timedelta(minutes=1), expires_at=NOW+timedelta(hours=1), nonce=action)
        context = authority.issue(claims, key_id='core')
        binding = PacketRequestBinding(access_context_digest=context.context_digest, tenant_id='tenant', project_id='project', capability_id='evidence', capability_version='1', capability_digest=DIGEST, request_digest=registered_git_evidence_request_digest(action, resource, request_target or target[0], state), purpose='registered-git-evidence', scope=resource.authority, issued_at=NOW-timedelta(minutes=1), not_before=NOW-timedelta(minutes=1), expires_at=NOW+timedelta(hours=1), nonce=action)
        return context, authority.issue_packet_request_binding(binding, key_id='core')
    bridge = RegisteredGitEvidenceBridge(repo, state, authority=authority, context_for=context_for, tenant_id='tenant', project_id='project', capability_id='evidence', capability_version='1', capability_digest=DIGEST, clock=lambda: NOW)
    bridge.setup(user_id='owner', setup_id='setup')
    target[0] = 'capture:doc'
    captured = bridge.capture(user_id='owner', commit=commit, path='guide.md', capture_id='doc')
    target[0] = 'review:doc:review:' + captured.content_digest
    bridge.review(user_id='owner', capture_id='doc', review_id='review')
    target[0] = 'retrieve:doc'
    contents = {'repository/project': f'Registered repository {repo} at {commit}'.encode(), 'task/request': b'Fix Unicode clipping; return diff and test results.'}
    operational = tuple(ref('repository' if key.startswith('repository') else 'task', key, value) for key, value in contents.items())
    allowed = {r.exact_key for r in (*operational, captured.source_ref)}
    def authorize(resource):
        if resource.exact_key not in allowed:
            raise PermissionError('unavailable')
    def read_operation(request):
        return GetResourceResult(resource=ResourcePayload(ref=request.ref, content=contents[request.ref.resource_id].decode(), media_type='text/plain'))
    kwargs = dict(bridge=bridge, subject='owner', selections=(RegisteredEvidenceSelection('doc', captured.source_ref),), operational_refs=operational, read_operational=read_operation, authorize=authorize)
    return kwargs, captured, contents, allowed, revocations, authority, state


@pytest.mark.parametrize('query', ['Unicode', 'Fix Unicode clipping café absent'])
def test_context_uses_real_typed_operational_refs_and_retained_document(composition, query):
    kwargs, captured, _, _, _, _, _ = composition
    reader = RegisteredEvidenceContextReader(**kwargs)
    repository, task = kwargs['operational_refs']
    brief = ContextBriefService(reader).assemble(ContextBriefRequest(repository=repository, issue=task, evaluated_at=NOW, query=query))
    document = next(item for item in brief.evidence.items if item.source == captured.source_ref)
    assert document.relevance == (1.0 if query == 'Unicode' else 0.8)
    assert brief.repository.resource_type == 'repository'
    assert brief.issue.resource_type == 'task'
    assert {item.source.exact_key for item in brief.evidence.items} == {repository.exact_key, task.exact_key, captured.source_ref.exact_key}
    assert any('café ☕' in item.excerpt for item in brief.evidence.items)
    reopened = RegisteredEvidenceContextReader(**kwargs)
    assert reopened.get_resource(GetResourceRequest(ref=captured.source_ref)).resource.content.encode() == captured.content


def test_exactness_revocation_and_final_search_authorization(composition):
    kwargs, captured, _, allowed, revoked, _, _ = composition
    reader = RegisteredEvidenceContextReader(**kwargs)
    with pytest.raises(PermissionError):
        reader.get_resource(GetResourceRequest(ref=captured.source_ref.model_copy(update={'version': '2'})))
    original = kwargs['authorize']
    calls = [0]
    def revoke_at_search_release(resource):
        calls[0] += 1
        if calls[0] == 3:
            allowed.clear()
        original(resource)
    with pytest.raises(PermissionError):
        RegisteredEvidenceContextReader(**{**kwargs, 'authorize': revoke_at_search_release}).search(SearchRequest(query='Unicode'))
    allowed.add(captured.source_ref.exact_key)
    revoked.revoke_delegation('selected-doc', revoked_at=NOW)
    with pytest.raises(PermissionError):
        reader.get_resource(GetResourceRequest(ref=captured.source_ref))


def test_bounds_collisions_and_utf8_clipping(composition):
    kwargs, captured, _, _, _, _, _ = composition
    reader = RegisteredEvidenceContextReader(**kwargs)
    clipped = reader.get_resource(GetResourceRequest(ref=captured.source_ref, max_bytes=26)).resource
    assert clipped.truncated and len(clipped.content.encode()) <= 26
    assert clipped.content == captured.content[:26].decode('utf-8', errors='ignore')
    with pytest.raises(ValueError):
        RegisteredEvidenceContextReader(**{**kwargs, 'operational_refs': (*kwargs['operational_refs'], captured.source_ref.model_copy(update={'authority': 'other://source'}))})
    with pytest.raises(ValueError):
        RegisteredEvidenceContextReader(**kwargs, max_source_bytes=2).get_resource(GetResourceRequest(ref=captured.source_ref))
    with pytest.raises(ValueError):
        reader.search(SearchRequest(query='Unicode', cursor='unsupported'))
    with pytest.raises(ValueError):
        RegisteredEvidenceContextReader(**kwargs, max_total_source_bytes=2).search(SearchRequest(query='Unicode'))


@pytest.mark.parametrize('query', ['Unicode', 'Fix Unicode clipping café absent'])
def test_signed_packet_restart_and_document_revocation(composition, query):
    from rail.authorized_context import AuthorizedContextPacketService, AuthorizedContextPacketCreateRequest, AuthorizedContextPacketReadRequest, authorized_context_packet_request_digest
    from rail.capability_publication import authorized_context_packet_descriptor
    kwargs, captured, _, _, revoked, authority, state = composition
    refs = (*kwargs['operational_refs'], captured.source_ref)
    descriptor = authorized_context_packet_descriptor()
    context_request = ContextBriefRequest(repository=refs[0], issue=refs[1], evaluated_at=NOW, query=query)
    claims = AccessClaims(issuer=authority.issuer, tenant_id='tenant', project_id='project', subject='owner', delegator='owner', delegation_id='packet-read', capability_id=descriptor.capability_id, capability_version=descriptor.semantic_version, capability_digest=descriptor.descriptor_digest, actions=('context.read',), source_ids=tuple(r.resource_id for r in refs), classifications=('internal',), policy_digest=DIGEST, issued_at=NOW-timedelta(minutes=1), not_before=NOW-timedelta(minutes=1), expires_at=NOW+timedelta(hours=1), nonce='packet')
    context = authority.issue(claims, key_id='core')
    digest = authorized_context_packet_request_digest(exact_refs=refs, context_request=context_request, purpose='maintenance', scope='project', max_context_tokens=32768)
    binding = PacketRequestBinding(access_context_digest=context.context_digest, tenant_id='tenant', project_id='project', capability_id=descriptor.capability_id, capability_version=descriptor.semantic_version, capability_digest=descriptor.descriptor_digest, request_digest=digest, purpose='maintenance', scope='project', issued_at=NOW-timedelta(minutes=1), not_before=NOW-timedelta(minutes=1), expires_at=NOW+timedelta(hours=1), nonce='binding')
    signed = authority.issue_packet_request_binding(binding, key_id='core')
    heads = {r.resource_id: r for r in refs}
    def reopen():
        return AuthorizedContextPacketService(ContextBriefService(RegisteredEvidenceContextReader(**kwargs)), project_path=state, authority=authority, tenant_id='tenant', project_id='project', capability_descriptor_digest=descriptor.descriptor_digest, current_ref_resolver=heads.__getitem__, clock=lambda: NOW)
    packet = reopen().create(AuthorizedContextPacketCreateRequest(access_context=context, request_binding=signed, exact_refs=refs, context_request=context_request, purpose='maintenance', scope='project'))
    read = AuthorizedContextPacketReadRequest(packet_digest=packet.packet_digest, access_context=context, request_binding=signed, exact_refs=refs, query=query, purpose='maintenance', scope='project')
    assert reopen().read(read).packet == packet
    heads[captured.source_ref.resource_id] = captured.source_ref.model_copy(update={'version': 'advanced'})
    assert reopen().read(read).status == 'context_packet_unavailable'
    heads[captured.source_ref.resource_id] = captured.source_ref
    # Revoke the bridge delegation only: the packet grant remains valid, but
    # its retained source must cease being served through the same reader.
    revoked.revoke_delegation('selected-doc', revoked_at=NOW)
    result = reopen().read(read)
    assert result.packet is None and result.reauthorization is None


def test_reader_rejects_false_operational_receipt_and_changed_retained_bytes(composition):
    kwargs, captured, _, _, _, _, state = composition
    repository, task = kwargs['operational_refs']
    original = kwargs['read_operational']
    wrong = lambda request: original(GetResourceRequest(ref=task))
    with pytest.raises(PermissionError):
        RegisteredEvidenceContextReader(**{**kwargs, 'read_operational': wrong}).get_resource(GetResourceRequest(ref=repository))
    (state / 'sources' / 'registered' / 'doc.bin').write_bytes(b'tampered')
    with pytest.raises(PermissionError):
        RegisteredEvidenceContextReader(**kwargs).search(SearchRequest(query='Unicode'))


@pytest.mark.parametrize('composition', [b'\xff invalid text'], indirect=True)
def test_rejects_non_utf8_reviewed_document(composition):
    kwargs, captured, *_ = composition
    with pytest.raises(UnicodeDecodeError):
        RegisteredEvidenceContextReader(**kwargs).get_resource(GetResourceRequest(ref=captured.source_ref))


def test_review_withdrawal_and_operational_revocation_deny_before_release(composition):
    from rail.integrity import ResearchIntegrityRepo
    kwargs, captured, _, allowed, _, _, state = composition
    reader = RegisteredEvidenceContextReader(**kwargs)
    integrity = ResearchIntegrityRepo(state)
    source = integrity.load_sources()[0]
    integrity.update_source(source.source_key, quality_status='candidate')
    with pytest.raises(PermissionError):
        reader.get_resource(GetResourceRequest(ref=captured.source_ref))
    repository = kwargs['operational_refs'][0]
    original = kwargs['read_operational']
    def revoke_during_read(request):
        result = original(request)
        allowed.clear()
        return result
    with pytest.raises(PermissionError):
        RegisteredEvidenceContextReader(**{**kwargs, 'read_operational': revoke_during_read}).get_resource(GetResourceRequest(ref=repository))


def test_inspect_list_and_exact_review_before_promotion(composition):
    from rail.integrity import ResearchIntegrityRepo
    kwargs, captured, _, _, _, _, state = composition
    original = kwargs['bridge']
    bridge = RegisteredGitEvidenceBridge(original.source_repo, state, authority=original.authority, context_for_request=original.context_for, tenant_id='tenant', project_id='project', capability_id='evidence', capability_version='1', capability_digest=DIGEST, clock=lambda: NOW)
    raw = bridge.capture(user_id='owner', commit=captured.commit, path=captured.path, capture_id='pending')
    before = ResearchIntegrityRepo(state).load_sources()
    inspected = bridge.inspect(user_id='owner', capture_id='pending')
    assert inspected.content == raw.content and inspected.review_id == ''
    assert ResearchIntegrityRepo(state).load_sources() == before
    metadata = bridge.list_captures(user_id='owner')
    pending = next(item for item in metadata if item.capture_id == 'pending')
    assert pending.reviewed_at is None and not hasattr(pending, 'content')
    assert len(bridge.list_captures(user_id='owner', limit=1)) == 1
    with pytest.raises(PermissionError):
        bridge.retrieve(user_id='owner', capture_id='pending')
    with pytest.raises(ValueError, match='digest changed'):
        bridge.review(user_id='owner', capture_id='pending', review_id='accepted', expected_content_digest=DIGEST)
    assert ResearchIntegrityRepo(state).load_sources() == before
    reviewed = bridge.review(user_id='owner', capture_id='pending', review_id='accepted', expected_content_digest=inspected.content_digest)
    assert bridge.review(user_id='owner', capture_id='pending', review_id='accepted', expected_content_digest=inspected.content_digest) == reviewed
    with pytest.raises(ValueError, match='different review identity'):
        bridge.review(user_id='owner', capture_id='pending', review_id='other', expected_content_digest=inspected.content_digest)
    assert bridge.inspect(user_id='owner', capture_id='pending').review_id == 'accepted'
    with pytest.raises(ValueError):
        bridge.list_captures(user_id='owner', limit=101)


def test_inspection_and_listing_enforce_exact_target_and_post_read_revocation(composition, monkeypatch):
    kwargs, captured, _, _, revoked, _, _ = composition
    bridge = kwargs['bridge']
    callback = bridge.context_for
    bridge.context_for_request = callback
    assert len(bridge.list_captures(user_id='owner')) == 1
    bridge.context_for_request = lambda subject, action, resource, target: callback(subject, action, resource, 'inspect:wrong-capture')
    with pytest.raises(PermissionError):
        bridge.inspect(user_id='owner', capture_id='doc')
    assert bridge.list_captures(user_id='owner') == ()
    bridge.context_for_request = callback
    original = bridge._read_local_bounded
    def revoke_after_blob(path, limit):
        result = original(path, limit)
        if path.suffix == '.bin':
            revoked.revoke_delegation('selected-doc', revoked_at=NOW)
        return result
    monkeypatch.setattr(bridge, '_read_local_bounded', revoke_after_blob)
    with pytest.raises(PermissionError):
        bridge.inspect(user_id='owner', capture_id='doc')
    assert bridge.list_captures(user_id='owner') == ()
