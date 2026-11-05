"""Opt-in actual DSH process smoke. Creates/resumes sessions without any model request or experiment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from controlplane.store import Store
from controlplane.engine import Engine
from controlplane.server import handler
from controlplane.host import patch,native_id
from controlplane.processes import atomic


def main():
    source=Path(__file__).resolve().parents[1]
    os.environ['AS_NEXT_SMOKE_TOKEN']='smoke-only'
    with tempfile.TemporaryDirectory(prefix='as-next-native-') as temp:
        root=Path(temp)
        config=dict(default_host='dev',default_version='v1',operator_token_env='AS_NEXT_SMOKE_TOKEN',sources=dict(feishu=dict(chats=['oc_test'],allowed_users=['ou_test'],allowed_stages=['initialize'])))
        engine=Engine(Store(root/'router.sqlite3'),config)
        engine.register_version(dict(id='v1',digest='a'*64,state='ready',validation=dict(passed=True,checks=['fixture-only'])))
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(engine,config,source/'controlplane/web'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        options=dict(python=sys.executable,provider='deepseek-official',model='deepseek-flash',reasoning_effort='high')
        outcomes=[]
        try:
            for role in ['router','initialize']:
                state=root/role;state.mkdir();workspace=state/'workspace';workspace.mkdir()
                manifest=state/'launch.json'
                value=dict(role='router' if role=='router' else 'worker',router_url=f'http://127.0.0.1:{server.server_port}',token_env='AS_NEXT_SMOKE_TOKEN',native_id=native_id('smoke-'+role),workspace=str(workspace),local_state=str(state/'session'),implementation=str(source),verify_only=True,**options)
                if role=='router':
                    engine.receive(dict(id='router-smoke',source='feishu',thread='oc_test:probe',actor='ou_test',text='connectivity test',channel=dict(chat_id='oc_test',message_id='om_router_smoke')))
                    value['verify_event_id']='router-smoke'
                else:
                    engine.receive(dict(id='smoke',source='feishu',thread='oc_test:smoke',actor='ou_test',text='initialize',channel=dict(chat_id='oc_test',message_id='om_smoke')))
                    result=engine.decide('smoke',dict(action='create',stage='initialize',title='native smoke',scope='no model',reason='test fixture'))
                    receipt=engine.claim('dev',result['job_id']);value.update(job=receipt['job'],attempt=receipt['attempt'])
                atomic(manifest,value);overlay=state/'patch.json';atomic(overlay,patch(source,manifest,options,router=role=='router'))
                for resume in (False,True):
                    # Workers started under the same attempt cannot change PID. A cold restart uses a new fenced attempt.
                    if resume and role!='router':
                        old=value['attempt'];engine.host_heartbeat('dev',[dict(attempt_id=old['id'],alive=False)])
                        engine.recover_attempt('dev',value['job']['id'],dict(attempt_id=old['id'],native_session_persisted=True,implementation_matches=True))
                        receipt=engine.claim('dev',value['job']['id']);value.update(job=receipt['job'],attempt=receipt['attempt']);atomic(manifest,value)
                    env=dict(os.environ,DSH_HOME=str(state/'dsh-home'),AS_NEXT_SMOKE_TOKEN='smoke-only',AS_NEXT_MANIFEST=str(manifest))
                    command=[shutil.which('dsh'),'--profile','headless','--patch',str(overlay)]
                    result=subprocess.run(command,cwd=workspace,env=env,capture_output=True,text=True,timeout=60)
                    error=Path(str(manifest)+'.error.json')
                    if result.returncode!=0 or not Path(str(manifest)+'.verified.json').exists():raise RuntimeError((error.read_text() if error.exists() else '')+'\n'+result.stdout[-3000:]+'\n'+result.stderr[-3000:])
                    evidence=json.loads(Path(str(manifest)+'.verified.json').read_text())
                    assert evidence['resumed']==resume and evidence['model_called'] is False,evidence
                    assert evidence['prompt_rendered'] is True,evidence
                    if role=='router':
                        assert set(evidence['tools'])=={'router_decide'},evidence
                        assert evidence['router_tool_executed'] is True,evidence
                        assert engine.store.get('decisions','router-smoke')['decision']['action']=='reply'
                        assert len(engine.store.list('outbox'))==1,'Native tool replay duplicated the acknowledgement'
                        assert not engine.store.list('jobs'),'A connectivity probe created a worker'
                    else:
                        assert 'study_init' in evidence['tools'] and 'final_measure' not in evidence['tools'] and 'research_start' not in evidence['tools'],evidence
                        assert evidence['initialization_registered'] and evidence['progress_tool_executed'] and evidence['module_reads']==8,evidence
                    outcomes.append(dict(role=role,**evidence))
            print(json.dumps(dict(passed=True,actual_dsh_processes=4,model_requests=0,checks=outcomes),ensure_ascii=False,indent=2))
        finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
