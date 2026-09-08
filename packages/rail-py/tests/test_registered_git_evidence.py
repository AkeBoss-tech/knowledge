from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import subprocess
from pathlib import Path
import pytest
from rail.registered_git_evidence import RegisteredGitEvidenceBridge
from rail.hosted.access import AccessClaims, AccessContextAuthority, MemoryRevocationRegistry, PacketRequestBinding

NOW=datetime(2026,9,7,12,tzinfo=UTC); DIGEST='sha256:'+'a'*64

@pytest.mark.parametrize('fault',["revoke","expire"])
def test_capture_publication_lock_rechecks_original_signed_delegation(tmp_path: Path,fault):
    repo=tmp_path/'repo'; repo.mkdir(); subprocess.run(['git','init','-q',str(repo)],check=True); subprocess.run(['git','-C',str(repo),'config','user.email','t@e'],check=True); subprocess.run(['git','-C',str(repo),'config','user.name','Test'],check=True)
    (repo/'evidence.md').write_bytes(b'evidence\n'); subprocess.run(['git','-C',str(repo),'add','.'],check=True); subprocess.run(['git','-C',str(repo),'commit','-qm','evidence'],check=True); commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    now=[NOW]; revoked=MemoryRevocationRegistry(); authority=AccessContextAuthority({'k':b'key'},issuer='issuer',revocations=revoked)
    def resolver(subject,action,ref):
        claims=AccessClaims(issuer='issuer',tenant_id='t',project_id='p',subject=subject,delegator=subject,delegation_id='d',capability_id='cap',capability_version='1',capability_digest=DIGEST,actions=(action,),source_ids=(ref.resource_id,),classifications=('internal',),policy_digest=DIGEST,issued_at=NOW-timedelta(minutes=1),not_before=NOW-timedelta(minutes=1),expires_at=NOW+timedelta(hours=1),nonce='n'+action)
        ctx=authority.issue(claims,key_id='k'); target='setup:s' if ref.resource_id.startswith('knowledge-state/') else 'capture:c'
        binding=PacketRequestBinding(access_context_digest=ctx.context_digest,tenant_id='t',project_id='p',capability_id='cap',capability_version='1',capability_digest=DIGEST,request_digest=bridge._request_digest(action,ref,target),purpose='registered-git-evidence',scope=ref.authority,issued_at=NOW-timedelta(minutes=1),not_before=NOW-timedelta(minutes=1),expires_at=NOW+timedelta(minutes=1) if fault=='expire' else NOW+timedelta(hours=1),nonce='b'+action)
        return ctx,authority.issue_packet_request_binding(binding,key_id='k')
    bridge=RegisteredGitEvidenceBridge(repo,tmp_path/'state',authority=authority,context_for=resolver,tenant_id='t',project_id='p',capability_id='cap',capability_version='1',capability_digest=DIGEST,clock=lambda:now[0]); bridge.setup(user_id='alice',setup_id='s')
    original=bridge._locked_manifest
    @contextmanager
    def fault_lock():
        with original():
            if fault=='revoke': revoked.revoke_delegation('d',revoked_at=NOW)
            else: now[0]=NOW+timedelta(minutes=2)
            yield
    bridge._locked_manifest=fault_lock
    with pytest.raises(PermissionError): bridge.capture(user_id='alice',commit=commit,path='evidence.md',capture_id='c')
    assert not (tmp_path/'state'/'sources'/'registered'/'c.bin').exists()

