"""Persistent process host. One lock, one launch receipt, one native identity per job."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from .client import Client
from .config import local_path, executable, command
from .processes import atomic, birth, host_lock
from .versions import content_digest
from .runtime_engines import materialize_run_engine


def read(path, default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:return default


def recovered_run_binding(job, committed_run):
    """Honor a trusted, operator-audited backend migration across host restarts."""
    migration=job.get('execution_migration')
    if not migration:return committed_run
    import re
    before,after=migration.get('from_run',''),migration.get('to_run','')
    if migration.get('reason')!='explicit_user_requested_dsh_native_backend' or not all(re.fullmatch(r'[a-z][a-z0-9-]{0,63}/[0-9]{3,}',v) for v in (before,after)):
        raise ValueError('Invalid explicit execution migration')
    if before.split('/')[0]!=after.split('/')[0] or committed_run not in (before,after):
        raise ValueError('Execution migration cannot adopt an unrelated batch')
    return after


def native_id(job_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'anysolvers-next:'+job_id))


def native_heartbeat(directory,job_id,now=None):
    """Host attestation is independent of model messages and wrapper liveness."""
    now=time.time() if now is None else now
    try:
        child=read(Path(directory)/'launch.json.child.json',{})
        heartbeat=read(Path(directory)/'session/heartbeat.json',{})
        marker=read(Path(directory)/'session/native.json',{})
        pid=child.get('pid');expected=native_id(job_id)
        if not child.get('birth') or birth(pid)!=child['birth']:return dict(native_alive=False)
        if heartbeat.get('pid')!=pid or heartbeat.get('native_id')!=expected or marker.get('native_id')!=expected:return dict(native_alive=False)
        age=now-heartbeat['at']/1000
        if age<0:return dict(native_alive=False)
        return dict(native_alive=True,native_pid=pid,native_id=expected,native_heartbeat_age_s=age,agent_status=heartbeat.get('agent_status'))
    except (ValueError,TypeError,KeyError,OSError):return dict(native_alive=False)


def patch(implementation, manifest, options, router=False, framework=False, observer=False):
    if framework:raise ValueError('Framework workers are disabled')
    implementation=Path(implementation).resolve()
    def plugin(name, **config):
        return dict(id='next-'+name,name=str(implementation/'lib/plugins'/f'{name}.js'),**({'config':config} if config else {}))
    role='anysolvers-observer' if observer else 'anysolvers-router' if router else 'anysolvers-main'
    entries=[dict(id='next-preset-registry',name='@deepseek-ai/dsh-agent-preset-registry',config={'default':role})]
    if not router and not observer:
        entries += [dict(id='research-runtime',name=str(implementation/'packages/research-runtime/lib/service.js'),config=dict(stateRoot=str(Path(manifest).parent/'runtime'))),
            plugin('study-runtime',python=options['python'],stateRoot=str(Path(manifest).parent/'engine-cache')),
            plugin('study-sandbox',backend='dsh-native',python=options['python']),
            plugin('study-sessions',provider=options['provider']),plugin('study-policy'),plugin('research-method')]
    roles=['anysolvers-observer'] if observer else ['anysolvers-router'] if router else ['anysolvers-main','anysolvers-researcher']
    for role in roles:
        plugins=[]
        if role=='anysolvers-main':
            plugins=[plugin('tool-study'),plugin('tool-final'),plugin('tool-visualization')]
        elif role=='anysolvers-researcher':plugins=[plugin('tool-solver')]
        entries.append(dict(id='preset-'+role,name='@deepseek-ai/dsh-agent-preset',config=dict(id=role,name=role,plugins=plugins)))
    entries.append(plugin('observer-runner' if observer else 'next-runner',manifest=str(manifest)))
    state=Path(manifest).parent/'dsh-sessions'
    inherited=[]
    if options.get('dsh_credentials_home'):
        credentials_home=Path(options['dsh_credentials_home']).expanduser()
        if not credentials_home.is_absolute():raise ValueError('dsh_credentials_home must be absolute')
        inherited=[dict(id='credentials',disabled=False,config=dict(dshHome=str(credentials_home),watch=True))]
        if options['provider']=='deepseek-account':
            inherited += [dict(id='authorization',disabled=False),dict(id='deepseek-account',disabled=False)]
    return inherited+[dict(id=id,disabled=True) for id in ('headless-runner','headless-startup','session-title-llm','session-telemetry-otel')]+[
        dict(id='session-persistence-jsonl',config=dict(root=str(state))),dict(insert=entries)]


class Host:
    def __init__(self,config):
        self.config=config;self.options=dict(config['worker_host']);self.id=self.options['id']
        # Python detects a virtual environment from the invoked executable path.
        # realpath/resolve would dereference venv/bin/python into the base runtime.
        self.options['python']=executable(config,self.options['python'])
        if self.options.get('dsh_command'):self.options['dsh_command']=command(config,self.options['dsh_command'])
        if self.options.get('dsh_credentials_home'):
            self.options['dsh_credentials_home']=str(local_path(config,self.options['dsh_credentials_home']))
        self.root=local_path(config,self.options['state_root']);self.root.mkdir(parents=True,exist_ok=True)
        self.work=local_path(config,self.options['work_root']);self.work.mkdir(parents=True,exist_ok=True)
        self.client=Client(self.options['router_url'],os.environ[self.options['token_env']])
        self.children={}

    def post(self,name,body):
        return self.client.request('/api/host/'+name,dict(host_id=self.id,**body))['value']

    def version(self,version_id,digest):
        value=self.options.get('versions',{}).get(version_id)
        if not value:raise ValueError('No local path configured for pinned version '+version_id)
        path=local_path(self.config,value)
        if content_digest(path)!=digest:raise ValueError('Pinned implementation content changed')
        if not (path/'lib/plugins/next-runner.js').is_file():raise ValueError('Build this implementation first')
        return path

    def prepare_engines(self,job,directory):
        if job['stage'] not in ('research','terminal'):return
        if job['stage']=='terminal' and not (job.get('handoff') or {}).get('run_id'):raise ValueError('Terminal host launch requires the complete handed-off run')
        bound=read(directory/'session/run.json',{})
        runs={v for v in [(job.get('handoff') or {}).get('run_id'),bound.get('run_id'),(job.get('execution_migration') or {}).get('to_run')] if v}
        if not runs:return
        sources=[]
        predecessor=job.get('predecessor_id')
        if predecessor and __import__('re').fullmatch(r'job-[a-zA-Z0-9_-]+',predecessor):sources.append(self.root/predecessor/'engine-cache/engines')
        sources.extend(local_path(self.config,p)/'lib/runtime/engines' for p in self.options.get('versions',{}).values())
        for run_id in sorted(runs):materialize_run_engine(self.work/job['project_id'],run_id,directory/'engine-cache/engines',sources)

    def launch(self,job):
        if job['stage'] not in ('initialize','research','terminal'):raise ValueError('Framework workers are disabled')
        directory=self.root/job['id'];directory.mkdir(parents=True,exist_ok=True)
        receipt=read(directory/'launch.json')
        if receipt and receipt.get('attempt',{}).get('id')==job.get('attempt_id') and job['state']!='queued':
            # Never infer a failed spawn from absent PID; the wrapper may not yet have committed its marker.
            return
        implementation=self.version(job['version_id'],job['version_digest'])
        self.prepare_engines(job,directory)
        accepted=self.post('claim',dict(job_id=job['id']))
        job=accepted['job'];attempt=accepted['attempt'];version=accepted['version']
        implementation=self.version(job['version_id'],version['digest'])
        workspace=self.work/job['project_id']
        workspace.mkdir(parents=True,exist_ok=True)
        local=directory/'session';local.mkdir(exist_ok=True)
        manifest=dict(role='worker',job=job,attempt=attempt,router_url=self.options['router_url'],
            native_id=native_id(job['id']),workspace=str(workspace),local_state=str(local),implementation=str(implementation),
            verify_only=self.options.get('verify_only',False),python=self.options['python'],provider=self.options['provider'],model=self.options['model'],
            reasoning_effort=self.options.get('reasoning_effort','high'),retry_ms=self.options.get('retry_ms',60000))
        path=directory/(attempt['id']+'.json');atomic(path,manifest)
        overlay=directory/(attempt['id']+'.patch.json');atomic(overlay,patch(implementation,path,self.options))
        # Durable intention precedes spawn. A wrapper writes its actual PID before it starts DSH.
        receipt=dict(job_id=job['id'],attempt=attempt,manifest=str(path),patch=str(overlay),implementation=str(implementation),pid=None,birth=None)
        for suffix in ('.pid.json','.child.json','.exit.json'):
            (directory/('launch.json'+suffix)).unlink(missing_ok=True)
        atomic(directory/'launch.json',receipt)
        cmd=[sys.executable,'-B','-m','controlplane.host','child',str(directory/'launch.json')]
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',AS_NEXT_DSH_COMMAND=json.dumps(self.options['dsh_command']))
        kwargs=dict(cwd=str(Path(__file__).resolve().parents[1]),env=env,stdin=subprocess.DEVNULL)
        if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS
        else:kwargs['start_new_session']=True
        with (directory/'host.log').open('ab') as log:
            child=subprocess.Popen(cmd,stdout=log,stderr=log,**kwargs)
        self.children[job['id']]=child

    def recover(self,job,receipt):
        manifest=read(receipt['manifest']);local=Path(manifest['local_state'])
        marker=read(local/'native.json')
        if not marker or marker['native_id']!=manifest['native_id']:return
        self.version(job['version_id'],job['version_digest'])
        # Local results are accepted only if fully fsynced by the trusted middleware.
        resolved=[]
        for file in (local/'tools').glob('*.json'):
            tool=read(file)
            if tool['state']=='completed' and tool['name']=='batch_init' and tool.get('result',{}).get('value',{}).get('run_id'):
                atomic(local/'run.json',dict(run_id=recovered_run_binding(job,tool['result']['value']['run_id'])))
            if tool['state']=='completed':resolved.append(dict(id=job['id']+':'+tool['id'],fingerprint=tool['fingerprint'],result=tool['result'],local_result_sha=__import__('hashlib').sha256(file.read_bytes()).hexdigest()))
        old_child=read(str(self.root/job['id']/'launch.json')+'.child.json')
        if old_child and old_child.get('birth') and birth(old_child['pid'])==old_child['birth']:raise ValueError('Old DSH PID is still present')
        for lock in Path(manifest['workspace']).glob('runs/*/*/*/controller.lock'):
            owner=read(lock)
            if not old_child or owner.get('pid')!=old_child['pid']:raise ValueError('Unknown controller lock owner')
            lock.unlink()
        value=self.post('recover',dict(job_id=job['id'],evidence=dict(attempt_id=receipt['attempt']['id'],native_session_persisted=True,implementation_matches=True,resolved_operations=resolved)))
        self.launch(value)

    def tick(self):
        workers=[];receipts={}
        for file in self.root.glob('*/launch.json'):
            receipt=read(file);receipts[receipt['job_id']]=receipt
            process=self.children.get(receipt['job_id'])
            if process:process.poll() # Reap exited direct children so zombies cannot look live.
            marker=read(str(file)+'.pid.json')
            if marker:
                wrapper_alive=birth(marker['pid'])==marker['birth']
                child=read(str(file)+'.child.json')
                child_alive=birth(child['pid'])==child['birth'] if child else None
                alive=True if wrapper_alive or child_alive else False if child else None
                # The wrapper is alive but its DSH child may have stopped already.
                if read(str(file)+'.exit.json') and child_alive is not True:alive=False
                workers.append(dict(attempt_id=receipt['attempt']['id'],alive=alive,pid=marker['pid'],**(native_heartbeat(file.parent,receipt['job_id']) if alive else {})))
            else:
                workers.append(dict(attempt_id=receipt['attempt']['id'],alive=None))
        jobs=self.post('heartbeat',dict(workers=workers))
        live=sum(w['alive'] is not False for w in workers)
        for job in jobs:
            if job['paused']:
                continue
            if job['state'] in ('queued','starting') and live<self.options.get('max_workers',3):
                self.launch(job);live+=1
            elif job['state'] in ('reconciliation','host_lost','waiting_provider'):
                receipt=receipts.get(job['id'])
                old=next((w for w in workers if receipt and w['attempt_id']==receipt['attempt']['id']),None)
                if old and old['alive'] is False and not job['paused']:
                    try:self.recover(job,receipt)
                    except (ValueError,OSError):pass # Unknown side effects are never replayed.

    def serve(self):
        with host_lock(self.root/'host.lock'):
            if self.config.get('observer',{}).get('enabled'):
                import threading
                from .observer_collector import Collector
                stop=threading.Event()
                threading.Thread(target=Collector(self.config,self.post).serve,args=(stop,),daemon=True,name='observer-collector').start()
            while True:
                try:self.tick()
                except (ValueError,OSError) as exc:
                    atomic(self.root/'host-error.json',dict(error=str(exc),at=time.time()))
                time.sleep(3)


def child(receipt_path):
    receipt=read(receipt_path);manifest=read(receipt['manifest'])
    atomic(receipt_path+'.pid.json',dict(pid=os.getpid(),birth=birth(os.getpid())))
    env=dict(os.environ,AS_NEXT_MANIFEST=receipt['manifest'],DSH_HOME=str(Path(receipt_path).parent/'dsh-home'))
    command=json.loads(env.pop('AS_NEXT_DSH_COMMAND'))
    if os.name=='nt' and (shutil.which(command[0]) or command[0]).lower().endswith(('.cmd','.bat')):raise ValueError('Use node + DSH entry.js on Windows')
    process=subprocess.Popen(command+['--patch',receipt['patch']],cwd=manifest['workspace'],env=env)
    atomic(receipt_path+'.child.json',dict(pid=process.pid,birth=birth(process.pid)))
    code=process.wait()
    atomic(receipt_path+'.exit.json',dict(code=code,at=time.time()));return code


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='child':sys.exit(child(sys.argv[2]))
    raise SystemExit('Use python -m controlplane host --config ...')
