"""Owner-selected Goal evidence CLI across real loopback Core and PostgreSQL."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
import uvicorn
from urllib.parse import urlsplit

pytest.importorskip('opensaddle.control_plane.api', reason='requires optional actual Core runtime')
import opensaddle
CORE = Path(os.environ.get('OPENSADDLE_TEST_SOURCE_ROOT', Path(opensaddle.__file__).resolve().parents[2]))
if not (CORE/'tests/test_work_placement.py').is_file(): pytest.skip('requires Core source test harness', allow_module_level=True)
if str(CORE/'tests') not in sys.path:
    sys.path.insert(0, str(CORE/'tests'))
from test_postgres_control_plane import isolated_capacity_postgres_url  # noqa: E402,F401
from test_work_placement import placement  # noqa: E402,F401
from test_project_goal_tick import isolated_goals, scheduler, PATH  # noqa: E402,F401
from test_noncoding_goal_journey import sandbox_goal, client_config, due  # noqa: E402
from test_project_goal_loop import root  # noqa: E402
from opensaddle.control_plane.worker_client import OutboundWorkerClient  # noqa: E402
from opensaddle.control_plane.work_session_worker import WorkSessionWorker  # noqa: E402
from opensaddle.control_plane.worker_loop import FencedWorkerLoop  # noqa: E402
from opensaddle.control_plane.native_worker import OutboundCodexWorkerExecutor  # noqa: E402
from opensaddle.storage.runtime_store import RuntimeStore  # noqa: E402
from opensaddle.execution.local_sandbox import LocalSandboxProvider  # noqa: E402

url = os.getenv('OPENSADDLE_TEST_POSTGRES_URL')
if url and (urlsplit(url).scheme not in {'postgres','postgresql'} or urlsplit(url).hostname not in {'localhost','127.0.0.1','::1'}):
    raise RuntimeError('fixture requires explicit loopback PostgreSQL')

pytestmark = pytest.mark.skipif(not os.getenv('OPENSADDLE_TEST_POSTGRES_URL') or not LocalSandboxProvider.available(),reason='requires disposable loopback PostgreSQL and Seatbelt')


def test_cli_captures_and_recalls_exact_accepted_goal_and_revocation(placement,scheduler,tmp_path):
    repo,api,call,project,source,workers,*_=placement; worker=workers[0]; headers,_=scheduler
    with OutboundWorkerClient(client_config(worker),transport=api._transport) as client:
        assert WorkSessionWorker(client,{'project_id':project},worktree=tmp_path,state_dir=tmp_path/'state').attest()['accepted']
        state=call('POST',root(project),sandbox_goal(source,tmp_path),status=201)
        assert api.post(PATH,headers=headers).status_code==200
        class NoModel:
            def shutdown(self): pass
        with RuntimeStore(tmp_path/'worker.db') as journal:
            result=FencedWorkerLoop(client,OutboundCodexWorkerExecutor(client,NoModel(),journal,workspace=tmp_path)).run_once()
        assert result.status=='completed'
    due(repo,state['agent_id']);assert api.post(PATH,headers=headers).status_code==200
    state=api.app.state.project_goal_loop.read(state['agent_id']); artifact=repo.artifacts(result.run_id)[-1]
    accepted=call('POST',root(project)+'/'+state['agent_id']+'/control',{'action':'accept_result','expected_revision':state['revision'],'idempotency_key':'owner-review','run_id':result.run_id,'artifact_id':artifact['artifact_id'],'expected_artifact_digest':artifact['content_digest'],'expected_lease_epoch':repo.get_run(result.run_id)['lease_epoch']})
    import socket
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(50);port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(api.app,log_level='error',lifespan='off'));thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.02)
        assert server.started
        token=api.app.state.control_plane_settings.authenticator.verifier.token
        token_file=tmp_path/'token';token_file.write_text(token);token_file.chmod(0o600)
        args=['--trust-local-core','--dedicated-short-lived-credential','--core-origin',f'http://127.0.0.1:{port}','--token-file',str(token_file),'--authority','opensaddle://fixture','--project-id',project,'--goal-id',state['agent_id'],'--run-id',result.run_id,'--observer',state['owner'],'--artifact-id',artifact['artifact_id'],'--artifact-digest',artifact['content_digest'],'--review-revision',str(accepted['revision']),'--tenant','fixture','--store',str(tmp_path/'evidence.json')]
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        def cli(operation,extra=()):
            return subprocess.run([sys.executable,'-m','rail.goal_evidence_cli',operation,*args,*extra],env=env,capture_output=True,text=True,timeout=60)
        captured=cli('capture');assert captured.returncode==0,captured.stderr
        data=json.loads(captured.stdout);assert data['evidence_state']=='caller_observed_unverified'
        repeated=cli('capture');assert json.loads(repeated.stdout)==data
        recalled=cli('recall',['--evidence-digest',data['evidence_ref']['digest']]);assert recalled.returncode==0,recalled.stderr
        assert json.loads(recalled.stdout)['content_base64']
        wrong=args.copy();wrong[wrong.index('--artifact-digest')+1]='0'*64
        bad=subprocess.run([sys.executable,'-m','rail.goal_evidence_cli','capture',*wrong],env=env,capture_output=True,text=True,timeout=60)
        assert bad.returncode!=0 and not bad.stdout
        token_file.chmod(0o644)
        unsafe=cli('capture');assert unsafe.returncode!=0 and not unsafe.stdout
        token_file.chmod(0o600)
        repo.update_source_snapshot(source['source_id'],revision='a'*40,snapshot_digest='b'*64)
        stale=cli('recall',['--evidence-digest',data['evidence_ref']['digest']]);assert stale.returncode!=0 and not stale.stdout
        repo.update_source_snapshot(source['source_id'],revision=source['revision'],snapshot_digest=source['snapshot_digest'])
        with repo._connect() as c:c.execute('UPDATE control_plane_memberships SET role=%s WHERE project_id=%s AND subject=%s',('auditor',project,state['owner']))
        denied=cli('recall',['--evidence-digest',data['evidence_ref']['digest']]);assert denied.returncode!=0 and not denied.stdout
        assert token not in captured.stdout+captured.stderr+recalled.stdout+recalled.stderr+denied.stdout+denied.stderr
        assert token not in (tmp_path/'evidence.json').read_text()
    finally:
        server.should_exit=True;thread.join(timeout=10);sock.close();assert not thread.is_alive()
