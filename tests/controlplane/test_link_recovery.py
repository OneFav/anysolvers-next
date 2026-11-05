import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from controlplane.engine import Engine,Conflict
from controlplane.store import Store
from controlplane.host import native_heartbeat,native_id
from controlplane.processes import birth,atomic

class ReconnectTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.now=1000
  self.config=dict(default_host='dev',default_version='v1',host_timeout_s=10,recovery=dict(stable_connection_s=6),sources={'feishu':dict(chats=['chat'],allowed_users=['owner'],allowed_stages=['initialize'])})
  self.store=Store(Path(self.temp.name)/'state.sqlite3');self.engine=Engine(self.store,self.config,lambda:self.now)
  self.engine.register_version(dict(id='v1',digest='a'*64,state='ready',validation={'passed':True}))
  self.engine.receive(dict(id='m1',source='feishu',thread='chat:thread',actor='owner',text='initialize',channel={'chat_id':'chat'}))
  result=self.engine.decide('m1',dict(action='create',title='fixture',scope='fixture',reason='authorized'))
  self.jid=result['job_id'];self.attempt=self.engine.claim('dev',self.jid)['attempt']
  self.event('start','started',dict(pid=123,native_id='native-stable'))
  self.proof=dict(attempt_id=self.attempt['id'],alive=True,native_alive=True,native_pid=123,native_id='native-stable',native_heartbeat_age_s=0,agent_status='idle')
 def event(self,key,kind,data):return self.engine.worker_event(self.attempt['id'],self.attempt['token'],dict(id=key,type=kind,data=data))
 def job(self):return self.store.get('jobs',self.jid)
 def contact(self):return self.engine.worker_context(self.attempt['id'],self.attempt['token'])
 def heartbeat(self,**changes):return self.engine.host_heartbeat('dev',[dict(self.proof,**changes)])
 def lose(self):
  self.heartbeat();self.now+=20;self.engine.monitor();self.assertEqual(self.job()['state'],'host_lost')
 def recover(self):
  self.contact();self.heartbeat();self.now+=7;self.contact();self.heartbeat()
 def resumes(self):return [i for i in self.store.list('inputs') if i['payload'].get('kind')=='connection_recovered']
 def test_live_reconnect_preserves_identity_and_deduplicates_each_episode(self):
  before=self.job();self.lose();self.recover()
  self.assertEqual(self.job()['state'],'running');self.assertEqual(len(self.resumes()),1)
  for k in ('id','project_id','attempt_id','generation','version_id','version_digest'):self.assertEqual(before[k],self.job()[k])
  for _ in range(8):self.now+=3;self.contact();self.heartbeat()
  self.assertEqual(len(self.resumes()),1)
  self.lose();self.recover();self.assertEqual(len(self.resumes()),2)
  self.assertEqual(self.store.get('incidents',self.jid+':host_lost')['episode'],2)
 def test_inflight_or_unknown_result_blocks_until_original_result_commits(self):
  self.engine.tool_begin(self.attempt['id'],self.attempt['token'],'original','solver_evaluate',{'id':'original-evaluation'})
  self.lose();self.recover()
  self.assertEqual(self.job()['state'],'host_lost');self.assertEqual(self.job()['connection_recovery']['phase'],'waiting_operations');self.assertFalse(self.resumes())
  self.engine.tool_finish(self.attempt['id'],self.attempt['token'],'original',{'score':1})
  self.contact();self.heartbeat();self.assertEqual(self.job()['state'],'running');self.assertEqual(len(self.store.list('operations')),1)
 def test_host_online_or_reused_pid_or_stale_heartbeat_is_not_recovery(self):
  self.lose();self.contact()
  for changes in [dict(native_id='different'),dict(native_pid=456),dict(native_alive=False),dict(native_heartbeat_age_s=60),dict(native_heartbeat_age_s=-1)]:
   self.now+=7
   self.contact();self.heartbeat(**changes);self.assertEqual(self.job()['state'],'host_lost')
  self.heartbeat();self.assertEqual(self.job()['state'],'host_lost');self.assertFalse(self.resumes())
 def test_auth_roundtrip_is_required_even_when_host_claims_live_process(self):
  self.lose();self.heartbeat();self.now+=7;self.heartbeat();self.assertEqual(self.job()['state'],'host_lost')
  with self.assertRaisesRegex(Conflict,'Host must verify'):self.event('unsafe','recovered',{'verified':True})
 def test_waiting_input_and_model_fault_are_preserved_and_not_woken(self):
  self.event('question','waiting_input',{'question':'need data'});self.lose();self.recover()
  self.assertEqual(self.job()['state'],'waiting_input');self.assertFalse(self.resumes())
  self.event('fault','fault',dict(kind='provider_quota',message='provider unavailable'));self.lose();self.recover()
  self.assertEqual(self.job()['state'],'waiting_provider');self.assertFalse(self.resumes())
  self.assertEqual(self.store.get('incidents',self.jid+':provider_quota')['state'],'open')
 def test_paused_and_running_native_turn_are_not_woken(self):
  with self.store.transaction() as tx:
   j=tx.get('jobs',self.jid);j['paused']=True;tx.put('jobs',j['id'],j)
  self.lose();self.recover();self.assertTrue(self.job()['paused']);self.assertFalse(self.resumes())
  with self.store.transaction() as tx:
   j=tx.get('jobs',self.jid);j['paused']=False;tx.put('jobs',j['id'],j)
  self.proof['agent_status']='running';self.lose();self.recover();self.assertFalse(self.resumes())
 def test_restart_of_controlplane_and_legacy_loss_recover_from_audit(self):
  self.event('ask','waiting_input',{'question':'waiting'});self.lose()
  with self.store.transaction() as tx:
   j=tx.get('jobs',self.jid);j.pop('connection_loss');j.pop('connection_recovery');tx.put('jobs',j['id'],j)
  self.engine=Engine(Store(self.store.path),self.config,lambda:self.now);self.recover()
  self.assertEqual(self.job()['state'],'waiting_input');self.assertFalse(self.resumes())
 def test_dead_process_waiting_for_user_stays_waiting_until_followup(self):
  self.event('ask','waiting_input',{'question':'need input'});self.lose();self.heartbeat(alive=False,native_alive=False)
  self.assertEqual(self.job()['state'],'waiting_input');self.assertTrue(self.job()['restart_waiting_input'])
  self.engine.receive(dict(id='m2',source='feishu',thread='chat:thread',actor='owner',text='here is the input',channel={'chat_id':'chat'}))
  self.engine.decide('m2',dict(action='attach',job_id=self.jid,reason='reply'))
  self.assertEqual(self.job()['state'],'reconciliation')
  result=self.engine.recover_attempt('dev',self.jid,dict(attempt_id=self.attempt['id'],native_session_persisted=True,implementation_matches=True))
  self.assertEqual(result['state'],'queued')
 def test_stopped_attempt_is_never_revived(self):
  self.lose()
  with self.store.transaction() as tx:
   j=tx.get('jobs',self.jid);j['state']='stopped';tx.put('jobs',j['id'],j)
  self.heartbeat();self.assertEqual(self.job()['state'],'stopped');self.assertFalse(self.resumes())

class ProcessProofTests(unittest.TestCase):
 def test_real_process_identity_stale_heartbeat_and_pid_reuse(self):
  # Actual OS process, deterministic fake native heartbeat; no DSH/model/solver is run here.
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(15)'])
   try:
    now=time.time();job='job-fixture';identity=native_id(job);stamp=birth(process.pid)
    atomic(root/'launch.json.child.json',dict(pid=process.pid,birth=stamp));atomic(root/'session/native.json',dict(native_id=identity))
    atomic(root/'session/heartbeat.json',dict(pid=process.pid,native_id=identity,at=now*1000,agent_status='idle'))
    proof=native_heartbeat(root,job,now);self.assertTrue(proof['native_alive']);self.assertEqual(proof['native_pid'],process.pid)
    self.assertGreater(native_heartbeat(root,job,now+60)['native_heartbeat_age_s'],15)
    atomic(root/'launch.json.child.json',dict(pid=process.pid,birth='old PID incarnation'))
    self.assertFalse(native_heartbeat(root,job,now)['native_alive'])
   finally:process.terminate();process.wait(timeout=5)

if __name__=='__main__':unittest.main()
