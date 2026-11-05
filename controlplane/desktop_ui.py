"""Fixed owner-only desktop adapter for Next projects. Never exposes tokens or arbitrary operations."""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from .config import load,local_path
from .store import Store
from .engine import Engine,ACTIVE,STAGES
from .runtime_engines import contained,verified_engine,materialize_run_engine

FIELDS={'deployment':set(),'service-start':set(),'service-stop':set(),'projects':set(),'catalog':{'projectId'},'configuration':{'projectId','studyId'},
        'save':{'projectId','studyId','revision','values'},'start':{'projectId','studyId','config'},
        'progress':{'projectId','runId','offset','final_offset'},'artifact':{'projectId','runId','kind','id','asset'}}
OPERATIONS={'catalog':'launch-list','configuration':'ui-configuration','save':'ui-save','progress':'ui-progress','artifact':'terminal-artifact'}

class DesktopUI:
    def __init__(self,config):
        self.config=config;self.root=Path(config['_base']);self.store=Store(local_path(config,config['state_db']))
        self.work=local_path(config,config['worker_host']['work_root'])
        self.cache=self.root/'.state/desktop-ui/engines'
        self.python=str(Path(config['worker_host']['python']).expanduser().absolute())

    def jobs(self):return [j for j in self.store.list('jobs') if j['stage'] in STAGES]

    def project(self,identity):
        if not isinstance(identity,str) or not re.fullmatch(r'project-[a-zA-Z0-9_-]+',identity):raise ValueError('Invalid Next project')
        if not any(j['project_id']==identity for j in self.jobs()):raise ValueError('Project is not registered in AnySolvers-next')
        path=contained(self.work,identity)
        if not path.is_dir():raise ValueError('Next project workspace is unavailable')
        return path

    def runs(self,project):
        root=contained(project,'runs')
        if not root.exists():return []
        values=[]
        for study in root.iterdir():
            if not re.fullmatch('[a-z][a-z0-9-]{0,63}',study.name):continue
            contained(root,study.name)
            for batch in study.iterdir():
                if re.fullmatch('[0-9]{3,}',batch.name) and contained(study,batch.name+'/state.json').is_file():values.append(study.name+'/'+batch.name)
        return sorted(values)

    def projects(self):
        values=[]
        for pid in sorted({j['project_id'] for j in self.jobs()}):
            jobs=[j for j in self.jobs() if j['project_id']==pid]
            try:
                path=self.project(pid);runs=self.runs(path)
                studies=sorted({(j.get('output') or {}).get('study_id') for j in jobs if (j.get('output') or {}).get('study_id')})
                def created(run):
                    state=json.loads(contained(path,'runs/'+run+'/state.json').read_text())
                    return state.get('configuration',{}).get('created_at','')
                latest=max(runs,key=lambda r:(created(r),r),default=None)
                values.append(dict(id=pid,name=' / '.join(studies) or pid,root=str(path),latestRun=latest,
                                   latestAt=created(latest) if latest else None,error=None))
            except (ValueError,OSError,KeyError) as exc:
                values.append(dict(id=pid,name=pid,root=str(self.work/pid),latestRun=None,latestAt=None,error=str(exc)))
        values.sort(key=lambda p:(p['latestAt'] or '',p['id']),reverse=True)
        return dict(projects=values,defaultProjectId=values[0]['id'] if values else None)

    def runtime(self,project):
        paths=[local_path(self.config,p) for p in self.config['worker_host']['versions'].values()]
        current=local_path(self.config,self.config['worker_host']['versions'][self.config['default_version']])
        identity=json.loads((current/'lib/runtime/current.json').read_text())['engine_id']
        source=current/'lib/runtime/engines'/identity;manifest=verified_engine(source,identity)
        self.cache.mkdir(parents=True,exist_ok=True);target=contained(self.cache,identity)
        if not target.exists():
            staging=self.cache/('.install-'+uuid.uuid4().hex);staging.mkdir()
            try:
                for relative in [*manifest['files'],'manifest.json']:
                    dest=contained(staging,relative);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(contained(source,relative),dest)
                verified_engine(staging,identity)
                try:staging.rename(target)
                except OSError:
                    if not target.exists():raise
            finally:shutil.rmtree(staging,ignore_errors=True)
        verified_engine(target,identity)
        sources=[p/'lib/runtime/engines' for p in paths]
        for run in self.runs(project):materialize_run_engine(project,run,self.cache,sources)
        return current,identity

    def call(self,project,operation,payload=None,study=None,run=None):
        if operation not in {*OPERATIONS.values(),'ui-launch-profile'}:raise ValueError('Unsupported UI operation')
        current,identity=self.runtime(project)
        request=dict(schema_version=1,operation=operation,payload=payload or {})
        if study:request['study_id']=study
        if run:request['run_id']=run
        env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG')}
        env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(current/'packages/research-runtime/python'))
        result=subprocess.run([self.python,'-B',str(current/'lib/runtime/dispatch.py'),str(project),str(self.cache),identity],
                              input=json.dumps(request),capture_output=True,text=True,env=env,timeout=45,cwd=project)
        try:reply=json.loads(result.stdout)
        except ValueError:raise ValueError('Runtime returned an incomplete UI response')
        if not reply.get('ok'):raise ValueError(reply.get('error',{}).get('message','Runtime UI request failed'))
        return reply['value']

    def request(self,method,payload):
        if method not in FIELDS or not isinstance(payload,dict) or set(payload)-FIELDS[method]:raise ValueError('Unknown UI operation or field')
        if method in ('deployment','service-start','service-stop'):
            from .deployment import service
            if method!='deployment':return service(method.removeprefix('service-'),self.config['_path'])
            value=service('status',self.config['_path'])
            value.update(version=self.config['default_version'],chats=[dict(label=c.get('label',''),chat_id=c['chat_id']) for c in self.config['feishu']['chats']],
                source_health=self.store.list('source_health'),instance=str(self.root))
            return value
        if method=='projects':return self.projects()
        project=self.project(payload.get('projectId'));study=payload.get('studyId');run=payload.get('runId')
        if method in ('configuration','save','start') and (not isinstance(study,str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}',study)):raise ValueError('Invalid study')
        if method in ('progress','artifact') and (not isinstance(run,str) or run not in self.runs(project)):raise ValueError('Unknown Next batch')
        args={k:v for k,v in payload.items() if k not in ('projectId','studyId','runId')}
        if method=='start':
            from .desktop_workflows import start
            return start(self,payload['projectId'],study,args['config'])
        if method=='save' and any(j['project_id']==payload['projectId'] and j['stage'] in ('research','terminal') and j['state'] in ACTIVE for j in self.jobs()):
            raise ValueError('此项目仍有进行中的 Worker；请等任务结清后保存下一批次配置。')
        value=self.call(project,OPERATIONS[method],args,study,run)
        if method=='catalog':value['root']=str(project)
        if method=='progress':
            jobs=[j for j in self.jobs() if j['project_id']==payload['projectId'] and ((j.get('handoff') or {}).get('run_id')==run or (j.get('output') or {}).get('run_id')==run or j['stage']=='research' and j['state'] in ACTIVE and (j.get('handoff') or {}).get('study_id')==run.split('/')[0])]
            incidents=self.store.list('incidents')
            value['workers']=[dict(id=j['id'],stage=j['stage'],state='recovering' if j['state']=='host_lost' and (j.get('connection_recovery') or {}).get('phase') in ('verifying','waiting_operations') else j['state'],detail=(j.get('connection_recovery') or {}).get('detail','') if j['state']=='host_lost' else '；'.join(i['message'] for i in incidents if i['job_id']==j['id'] and i['state']=='open')) for j in sorted(jobs,key=lambda j:j['created_at'])]
        return value

def main():
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('UI request too large')
        request=json.loads(raw)
        import argparse
        parser=argparse.ArgumentParser();parser.add_argument('--config',default=str(Path(__file__).resolve().parents[1]/'config/settings.json'))
        result=DesktopUI(load(parser.parse_args().config)).request(request['method'],request['payload'])
        print(json.dumps(dict(ok=True,value=result),ensure_ascii=False))
    except Exception as exc:print(json.dumps(dict(ok=False,error=dict(message=str(exc)[:1600])),ensure_ascii=False))

if __name__=='__main__':main()
