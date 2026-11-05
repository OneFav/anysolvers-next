"""Automatic observer process supervision, independent of stage workers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .config import local_path
from .host import patch, child, read
from .processes import atomic, birth, host_lock


class Supervisor:
    def __init__(self, engine, source):
        self.engine, self.observer = engine, engine.observer
        self.config = engine.config
        self.source = Path(source).resolve()
        self.root = local_path(self.config, self.config.get('observer',{}).get('state_root','.state/observer'))
        self.children = {}

    def tick(self):
        for job in self.engine.store.list('jobs'):
            if job['stage'] == 'research':
                self.observer.register_job(job)
        for logical in self.observer.store.list('observers'):
            oid=logical['id'];directory=self.root/oid;directory.mkdir(parents=True,exist_ok=True)
            receipt_path=directory/'launch.json'
            receipt=read(receipt_path)
            process=self.children.get(oid)
            if process:process.poll()
            if receipt:
                wrapper=read(str(receipt_path)+'.pid.json')
                native=read(str(receipt_path)+'.child.json')
                exited=read(str(receipt_path)+'.exit.json')
                if any(p and p.get('birth') and birth(p['pid'])==p['birth'] for p in (wrapper,native)):
                    continue
                if not exited and not wrapper:
                    # A crash between spawn intention and PID receipt cannot prove absence.
                    continue
            options=dict(self.config['router_agent'], **self.config.get('observer',{}).get('agent',{}))
            session=self.observer.open_session(oid)
            manifest=directory/(session['id']+'.json')
            atomic(manifest,dict(role='observer',observer_id=oid,session=session,router_url=options['router_url'],
                workspace=str(directory),local_state=str(directory/'session'),provider=options['provider'],model=options['model'],
                reasoning_effort=options.get('reasoning_effort','high'),retry_ms=options.get('retry_ms',60000),
                max_context_bytes=self.observer.options['max_session_bytes']))
            overlay=directory/(session['id']+'.patch.json');atomic(overlay,patch(self.source,manifest,options,observer=True))
            for suffix in ('.pid.json','.child.json','.exit.json'):
                Path(str(receipt_path)+suffix).unlink(missing_ok=True)
            atomic(receipt_path,dict(manifest=str(manifest),patch=str(overlay),observer_id=oid,session_id=session['id']))
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',AS_NEXT_DSH_COMMAND=json.dumps(options['dsh_command']))
            with (directory/'process.log').open('ab') as log:
                self.children[oid]=subprocess.Popen([sys.executable,'-B','-m','controlplane.observer_runtime','child',str(receipt_path)],
                    cwd=self.source,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=os.name!='nt')

    def serve(self, stop):
        self.root.mkdir(parents=True,exist_ok=True)
        with host_lock(self.root/'supervisor.lock'):
            while not stop.is_set():
                try:self.tick()
                except (OSError,ValueError):
                    atomic(self.root/'error.json',dict(at=time.time(),detail='Observer supervision failed; evidence remains queued'))
                stop.wait(3)


def start(engine, source, stop):
    if not engine.observer:
        return
    supervisor=Supervisor(engine,source)
    threading.Thread(target=supervisor.serve,args=(stop,),daemon=True,name='observer-supervisor').start()


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='child':
        raise SystemExit(child(sys.argv[2]))
    raise SystemExit('Observer is automatically supervised by controlplane serve')
