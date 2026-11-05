"""Deterministic launch receipts; actual native processes are tested separately."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from controlplane.engine import Engine
from controlplane.store import Store
from controlplane.observer_runtime import Supervisor
from controlplane.processes import atomic


class SupervisorCase(unittest.TestCase):
    def test_enabled_starts_one_idle_observer_and_unknown_spawn_never_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            cfg=dict(_base=str(root),observer=dict(enabled=True,state_root='observer'),router_agent=dict(
                router_url='http://127.0.0.1:1',provider='fixture',model='fixture',dsh_command=['fixture-dsh']))
            engine=Engine(Store(root/'router.db'),cfg)
            supervisor=Supervisor(engine,Path(__file__).resolve().parents[2])
            with patch('controlplane.observer_runtime.subprocess.Popen',return_value=Mock()) as spawn:
                supervisor.tick();supervisor.tick()
                self.assertEqual(spawn.call_count,1)
                self.assertEqual(len(engine.observer.store.list('observers')),1)
                self.assertFalse(engine.observer.store.get('observers','default')['active'])
                receipt=json.loads((root/'observer/default/launch.json').read_text())
                overlay=json.loads(Path(receipt['patch']).read_text())
                entries=overlay[-1]['insert']
                self.assertTrue(any(p['name'].endswith('observer-runner.js') for p in entries))
                self.assertFalse(any('research-runtime' in p['name'] or 'study-sessions' in p['name'] for p in entries))
                # An attested exited wrapper authorizes a fresh native context.
                atomic(root/'observer/default/launch.json.pid.json',dict(pid=123,birth='old-birth'))
                atomic(root/'observer/default/launch.json.exit.json',dict(code=1))
                with patch('controlplane.observer_runtime.birth',return_value=None):supervisor.tick()
                self.assertEqual(spawn.call_count,2)
                fresh=json.loads((root/'observer/default/launch.json').read_text())
                self.assertNotEqual(receipt['session_id'],fresh['session_id'])

    def test_live_pid_receipt_keeps_the_same_logical_observer_across_new_batches(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            cfg=dict(_base=str(root),observer=dict(enabled=True,state_root='observer'),router_agent=dict(
                router_url='http://127.0.0.1:1',provider='fixture',model='fixture',dsh_command=['fixture-dsh']))
            engine=Engine(Store(root/'router.db'),cfg);supervisor=Supervisor(engine,Path(__file__).resolve().parents[2])
            with patch('controlplane.observer_runtime.subprocess.Popen',return_value=Mock()) as spawn:
                supervisor.tick()
                atomic(root/'observer/default/launch.json.pid.json',dict(pid=123,birth='live-birth'))
                with engine.store.transaction() as tx:
                    for job in ('A','B'):tx.put('jobs',job,dict(id=job,project_id=job,stage='research',state='running',host_id='dev'))
                with patch('controlplane.observer_runtime.birth',return_value='live-birth'):supervisor.tick()
                self.assertEqual(spawn.call_count,1)
                self.assertEqual(engine.observer.public()[0]['jobs'],['A','B'])


if __name__=='__main__':unittest.main()
