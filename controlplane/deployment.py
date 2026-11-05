"""Independent process supervisor for agent-installed instances, on POSIX and Windows."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from .config import load, local_path
from .processes import atomic, birth, host_lock

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return {}

def live(value):return bool(value.get('birth')) and birth(value.get('pid')) == value['birth']

def detached():
    return {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}

def service(action, config):
    config=Path(config).resolve();cfg=load(config)
    root=local_path(cfg,'.state/services');root.mkdir(parents=True,exist_ok=True)
    status=read(root/'status.json')
    if action=='status':
        alive=live(status)
        return dict(running=alive,state=status.get('state','stopped') if alive else 'stopped',
            components={k:live(v) for k,v in status.get('children',{}).items()},
            mode=cfg.get('deployment',{}).get('mode','standalone'),port=cfg['port'])
    if action=='stop':
        if not live(status):return dict(stopped=True)
        (root/'stop').touch(mode=0o600)
        for _ in range(150):
            if not live(status):return dict(stopped=True, research_deleted=False)
            time.sleep(.2)
        raise ValueError('Stop is pending; inspect status before starting another instance')
    if live(status):return service('status',config)
    if not cfg['worker_host'].get('versions',{}).get(cfg['default_version']):
        raise ValueError('Seal and register the selected version before starting services')
    # Lock startup as well as supervision: concurrent agents must not create two supervisors.
    with host_lock(root/'start.lock'):
        if live(read(root/'status.json')):return service('status',config)
        with (root/'supervisor.log').open('ab') as log:
            child=subprocess.Popen([cfg['worker_host']['python'],'-B','-m','controlplane.deployment','--config',str(config)],
                cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,**detached())
        for _ in range(160):
            current=read(root/'status.json')
            if current.get('pid')==child.pid and current.get('state')=='running':return service('status',config)
            if child.poll() is not None:raise ValueError('Startup failed; inspect '+str(root/'status.json'))
            time.sleep(.25)
    raise ValueError('Startup pending; inspect status before retrying')

def supervise(config):
    cfg=load(config);root=local_path(cfg,'.state/services');root.mkdir(parents=True,exist_ok=True)
    if os.name!='nt':os.chmod(root,0o700)
    mode=cfg.get('deployment',{}).get('mode','standalone')
    roles={'standalone':['serve','router','host'],'control':['serve','router'],'host':['host']}[mode]
    with host_lock(root/'supervisor.lock'):
        (root/'stop').unlink(missing_ok=True)
        status=dict(pid=os.getpid(),birth=birth(os.getpid()),state='starting',children={})
        atomic(root/'status.json',status)
        env=dict(os.environ);tokens=read(root/'tokens.json')
        required=([cfg['worker_host']['token_env']] if mode=='host' else [cfg['operator_token_env'],*[h['token_env'] for h in cfg['hosts'].values()]])
        for key in required:
            if not env.get(key):
                if mode=='host' and not tokens.get(key):raise ValueError('Provide the matching execution-node token in environment '+key)
                env[key]=tokens.setdefault(key,secrets.token_urlsafe(32))
        atomic(root/'tokens.json',tokens)
        children={};stopping=False
        def stop(*_):
            nonlocal stopping
            stopping=True
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try:
            for role in roles:
                with (root/(role+'.log')).open('ab') as log:
                    child=subprocess.Popen([cfg['worker_host']['python'],'-B','-m','controlplane',role,'--config',str(config)],
                        cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,**detached())
                children[role]=child;status['children'][role]=dict(pid=child.pid,birth=birth(child.pid));atomic(root/'status.json',status)
                if role=='serve':
                    url='http://127.0.0.1:'+str(cfg['port'])+'/api/state'
                    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+env[cfg['operator_token_env']]})
                    for _ in range(100):
                        if child.poll() is not None:raise ValueError('API exited')
                        try:
                            with urllib.request.urlopen(request,timeout=1) as response:response.read()
                            break
                        except OSError:time.sleep(.2)
                    else:raise ValueError('API readiness timeout')
            # Process readiness is distinct from model/provider and Feishu readiness.
            time.sleep(1)
            if any(c.poll() is not None for c in children.values()):raise ValueError('Component exited during startup')
            status['state']='running';atomic(root/'status.json',status)
            while not stopping and not (root/'stop').exists():
                if any(c.poll() is not None for c in children.values()):raise ValueError('Component exited; durable state retained')
                time.sleep(.3)
        except Exception as exc:
            status.update(state='failed',error=str(exc));raise
        finally:
            for child in reversed(list(children.values())):
                if child.poll() is None:
                    if os.name=='nt':subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    else:os.killpg(child.pid,signal.SIGTERM)
            for child in children.values():
                try:child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    if os.name=='nt':
                        subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    else:os.killpg(child.pid,signal.SIGKILL)
                    child.wait(timeout=5)
            if status['state']!='failed':status['state']='stopped'
            atomic(root/'status.json',status)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True)
    supervise(Path(p.parse_args().config).resolve())
