import concurrent.futures
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from controlplane.engine import Engine,Conflict,digest
from controlplane.store import Store
from controlplane.feishu import Feishu,normalize
from controlplane.server import handler
from controlplane.client import Client
from controlplane.processes import birth
from controlplane.versions import content_digest,seal


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1000
        self.config=dict(default_host='dev',default_version='v1',host_timeout_s=10,
            sources=dict(feishu=dict(chats=['oc_test'],allowed_users=['ou_owner'],allowed_stages=['initialize','research','terminal','framework'],auto_framework_requests=True)))
        self.store=Store(Path(self.tmp.name)/'router.sqlite3');self.engine=Engine(self.store,self.config,lambda:self.now)
        self.engine.register_version(dict(id='v1',digest='a'*64,state='ready',validation=dict(passed=True,checks=['unit'])))

    def trigger(self,key='m1',**kwargs):
        event=dict(id=key,source='feishu',thread='oc_test:thread',actor='ou_owner',text='请初始化新课题',channel=dict(chat_id='oc_test',message_id='om_'+key))
        event.update(kwargs);return self.engine.receive(event)

    def job(self,key='m1',stage='initialize',**kwargs):
        self.trigger(key)
        decision=dict(action='create',stage=stage,title='Test',scope='test scope',reason='source intent',**kwargs)
        value=self.engine.decide(key,decision);return self.store.get('jobs',value['job_id'])

    def start(self,job):
        receipt=self.engine.claim('dev',job['id']);self.attempt=receipt['attempt'];self.job_id=job['id']
        self.event('start','started',dict(pid=123,native_id='native-stable'))
        return receipt

    def event(self,id,type,data):return self.engine.worker_event(self.attempt['id'],self.attempt['token'],dict(id=id,type=type,data=data))
    def begin(self,id='call-1',name='solver_evaluate',arguments=None):return self.engine.tool_begin(self.attempt['id'],self.attempt['token'],id,name,arguments or dict(id='original'))
    def finish(self,id='call-1',result=None):return self.engine.tool_finish(self.attempt['id'],self.attempt['token'],id,result or dict(score=1))

    def test_duplicate_trigger_decision_claim_and_completion(self):
        job=self.job()
        for _ in range(100):
            self.trigger();self.engine.decide('m1',dict(action='create',reason='same retry'))
        self.assertEqual(len(self.store.list('jobs')),1)
        one=self.engine.claim('dev',job['id']);two=self.engine.claim('dev',job['id'])
        self.assertEqual(one['attempt']['id'],two['attempt']['id'])
        self.attempt=one['attempt'];self.event('start','started',dict(pid=123,native_id='same'))
        proof=dict(validated=True,artifacts=['sealed'])
        self.event('done','completed',proof);self.event('done','completed',proof)
        self.assertEqual(len([x for x in self.store.list('outbox') if x.get('purpose') == 'result']),1)

    def test_failed_research_outcome_and_issue_notify_with_evidence(self):
        initialized=self.job();self.start(initialized)
        self.event('done','completed',dict(validated=True,study_id='study',artifacts=['seal']))
        research=self.job('research',stage='research',project_id=initialized['project_id']);self.start(research)
        self.event('issue','issue',dict(request='baseline executed zero cases: evaluation_wall_budget',evidence=['eval-001']))
        issue=next(m for m in self.store.list('outbox') if m.get('purpose') == 'attention')
        self.assertNotIn('zero cases',issue['text']);self.assertEqual(issue['mention'],'ou_owner')
        self.assertIn('zero cases',self.store.list('framework_requests')[0]['request'])
        self.assertEqual(self.store.list('framework_requests')[0]['evidence'],['eval-001'])
        self.event('done','completed',dict(validated=True,run_id='study/001',outcome='no_valid_candidate',artifacts=['failure evidence']))
        done=next(m for m in self.store.list('outbox') if '未获得完整合法' in m['text'])
        self.assertIn('无法进入最终评测',done['text']);self.assertNotIn('交付已通过',done['text'])
        self.assertEqual(done['mention'],'ou_owner')

    def test_readonly_delivery_uses_current_tooling_and_requires_completed_terminal(self):
        initialized=self.job();self.start(initialized);self.event('done','completed',dict(validated=True,study_id='study',artifacts=['fixture']))
        self.trigger('research');value=self.engine.decide('research',dict(action='create',stage='research',project_id=initialized['project_id'],title='research',scope='fixture',reason='fixture'))
        research=self.store.get('jobs',value['job_id']);self.start(research);self.event('done','completed',dict(validated=True,run_id='study/002',artifacts=['fixture']))
        self.engine.register_version(dict(id='v2',digest='b'*64,state='ready',validation={'passed':True}));self.config['default_version']='v2'
        self.trigger('premature')
        with self.assertRaisesRegex(Conflict,'completed terminal'):self.engine.decide('premature',dict(action='create',stage='terminal',delivery_only=True,project_id=initialized['project_id'],title='show',scope='display',reason='show'))
        self.trigger('terminal');value=self.engine.decide('terminal',dict(action='create',stage='terminal',project_id=initialized['project_id'],title='terminal',scope='fixture',reason='fixture'))
        terminal=self.store.get('jobs',value['job_id']);self.assertEqual(terminal['version_id'],'v1');self.start(terminal);self.event('done','completed',dict(validated=True,run_id='study/002',artifacts=['fixture']))
        self.trigger('show');value=self.engine.decide('show',dict(action='create',stage='terminal',delivery_only=True,project_id=initialized['project_id'],title='show chart',scope='read frozen chart',reason='human request'))
        displayed=self.store.get('jobs',value['job_id']);self.assertTrue(displayed['delivery_only']);self.assertEqual(displayed['version_id'],'v2');self.assertEqual(displayed['handoff']['run_id'],'study/002')

    def test_concurrent_receipt_uniqueness(self):
        self.trigger()
        def decide(_):return self.engine.decide('m1',dict(action='create',stage='initialize',title='same',scope='same',reason='same'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(decide,range(20)))
        self.assertEqual(len({r['job_id'] for r in results}),1)

    def test_resume_waiting_worker_delivers_answer_once_without_repeat_bit(self):
        job=self.job();self.start(job)
        self.event('ask','waiting_input',dict(question='Is the remote ready?'))
        self.trigger('answer',text='Remote is verified ready; continue this job.')
        decision=dict(action='resume',job_id=job['id'],reason='verified recovery')
        self.engine.decide('answer',decision);self.engine.decide('answer',decision)
        current=self.store.get('jobs',job['id'])
        self.assertEqual(current['state'],'running');self.assertFalse(current['resume_requested'])
        answers=[i for i in self.store.list('inputs') if i['state']=='pending' and i['payload'].get('trigger',{}).get('id')=='answer']
        self.assertEqual(len(answers),1);self.assertEqual(answers[0]['payload']['kind'],'followup')
        with self.store.transaction() as tx:
            current['resume_requested']=True;tx.put('jobs',job['id'],current)
        self.event('new-blocker','waiting_input',dict(question='A new independent blocker requires input.'))
        self.assertFalse(self.store.get('jobs',job['id'])['resume_requested'])

    def test_predecessor_resolution_ignores_out_of_scope_handoffs(self):
        initialized=self.job();self.start(initialized)
        self.event('done','completed',dict(validated=True,study_id='study',artifacts=['sealed']))
        hidden=copy.deepcopy(self.store.get('jobs',initialized['id']))
        hidden.update(id='private-local-handoff',owner='desktop-owner',channel=dict(chat_id='desktop-local',message_id='local'))
        with self.store.transaction() as tx:tx.put('jobs',hidden['id'],hidden)
        research=self.job('research',stage='research',project_id=initialized['project_id'])
        self.assertEqual(research['predecessor_id'],initialized['id'])

    def test_explicit_predecessor_still_requires_scope(self):
        initialized=self.job();self.start(initialized)
        self.event('done','completed',dict(validated=True,study_id='study',artifacts=['sealed']))
        hidden=copy.deepcopy(self.store.get('jobs',initialized['id']))
        hidden.update(id='private-local-handoff',owner='desktop-owner',channel=dict(chat_id='desktop-local',message_id='local'))
        with self.store.transaction() as tx:tx.put('jobs',hidden['id'],hidden)
        with self.assertRaisesRegex(ValueError,'outside source scope'):
            self.job('research',stage='research',project_id=initialized['project_id'],predecessor_id=hidden['id'])

    def test_direct_reply_is_durable_idempotent_and_never_creates_worker(self):
        self.trigger('probe',text='@asers 联通测试')
        decision=dict(action='reply',text='测试已收到',reason='connectivity acknowledgement')
        first=self.engine.decide('probe',decision)
        self.assertEqual(first,self.engine.decide('probe',decision))
        self.assertEqual(self.store.list('jobs'),[])
        messages=self.store.list('outbox')
        self.assertEqual(len(messages),1)
        self.assertEqual(messages[0]['text'],'测试已收到')
        self.assertEqual(messages[0]['mention'],'ou_owner')
        self.assertEqual(messages[0]['channel']['message_id'],'om_probe')
        self.trigger('forbidden',actor='ou_unknown')
        with self.assertRaises(ValueError):self.engine.decide('forbidden',decision)

    def test_router_failure_health_is_visible_and_recovers(self):
        self.engine.router_health('error','模型回合失败')
        self.assertEqual(self.engine.snapshot()['source_health'][0]['state'],'error')
        self.engine.router_health('ready','模型恢复')
        self.assertEqual(self.engine.snapshot()['source_health'][0]['state'],'ready')

    def test_source_gate_and_context_no_authority(self):
        job=self.job();self.trigger('intruder',actor='ou_unknown')
        with self.assertRaises(ValueError):self.engine.decide('intruder',dict(action='create',reason='not allowed'))
        self.assertTrue(any(i['payload']['kind']=='context' for i in self.store.list('inputs')))
        self.trigger('echo',bot=True)
        self.assertEqual(self.store.get('triggers','echo')['state'],'filtered')
        self.assertEqual(len(self.store.list('jobs')),1)

    def test_status_uses_persisted_operation_without_invented_progress(self):
        job=self.job();self.start(job)
        text=self.engine.status_text(self.store.get('jobs',job['id']))
        self.assertEqual(text,'「Test」的初始化正在运行。');self.assertNotIn('{}',text)
        self.begin(name='study_write',arguments={'content':'private draft'});self.finish()
        text=self.engine.status_text(self.store.get('jobs',job['id']))
        self.assertIn('准备初始化材料已返回',text);self.assertNotIn('private draft',text)
        self.assertNotIn('更新时间：1000',text)

    def test_routing_existing_followup_and_status(self):
        job=self.job();self.start(job);self.event('ask','waiting_input',dict(question='数据规模？'))
        self.trigger('reply',text='数据规模 100',reply_to='m1')
        context=self.engine.routing_context('reply');self.assertEqual(context['jobs'][0]['id'],job['id'])
        self.engine.decide('reply',dict(action='attach',job_id=job['id'],reason='same thread'))
        self.assertEqual(self.store.get('jobs',job['id'])['state'],'running')
        self.trigger('progress');self.engine.decide('progress',dict(action='status',job_id=job['id'],reason='status question'))
        self.assertEqual(len(self.store.list('jobs')),1)

    def test_stage_handoff_and_immutable_version(self):
        job=self.job(project_id='project-a');self.start(job)
        with self.assertRaises(Conflict):self.job('early',stage='research',project_id='project-a')
        self.event('done','completed',dict(validated=True,study_id='study',artifacts=['seal']))
        research=self.job('research',stage='research',project_id='project-a',version_id='v999')
        self.assertEqual(research['version_id'],'v1');self.assertEqual(research['handoff']['study_id'],'study')
        self.assertEqual(research['version_digest'],'a'*64)

    def test_quota_and_network_faults_deduplicate_without_budget_reset(self):
        job=self.job();self.start(job)
        for index in range(20):self.event(str(index),'fault',dict(kind='provider_quota',message='provider unavailable'))
        self.assertEqual(len(self.store.list('incidents')),1)
        self.assertEqual(len([x for x in self.store.list('outbox') if x['mention']]),1)
        self.event('resume','recovered',dict(verified=True))
        latest=self.store.get('jobs',job['id']);self.assertEqual(latest['generation'],1)
        self.assertEqual(latest['state'],'running');self.assertNotIn('token_budget',latest)

    def test_unknown_tool_blocks_recovery_and_replay(self):
        job=self.job();self.start(job);self.begin()
        self.event('fail','fault',dict(kind='network',message='offline'))
        with self.assertRaises(Conflict):self.event('resume','recovered',dict(verified=True))
        self.engine.host_heartbeat('dev',[dict(attempt_id=self.attempt['id'],alive=False)])
        with self.assertRaises(Conflict):self.engine.recover_attempt('dev',job['id'],dict(attempt_id=self.attempt['id'],native_session_persisted=True,implementation_matches=True))
        self.assertEqual(len(self.store.list('operations')),1)

    def test_completed_local_operation_restored_new_attempt_fences_old(self):
        job=self.job();self.start(job);op=self.begin()
        self.engine.host_heartbeat('dev',[dict(attempt_id=self.attempt['id'],alive=False)])
        resumed=self.engine.recover_attempt('dev',job['id'],dict(attempt_id=self.attempt['id'],native_session_persisted=True,implementation_matches=True,
            resolved_operations=[dict(id=op['id'],fingerprint=op['fingerprint'],result={'score':3},local_result_sha='a'*64)]))
        new=self.engine.claim('dev',job['id'])
        with self.assertRaises(Conflict):self.begin('unsafe-new')
        self.assertEqual(new['job']['generation'],2)
        result=self.engine.tool_begin(new['attempt']['id'],new['attempt']['token'],'call-1','solver_evaluate',dict(id='original'))
        self.assertEqual(result['result'],dict(score=3));self.assertEqual(len(self.store.list('operations')),1)

    def test_recovery_returns_host_handoff_even_though_ui_omits_it(self):
        init=self.job(project_id='p');self.start(init);self.event('end','completed',dict(validated=True,study_id='study',artifacts=['seal']))
        research=self.job('research','research',project_id='p');self.start(research);self.event('end','completed',dict(validated=True,run_id='study/002',artifacts=['settled']))
        terminal=self.job('terminal','terminal',project_id='p');self.start(terminal)
        self.engine.host_heartbeat('dev',[dict(attempt_id=self.attempt['id'],alive=False)])
        resumed=self.engine.recover_attempt('dev',terminal['id'],dict(attempt_id=self.attempt['id'],native_session_persisted=True,implementation_matches=True))
        self.assertEqual(resumed['handoff']['run_id'],'study/002')
        self.assertNotIn('handoff',self.engine.public(resumed))
        from controlplane.host import Host
        host=object.__new__(Host)
        with self.assertRaisesRegex(ValueError,'complete handed-off run'):host.prepare_engines(self.engine.public(resumed),Path(self.tmp.name))

    def test_host_loss_is_unknown_not_stopped_and_restart_persists(self):
        job=self.job();self.start(job)
        self.engine.host_heartbeat('dev',[dict(attempt_id=self.attempt['id'],alive=True)])
        self.now+=20;self.engine.monitor();self.engine.monitor()
        self.assertEqual(self.store.get('jobs',job['id'])['state'],'host_lost')
        rebuilt=Engine(Store(Path(self.tmp.name)/'router.sqlite3'),self.config,lambda:self.now)
        self.assertEqual(rebuilt.snapshot()['jobs'][0]['id'],job['id'])
        self.assertEqual(len(rebuilt.store.list('incidents')),1)

    def test_live_old_process_prevents_second_worker(self):
        job=self.job();self.start(job);self.event('bad','fault',dict(kind='model_crash',message='crash'))
        self.engine.host_heartbeat('dev',[dict(attempt_id=self.attempt['id'],alive=True)])
        with self.assertRaises(Conflict):self.engine.recover_attempt('dev',job['id'],dict(attempt_id=self.attempt['id'],native_session_persisted=True,implementation_matches=True))

    def test_negative_evidence_and_final_boundary(self):
        init=self.job(project_id='p');self.start(init);self.event('end','completed',dict(validated=True,artifacts=['seal']))
        job=self.job('research','research',project_id='p');self.start(job)
        for i in range(6):self.event('eval'+str(i),'evaluation',dict(scope='development',evaluation_id='e'+str(i),dataset='dev',all_valid=False))
        incident=self.store.list('incidents')[0];self.assertEqual(incident['kind'],'negative_results')
        with self.assertRaises(ValueError):self.event('leak','evaluation',dict(scope='final',evaluation_id='f'))
        self.assertEqual(len(self.store.list('incidents')),1)

    def test_framework_stage_is_denied_even_with_legacy_enabled_config(self):
        self.trigger('forbidden')
        with self.assertRaisesRegex(ValueError,'not authorized'):self.engine.decide('forbidden',dict(action='create',stage='framework',title='edit harness',scope='edit',reason='legacy request'))
        self.assertFalse(self.store.list('jobs'))

    def test_legacy_framework_requests_are_report_only_and_cannot_dispatch(self):
        job=self.job();self.start(job)
        self.event('defect','framework_request',dict(request='framework defect',evidence=['log']))
        request=self.store.list('framework_requests')[0]
        self.assertEqual(request['state'],'report_only')
        with self.assertRaisesRegex(Conflict,'disabled'):self.engine.dispatch_framework(request['id'])
        self.assertEqual(len(self.store.list('jobs')),1)
        self.event('issue','issue',dict(request='execution defect',evidence=['log']))
        self.assertEqual(len(self.store.list('jobs')),1)

    def test_framework_host_and_edit_tools_are_disabled(self):
        from controlplane.host import Host,patch
        from controlplane.engineering import operate
        host=object.__new__(Host)
        with self.assertRaisesRegex(ValueError,'disabled'):host.launch(dict(stage='framework'))
        with self.assertRaisesRegex(ValueError,'disabled'):patch('.', '.', {}, framework=True)
        for operation in ('read','write','list','validate'):
            with self.assertRaisesRegex(ValueError,'disabled'):operate(self.tmp.name,operation,{})
        job=self.job();self.start(job)
        with self.store.transaction() as tx:
            job=tx.get('jobs',job['id']);job['stage']='framework';tx.put('jobs',job['id'],job)
        with self.assertRaisesRegex(Conflict,'disabled'):self.begin()
        with self.store.transaction() as tx:
            job=tx.get('jobs',job['id']);job['state']='starting';tx.put('jobs',job['id'],job)
            attempt=tx.get('attempts',self.attempt['id']);attempt['pid']=None;tx.put('attempts',attempt['id'],attempt)
        with self.assertRaisesRegex(Conflict,'disabled'):self.engine.claim('dev',job['id'])

    def test_subscription_checks_chat(self):
        job=self.job();self.start(job)
        with self.assertRaises(ValueError):self.engine.subscribe(self.attempt['id'],self.attempt['token'],'unseen')
        out=self.engine.subscribe(self.attempt['id'],self.attempt['token'],'oc_test:thread')
        self.assertEqual(out['authority'],'context-only')

    def test_http_auth_scope_and_real_process_event(self):
        self.config.update(operator_token_env='AS_NEXT_TEST_OPERATOR',hosts=dict(dev=dict(token_env='AS_NEXT_TEST_HOST')))
        with patch.dict(os.environ,AS_NEXT_TEST_OPERATOR='operator-test',AS_NEXT_TEST_HOST='host-test'):
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.engine,self.config,Path(__file__).parents[2]/'controlplane/web'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            url='http://127.0.0.1:'+str(server.server_port)
            with self.assertRaises(ValueError):Client(url,'wrong').request('/api/state')
            job=self.job();receipt=self.engine.claim('dev',job['id']);attempt=receipt['attempt']
            code="""import json,os,urllib.request
u,t,a=__import__('sys').argv[1:]
b=json.dumps(dict(attempt_id=a,event=dict(id='real',type='started',data=dict(pid=os.getpid(),native_id='os-fixture')))).encode()
r=urllib.request.urlopen(urllib.request.Request(u+'/api/worker/event',b,{'Authorization':'Bearer '+t,'Content-Type':'application/json'}));print(r.status)
"""
            result=subprocess.run([sys.executable,'-c',code,url,attempt['token'],attempt['id']],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.store.get('jobs',job['id'])['state'],'running')
            self.assertTrue(birth(os.getpid()))

    def test_feishu_outbox_retries_same_id_after_transport_failure(self):
        job=self.job();self.start(job)
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            if len(calls)==1:raise OSError('lost acknowledgement')
            return subprocess.CompletedProcess(command,0,stdout='{"code":0,"data":{"message_id":"om_sent"}}')
        adapter=Feishu(self.engine,dict(enabled=True,chats=[dict(chat_id='oc_test')]),run)
        adapter.flush();self.assertEqual(self.store.list('outbox')[0]['state'],'pending')
        self.now+=600;adapter.flush()
        self.assertEqual(self.store.list('outbox')[0]['state'],'sent')
        self.assertEqual(calls[0][calls[0].index('--idempotency-key')+1],calls[1][calls[1].index('--idempotency-key')+1])

    def test_offline_topic_replies_backfilled_before_cursor_commit(self):
        adapter=Feishu(self.engine,dict(chats=[dict(chat_id='oc_test',allowed_users=['ou_owner'])]))
        with self.store.transaction() as tx:tx.put('cursors','feishu:oc_test',dict(id='feishu:oc_test',since=1))
        broken=[True]
        def call(args):
            params=json.loads(args[-1]);topic=params['container_id_type']=='thread'
            if topic and broken[0]:raise ValueError('topic network error')
            item=dict(message_id='om_reply' if topic else 'om_root',chat_id='oc_test',thread_id='omt_topic',root_id='om_root',sender=dict(id='ou_owner',sender_type='user'),body=dict(content='{"text":"continue"}'))
            return dict(items=[item],has_more=False)
        adapter.call=call
        with self.assertRaises(ValueError):adapter.backfill()
        self.assertEqual(self.store.get('cursors','feishu:oc_test')['since'],1)
        broken[0]=False;adapter.backfill()
        self.assertEqual(len(self.store.list('triggers')),2)
        self.assertIsNotNone(self.store.get('triggers','feishu:om_reply'))
        self.assertGreater(self.store.get('cursors','feishu:oc_test')['since'],1)

    def test_per_chat_authority_does_not_leak_from_another_group(self):
        self.config['sources']['feishu'].update(chats=['oc_test','oc_second'],allowed_users=['ou_owner'],members={'oc_test':['ou_owner'],'oc_second':[]})
        item=self.trigger('cross',channel=dict(chat_id='oc_second',message_id='om_cross'))
        self.assertFalse(item['admitted'])


class FeishuCase(unittest.TestCase):
    def setUp(self):self.cfg=dict(enabled=True,bot_id='ou_bot',chats=[dict(chat_id='oc_test',allowed_users=['ou_person'])])
    def event(self):return dict(message_id='om_1',chat_id='oc_test',sender_id='ou_person',sender_type='user',content='{"text":"@_user_1 初始化"}',mentions=[dict(key='@_user_1',name='AS')])
    def test_native_stream_and_webhook_normalization(self):
        value=normalize(self.event(),self.cfg);self.assertEqual(value['text'],'@AS 初始化')
        self.assertEqual(value['thread'],'oc_test:om_1')
        raw=dict(schema='2.0',event=dict(message=dict(message_id='om_2',chat_id='oc_test',root_id='om_1',content='{"text":"继续"}'),sender=dict(sender_id=dict(open_id='ou_person'),sender_type='user')))
        self.assertEqual(normalize(raw,self.cfg)['thread'],'oc_test:om_1')
    def test_bot_and_unlisted_user_filtered(self):
        e=self.event();e['sender_id']='ou_bot';self.assertIsNone(normalize(e,self.cfg))
        e['sender_id']='ou_other';self.assertEqual(normalize(e,self.cfg)['actor'],'ou_other') # context only; Engine rejects authority


class VersionCase(unittest.TestCase):
    def test_copy_seal_and_detect_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';root.mkdir();(root/'code.py').write_text('print(1)')
            proof=dict(passed=True,checks=['test'])
            record,pinned=seal(root,Path(temp)/'versions','v1',proof)
            (root/'code.py').write_text('print(2)')
            self.assertEqual(content_digest(pinned),record['digest']);self.assertNotEqual(content_digest(root),record['digest'])
            (pinned/'code.py').write_text('changed')
            self.assertNotEqual(content_digest(pinned),record['digest'])


if __name__=='__main__':unittest.main()
