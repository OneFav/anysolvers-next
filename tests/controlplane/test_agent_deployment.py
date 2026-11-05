"""Installation fixtures and real local process checks; no model or live Feishu calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from argparse import Namespace
from controlplane.config import load
from controlplane.feishu import Feishu
from controlplane.processes import atomic
from controlplane.deployment import service

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('agent_deploy',ROOT/'scripts/deploy.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)

class AgentDeployment(unittest.TestCase):
    def args(self,**changes):
        return Namespace(**dict(dict(mode='standalone',port=8797,router_url=None,projects=None,provider='fixture',model='fixture',dsh_credentials_home=None),**changes))
    def test_init_keeps_separate_state_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory(prefix='部署 space ') as temp:
            instance=Path(temp).resolve()/'instance'
            deploy.init(instance,self.args())
            cfg=load(instance/'config/settings.json')
            self.assertEqual(cfg['_base'],str(instance))
            self.assertEqual(cfg['feishu']['chats'],[])
            self.assertFalse(cfg['feishu']['enabled'])
            self.assertEqual(cfg['worker_host']['router_url'],'http://127.0.0.1:8797')
            self.assertIn(str(instance),cfg['worker_host']['python'])
            with self.assertRaises(ValueError):deploy.init(instance,self.args())
    def test_host_requires_explicit_secure_router(self):
        with tempfile.TemporaryDirectory() as temp:
            for url in [None,'http://remote.example:8787','https://user:password@example.com']:
                with self.assertRaises(ValueError):deploy.init(Path(temp).resolve(),self.args(mode='host',router_url=url))
            deploy.init(Path(temp).resolve(),self.args(mode='host',router_url='https://router.example.com'))
    def test_history_mode_does_not_start_consumer(self):
        import threading
        class Fixture(Feishu):
            def flush(self):pass
            def backfill(self):pass
            def health(self,*args):pass
            def consume(self,*args):raise AssertionError('Extra stream')
        stop=threading.Event();stop.set()
        Fixture(None,dict(receive_mode='history')).serve(stop)
    def test_stale_status_is_stopped(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();deploy.init(root,self.args())
            atomic(root/'.state/services/status.json',dict(pid=999999999,birth='stale',state='running',children={}))
            self.assertEqual(service('status',root/'config/settings.json')['state'],'stopped')
    def test_external_config_desktop_bridge_real_process(self):
        with tempfile.TemporaryDirectory(prefix='部署 space ') as temp:
            root=Path(temp).resolve();deploy.init(root,self.args())
            result=subprocess.run([sys.executable,'-B','-m','controlplane.desktop_ui','--config',str(root/'config/settings.json')],
                cwd=ROOT,input=json.dumps(dict(method='deployment',payload={})),text=True,capture_output=True,check=True)
            value=json.loads(result.stdout)
            self.assertTrue(value['ok'],value)
            self.assertEqual(value['value']['instance'],str(root))
            self.assertFalse(value['value']['running'])

if __name__=='__main__':unittest.main()
