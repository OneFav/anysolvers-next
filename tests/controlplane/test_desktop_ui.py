import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from controlplane.config import load
from controlplane.desktop_ui import DesktopUI
from controlplane.desktop_workflows import start,POLICY,SOURCE
from controlplane.store import Store
from controlplane.engine import Engine,Conflict

class DesktopTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
  self.config=dict(_base=str(root),state_db='state.db',worker_host=dict(work_root='projects',python='/usr/bin/python3'),default_version='v2',default_host='dev',sources={SOURCE:dict(POLICY)})
  self.ui=DesktopUI(self.config);self.store=self.ui.store;self.engine=Engine(self.store,self.config)
  self.project='project-fixture';(root/'projects'/self.project).mkdir(parents=True)
  with self.store.transaction() as tx:
   for v in ['v1','v2']:tx.put('versions',v,dict(id=v,digest=('a' if v=='v1' else 'b')*64,state='ready',validation={'passed':True}))
   tx.put('jobs','init',dict(id='init',project_id=self.project,stage='initialize',state='completed',owner='feishu-owner',channel={'chat_id':'oc_fixture'},version_id='v1',output={'study_id':'study','config':'configs/base.yaml'},created_at=1))
  self.profile=dict(config_sha='d'*64,previous_runs=['study/001'])
 def launch(self):
  with patch.object(self.ui,'call',return_value=self.profile):return start(self.ui,self.project,'study','configs/ui/study-'+'1'*32+'.yaml')
 def test_idempotent_and_new_version_without_old_mutation(self):
  before=copy.deepcopy(self.store.get('jobs','init'));one=self.launch();two=self.launch()
  self.assertEqual(one,two);job=self.store.get('jobs',one['job_id'])
  self.assertEqual(job['version_id'],'v2');self.assertEqual(job['handoff']['desktop_config']['sha256'],'d'*64)
  self.assertEqual(self.store.get('jobs','init'),before)
  self.assertEqual(job['owner'],'feishu-owner');self.assertEqual(job['channel']['chat_id'],'oc_fixture')
  self.assertEqual(job['mandate']['actor'],'desktop-owner')
  self.assertTrue(all(x['channel']['chat_id']=='oc_fixture' for x in self.store.list('outbox')))
  self.assertEqual(len(self.store.list('jobs')),2)
 def test_active_project_rejected(self):
  self.launch()
  with patch.object(self.ui,'call',return_value=self.profile),self.assertRaisesRegex(Conflict,'活动'):
   start(self.ui,self.project,'study','configs/ui/study-'+'2'*32+'.yaml')
 def test_terminal_only_after_verified_research_and_survives_restart(self):
  launched=self.launch();self.engine.monitor();self.assertEqual(len(self.store.list('jobs')),2)
  with self.store.transaction() as tx:
   job=tx.get('jobs',launched['job_id']);job.update(state='completed',output=dict(validated=True,run_id='study/002'));tx.put('jobs',job['id'],job)
  engine=Engine(Store(self.store.path),self.config)
  engine.monitor();engine.monitor()
  terminals=[j for j in self.store.list('jobs') if j['stage']=='terminal'];self.assertEqual(len(terminals),1)
  self.assertEqual(terminals[0]['handoff']['run_id'],'study/002');self.assertEqual(terminals[0]['version_id'],'v2')
  with self.store.transaction() as tx:
   job=tx.get('jobs',terminals[0]['id']);job['state']='completed';tx.put('jobs',job['id'],job)
  engine.monitor();self.assertEqual(self.store.get('desktop_workflows',launched['workflow_id'])['state'],'completed')
 def test_stop_does_not_launch_terminal(self):
  launched=self.launch()
  with self.store.transaction() as tx:
   job=tx.get('jobs',launched['job_id']);job['state']='stopped';tx.put('jobs',job['id'],job)
  self.engine.monitor();self.assertEqual(len(self.store.list('jobs')),2)
 def test_scope_and_paths(self):
  for method,payload in [('evaluate',{}),('projects',{'root':'/tmp'}),('catalog',{'projectId':'../../AnySolvers-dsh'}),('progress',{'projectId':self.project,'runId':'study/999'})]:
   with self.assertRaises(ValueError):self.ui.request(method,payload)
  p=Path(self.tmp.name)/'projects'/self.project;p.rmdir();p.symlink_to('/tmp')
  with self.assertRaisesRegex(ValueError,'Redirected'):self.ui.project(self.project)
 def test_ui_source_cannot_be_used_by_feishu(self):
  source=dict(self.config,sources={'feishu':dict(allowed_stages=['research'])})
  engine=Engine(self.store,source)
  with self.store.transaction() as tx,self.assertRaisesRegex(ValueError,'scope'):
   engine._create(tx,dict(id='fake',source='feishu',actor='other',channel={'chat_id':'other'},text='fake'),dict(stage='research',project_id=self.project,predecessor_id='init'))

if __name__=='__main__':unittest.main()