def test_signed_git_source_review_restart_and_tamper(tmp_path: Path):
    repo=tmp_path/'repo'; repo.mkdir(); subprocess.run(['git','init','-q',str(repo)],check=True); subprocess.run(['git','-C',str(repo),'config','user.email','t@e'],check=True); subprocess.run(['git','-C',str(repo),'config','user.name','Test'],check=True)
    (repo/'evidence.md').write_bytes(b'actual retained evidence\n'); subprocess.run(['git','-C',str(repo),'add','.'],check=True); subprocess.run(['git','-C',str(repo),'commit','-qm','evidence'],check=True); commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    revocations=MemoryRevocationRegistry(); authority=AccessContextAuthority({'k':b'key'},issuer='issuer',revocations=revocations)
    now=[NOW]; clock=lambda: now[0]
    def context_for(subject, action, ref):
        claims=AccessClaims(issuer='issuer',tenant_id='t',project_id='p',subject=subject,delegator=subject,delegation_id='d-'+subject,capability_id='cap',capability_version='1',capability_digest=DIGEST,actions=(action,),source_ids=(("other.md",) if subject=='bob' else (ref.resource_id,)),classifications=('internal',),policy_digest=DIGEST,issued_at=NOW-timedelta(minutes=1),not_before=NOW-timedelta(minutes=1),expires_at=NOW+timedelta(hours=1),nonce='n-'+action)
        ctx=authority.issue(claims,key_id='k')
        binding=PacketRequestBinding(access_context_digest=ctx.context_digest,tenant_id='t',project_id='p',capability_id='cap',capability_version='1',capability_digest=DIGEST,request_digest='sha256:'+'0'*64,purpose='registered-git-evidence',scope=ref.authority,issued_at=NOW-timedelta(minutes=1),not_before=NOW-timedelta(minutes=1),expires_at=NOW+timedelta(hours=1),nonce='b-'+action)
        # Bind to the bridge's exact target digest.
        target=('setup:s1' if ref.resource_id.startswith('knowledge-state/') else (('capture:c2' if ref.resource_id=='second.md' else 'capture:c1') if action=='capture.write' else {'shared_knowledge.review':'review:c1:r1:'+ref.digest,'capture.read':'retrieve:c1','shared_knowledge.read':'retrieve:c1'}[action]))
        binding=binding.model_copy(update={'request_digest':bridge._request_digest(action,ref,target)})
        return ctx,authority.issue_packet_request_binding(binding,key_id='k')
    bridge=RegisteredGitEvidenceBridge(repo,tmp_path/'state',authority=authority,context_for=context_for,tenant_id='t',project_id='p',capability_id='cap',capability_version='1',capability_digest=DIGEST,clock=clock)
    bridge.setup(user_id='alice',setup_id='s1')
    with pytest.raises(ValueError): bridge.capture(user_id='alice',commit='bad',path='evidence.md',capture_id='bad')
    with pytest.raises(ValueError): bridge.capture(user_id='alice',commit=commit,path='../evidence.md',capture_id='bad')
    with pytest.raises(ValueError): bridge.capture(user_id='alice',commit=commit,path='evidence.md',capture_id='bad/id')
    candidate=bridge.capture(user_id='alice',commit=commit,path='evidence.md',capture_id='c1')
    try: bridge.retrieve(user_id='alice',capture_id='c1')
    except PermissionError: pass
    else: raise AssertionError('unreviewed source was readable')
    with pytest.raises(PermissionError): bridge.review(user_id='alice',capture_id='c1',review_id='r2')
    from rail.integrity import ResearchIntegrityRepo
    assert not ResearchIntegrityRepo(tmp_path/'state').load_sources()
    reviewed=bridge.review(user_id='alice',capture_id='c1',review_id='r1'); assert reviewed.content==b'actual retained evidence\n'
    assert bridge.retrieve(user_id='alice',capture_id='c1').content==reviewed.content
    restarted=RegisteredGitEvidenceBridge(repo,tmp_path/'state',authority=authority,context_for=context_for,tenant_id='t',project_id='p',capability_id='cap',capability_version='1',capability_digest=DIGEST,clock=clock)
    assert restarted.retrieve(user_id='alice',capture_id='c1').content==reviewed.content
    with pytest.raises(PermissionError): restarted.retrieve(user_id='bob',capture_id='c1')
    stored=tmp_path/'state'/'sources'/'registered'/'c1.bin'
    saved=stored.read_bytes(); twin=stored.with_name('twin.bin'); twin.write_bytes(saved); stored.unlink(); stored.symlink_to(twin.name)
    with pytest.raises(PermissionError): restarted.retrieve(user_id='alice',capture_id='c1')
    stored.unlink(); stored.write_bytes(saved)
    manifest=tmp_path/'state'/'.krail'/'registered-evidence-staging.json'; original=manifest.read_bytes(); manifest.write_bytes(b'x'*(1_048_577))
    with pytest.raises(PermissionError): restarted.retrieve(user_id='alice',capture_id='c1')
    manifest.write_bytes(original)
    stored.write_bytes(b'tampered')
    with pytest.raises(PermissionError): restarted.retrieve(user_id='alice',capture_id='c1')
    stored.write_bytes(saved)
    # A second bridge shares the same durable manifest; publication locking
    # must preserve this new capture while the first bridge reviews c1.
    (repo/'second.md').write_bytes(b'second evidence\n'); subprocess.run(['git','-C',str(repo),'add','.'],check=True); subprocess.run(['git','-C',str(repo),'commit','-qm','second'],check=True); second_commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    second_bridge=RegisteredGitEvidenceBridge(repo,tmp_path/'state',authority=authority,context_for=context_for,tenant_id='t',project_id='p',capability_id='cap',capability_version='1',capability_digest=DIGEST,clock=clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        review_future=pool.submit(restarted.review,user_id='alice',capture_id='c1',review_id='r1')
        capture_future=pool.submit(second_bridge.capture,user_id='alice',commit=second_commit,path='second.md',capture_id='c2')
        assert review_future.result().content==reviewed.content
        assert capture_future.result().capture_id=='c2'
    assert restarted.retrieve(user_id='alice',capture_id='c1').content==reviewed.content
    assert second_bridge._read_manifest()['c2']['path']=='second.md'
    with pytest.raises(PermissionError): restarted.retrieve(user_id='bob',capture_id='c1')
    now[0] = NOW + timedelta(hours=2)
    with pytest.raises(PermissionError): restarted.retrieve(user_id='alice',capture_id='c1')
    now[0] = NOW
    revocations.revoke_delegation('d-alice', revoked_at=NOW)
    with pytest.raises(PermissionError): restarted.retrieve(user_id='alice',capture_id='c1')
    blob=tmp_path/'state'/reviewed.source_ref.resource_id
    stored=tmp_path/'state'/'sources'/'registered'/'c1.bin'; stored.write_bytes(b'tampered')
    try: bridge.retrieve(user_id='alice',capture_id='c1')
    except PermissionError: pass
    else: raise AssertionError('tampered source was readable')
