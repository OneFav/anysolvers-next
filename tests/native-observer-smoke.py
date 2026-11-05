"""Actual native DSH observer sessions/tools; deterministic records, ZERO model/research/Feishu calls."""
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
from controlplane.host import patch
from controlplane.processes import atomic


def main():
    source=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='as-observer-native-') as temp:
        root=Path(temp)
        config=dict(_base=str(root),observer=dict(enabled=True,state_root='observer'),operator_token_env='AS_OBSERVER_FIXTURE_OPERATOR')
        engine=Engine(Store(root/'router.db'),config)
        o=engine.observer
        o.register_job(dict(id='fixture-A',stage='research',state='running',project_id='fixture',host_id='dev'))
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(engine,config,source/'controlplane/web'))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        results=[]
        try:
            options=dict(provider='deepseek-official',model='deepseek-flash',reasoning_effort='high')
            for iteration in range(2):
                # A new native session takes over the same logical observer state.
                session=o.open_session('default')
                content='Deterministic native tool fixture '+str(iteration)
                o.ingest([dict(job_id='fixture-A',batch='fixture/001',session='researcher-fixture',position=iteration,
                    kind='analysis',object='message',version=__import__('hashlib').sha256(content.encode()).hexdigest(),
                    content=content,at=1)])
                # Use size trigger to avoid sleeping in a process/tool boundary test.
                o.options['max_events']=1
                state=root/str(iteration);state.mkdir()
                manifest=state/'launch.json'
                atomic(manifest,dict(role='observer',observer_id='default',session=session,router_url=f'http://127.0.0.1:{server.server_port}',
                    workspace=str(state),local_state=str(state/'session'),verify_only=True,verify_handover=True,**options))
                overlay=state/'patch.json';atomic(overlay,patch(source,manifest,options,observer=True))
                env=dict(os.environ,DSH_HOME=str(state/'dsh-home'))
                result=subprocess.run([shutil.which('dsh'),'--profile','headless','--patch',str(overlay)],cwd=state,env=env,capture_output=True,text=True,timeout=60)
                verified=Path(str(manifest)+'.verified.json');error=Path(str(manifest)+'.error.json')
                if result.returncode or not verified.exists():
                    raise RuntimeError((error.read_text() if error.exists() else '')+'\n'+result.stdout[-2000:]+'\n'+result.stderr[-2000:])
                proof=json.loads(verified.read_text())
                assert set(proof['tools'])=={'observer_read','observer_commit'},proof
                assert proof['model_called'] is False and proof['observer_read_executed'] and proof['observer_commit_executed'],proof
                assert proof['handover_verified'] and len(proof['native_sessions'])==2,proof
                results.append(proof)
            assert len(o.store.list('observers'))==1
            assert len(o.store.list('observer_commits'))==2
            assert len(o.store.list('observer_reports'))==0
            print(json.dumps(dict(passed=True,actual_dsh_processes=2,actual_native_sessions=4,model_calls=0,sandbox_evaluations=0,feishu_sends=0,checks=results),indent=2))
        finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
