"""Explicit desktop launch: one immutable draft, one research worker, one terminal worker."""
import copy
from .engine import Engine,Conflict,ACTIVE,digest

SOURCE='desktop-owner'
POLICY=dict(chats=['desktop-local'],allowed_users=['desktop-owner'],allowed_stages=['research','terminal'],local_owner=True)

def event(key,text):
    return dict(id=key,source=SOURCE,thread=key,actor='desktop-owner',text=text,
                channel=dict(chat_id='desktop-local',message_id=key),admitted=True,state='routed')

def start(ui,project_id,study_id,config):
    key='desktop:'+digest([project_id,study_id,config])
    engine=Engine(ui.store,ui.config)
    # Check idempotency before validating the draft; it may already have produced a run.
    with ui.store.transaction() as tx:
        previous=tx.get('desktop_workflows',key)
        if previous:return dict(job_id=previous['research_id'],workflow_id=key)
        if any(j['project_id']==project_id and j['stage'] in ('research','terminal') and j['state'] in ACTIVE for j in tx.list('jobs')):
            raise Conflict('此项目仍有活动 Worker，请先等原任务结清。')
        predecessors=[j for j in tx.list('jobs') if j['project_id']==project_id and j['stage']=='initialize' and j['state']=='completed' and (j.get('output') or {}).get('study_id')==study_id]
        if len(predecessors)!=1:raise Conflict('此研究缺少唯一、已完成的初始化交接。')
        profile=ui.call(ui.project(project_id),'ui-launch-profile',{'config':config},study=study_id)
        text=f'按已保存配置 {config} 为 {study_id} 创建一个新批次。研究完成后由独立终局 Worker 完成最终测试、报告及图表；禁止修改任何旧批次和框架。'
        trigger=event(key,text);trigger['received_at']=engine.clock();tx.put('triggers',key,trigger)
        decision=dict(action='create',stage='research',project_id=project_id,predecessor_id=predecessors[0]['id'],title=study_id+' · UI 新批次',scope=text,reason='Authenticated desktop owner clicked Start')
        job=engine._create(tx,trigger,decision)
        job['handoff']=dict(copy.deepcopy(job['handoff']),config=config,desktop_config=dict(config=config,sha256=profile['config_sha'],previous_runs=profile['previous_runs']))
        tx.put('jobs',job['id'],job)
        workflow=dict(id=key,project_id=project_id,study_id=study_id,research_id=job['id'],terminal_id=None,state='research',authorized=True,created_at=engine.clock())
        tx.put('desktop_workflows',key,workflow)
        tx.put('decisions',key,dict(event_id=key,decision=decision,job_id=job['id'],at=engine.clock()))
        engine._event(tx,'desktop.launch',dict(workflow_id=key,job_id=job['id'],config=config))
        return dict(job_id=job['id'],workflow_id=key)

def advance(engine,tx):
    for workflow in tx.list('desktop_workflows'):
        if not workflow.get('authorized') or workflow['state'] in ('completed','stopped'):continue
        research=tx.get('jobs',workflow['research_id'])
        if not research:continue
        if research['state']=='stopped':workflow['state']='stopped'
        elif research['state']=='completed' and not research.get('paused'):
            terminal=tx.get('jobs',workflow.get('terminal_id') or '')
            if not terminal:
                matches=[j for j in tx.list('jobs') if j['stage']=='terminal' and j.get('predecessor_id')==research['id'] and j['state']!='stopped']
                if matches:terminal=sorted(matches,key=lambda j:j['created_at'])[-1]
                elif not any(j['project_id']==research['project_id'] and j['stage']=='terminal' and j['state'] in ACTIVE for j in tx.list('jobs')):
                    key=workflow['id']+':terminal'
                    trigger=event(key,'完成此 UI 已授权批次的最终测试、报告和图表，不创建新研究。');trigger['received_at']=engine.clock()
                    tx.put('triggers',key,trigger)
                    decision=dict(action='create',stage='terminal',project_id=research['project_id'],predecessor_id=research['id'],title=workflow['study_id']+' · UI 终局',scope=trigger['text'],reason='Desktop Start authorized terminal after validated research completion')
                    terminal=engine._create(tx,trigger,decision)
                    tx.put('decisions',key,dict(event_id=key,decision=decision,job_id=terminal['id'],at=engine.clock()))
            if terminal:
                workflow['terminal_id']=terminal['id']
                workflow['state']='completed' if terminal['state']=='completed' else 'stopped' if terminal['state']=='stopped' else 'terminal'
        tx.put('desktop_workflows',workflow['id'],workflow)
