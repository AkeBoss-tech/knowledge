"""Trusted-local CLI security checks, independent of optional Core fixtures."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

def test_private_store_rejects_symlinks_and_shared_directory(tmp_path):
    from rail.goal_evidence_cli import _private_store
    private=tmp_path/'private';private.mkdir(mode=0o700)
    outsider=tmp_path/'outside';outsider.write_text('preserve');outsider.chmod(0o600)
    for name in ('data.json','data.json.lock'):
        link=private/name;link.symlink_to(outsider)
        with pytest.raises(OSError): _private_store(str(private/'data.json'))
        link.unlink()
        (private/'data.json.lock').unlink(missing_ok=True)
    shared=tmp_path/'shared';shared.mkdir(mode=0o755)
    with pytest.raises(PermissionError): _private_store(str(shared/'data.json'))
    assert outsider.read_text()=='preserve'


@pytest.mark.parametrize('flag', ['--trust-local-core', '--dedicated-short-lived-credential'])
def test_untrusted_loopback_never_receives_credential(tmp_path, flag):
    import socket
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.2)
    token=tmp_path/'token';token.write_text('synthetic-credential');token.chmod(0o600)
    store=tmp_path/'state';store.mkdir(mode=0o700)
    args=[flag,'--core-origin',f'http://127.0.0.1:{listener.getsockname()[1]}','--token-file',str(token),'--authority','opensaddle://fixture','--project-id','p','--goal-id','g','--run-id','run_x','--observer','owner','--artifact-id','art_x','--artifact-digest','a'*64,'--review-revision','1','--tenant','t','--store',str(store/'evidence.json')]
    try:
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        result=subprocess.run([sys.executable,'-m','rail.goal_evidence_cli','capture',*args],env=env,capture_output=True,text=True,timeout=10)
        assert result.returncode!=0 and 'synthetic-credential' not in result.stdout+result.stderr
        with pytest.raises(TimeoutError): listener.accept()
    finally:
        listener.close()


@pytest.mark.parametrize('kind', ['credential', 'store', 'lock'])
def test_hard_links_are_rejected_without_changing_existing_file(tmp_path, kind):
    from rail.goal_evidence_cli import _private_store, _token
    root=tmp_path/'private';root.mkdir(mode=0o700)
    original=root/'original';original.write_text('keep');original.chmod(0o600)
    alias=root/('token' if kind=='credential' else 'data.json' if kind=='store' else 'data.json.lock')
    os.link(original,alias)
    with pytest.raises(PermissionError):
        _token(alias) if kind=='credential' else _private_store(str(root/'data.json'))
    assert original.read_text()=='keep'
