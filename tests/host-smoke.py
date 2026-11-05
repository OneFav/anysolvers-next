"""Actual API -> Host -> wrapper -> DSH, then a replacement Host resumes the same session. No model calls."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from controlplane.store import Store
from controlplane.engine import Engine
from controlplane.server import handler
from controlplane.host import Host,read
from controlplane.client import Client
from controlplane.versions import content_digest


def main():
    source=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='as-next-host-') as temp:
        root=Path(temp);os.environ['AS_HOST_SMOKE_OP']='local-operator';os.environ['AS_HOST_SMOKE_NODE']='local-host'
        cfg=dict(_base=str(source),default_host='dev',default_version='v1',operator_token_env='AS_HOST_SMOKE_OP',
            hosts=dict(dev=dict(token_env='AS_HOST_SMOKE_NODE')),sources=dict(feishu=dict(chats=['oc_test'],allowed_users=['ou_test'],allowed_stages=['initialize'])))
        engine=Engine(Store(root/'router.sqlite3'),cfg)
        engine.register_version(dict(id='v1',digest=content_digest(source),state='ready',validation=dict(passed=True,checks=['fixture-for-transport-test'])))
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(engine,cfg,source/'controlplane/web'));threading.Thread(target=server.serve_forever,daemon=True).start()
        url=f'http://127.0.0.1:{server.server_port}'
        cfg['worker_host']=dict(id='dev',router_url=url,token_env='AS_HOST_SMOKE_NODE',work_root=str(root/'projects'),state_root=str(root/'host'),max_workers=1,
             versions=dict(v1=str(source)),python=sys.executable,dsh_command=[shutil.which('dsh'),'--profile','headless'],provider='deepseek-official',model='deepseek-flash',verify_only=True)
        children=[]
        try:
            client=Client(url,'local-operator')
            client.request('/api/receive',dict(id='human',source='feishu',thread='oc_test:thread',actor='ou_test',text='初始化研究',channel=dict(chat_id='oc_test',message_id='om_test')))
            value=client.request('/api/decide',dict(event_id='human',decision=dict(action='create',stage='initialize',title='真实进程协议验证',scope='no model',reason='deterministic test assessor')))['value']
            job_id=value['job_id'];previous=None;proof=[]
            for generation in (1,2):
                host=Host(cfg);host.tick()
                child=host.children.get(job_id);assert child,'Host did not start the worker'
                children.append(child);child.wait(timeout=40)
                receipt=read(root/'host'/job_id/'launch.json');manifest=receipt['manifest']
                result=read(manifest+'.verified.json')
                if not result:raise AssertionError((read(manifest+'.error.json') or {}) | {'log':(root/'host'/job_id/'host.log').read_text()[-5000:]})
                assert result['resumed']==(generation==2)
                assert result['model_called'] is False
                assert result['initialization_tool_executed'] is True
                assert read(manifest)['python']==str(Path(sys.executable).absolute()),'Host escaped the selected Python environment'
                if previous:assert result['native_id']==previous
                previous=result['native_id'];proof.append(dict(generation=generation,native_id=previous,resumed=result['resumed']))
            assert engine.store.get('jobs',job_id)['generation']==2
            print(json.dumps(dict(passed=True,actual_dsh_processes=2,actual_wrapper_processes=2,model_requests=0,checks=proof),indent=2))
        finally:
            for child in children:
                if child.poll() is None:child.terminate();child.wait(timeout=10)
            server.shutdown();server.server_close()

if __name__=='__main__':main()
