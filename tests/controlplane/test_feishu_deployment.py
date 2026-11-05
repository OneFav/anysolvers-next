import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from controlplane.config import load
from controlplane.engine import Engine
from controlplane.feishu import Feishu, normalize
from controlplane.store import Store


class DeploymentCase(unittest.TestCase):
    def test_cli_reply_to_maps_to_existing_message(self):
        cfg=dict(chats=[dict(chat_id='oc_test')])
        event=dict(message_id='om_reply',chat_id='oc_test',sender_id='ou_user',sender_type='user',content='继续',reply_to='om_parent')
        result=normalize(event,cfg)
        self.assertEqual(result['reply_to'],'feishu:om_parent')
        self.assertEqual(result['thread'],'oc_test:om_parent')

    def test_identity_environment_is_project_local(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir()
            (root/'config/settings.json').write_text(json.dumps(dict(schema_version=1,feishu=dict(cli_profile='anysolvers-next'))))
            cfg=load(root/'config/settings.json')
            with patch.dict(os.environ,{'LARKSUITE_CLI_APP_SECRET':'must-not-inherit','LARKSUITE_CLI_AUTH_PROXY':'must-not-inherit','OPENCLAW_HOME':'elsewhere'}):
                env=Feishu(None,cfg['feishu']).environment()
            self.assertNotIn('LARKSUITE_CLI_APP_SECRET',env)
            self.assertNotIn('LARKSUITE_CLI_AUTH_PROXY',env)
            self.assertNotIn('OPENCLAW_HOME',env)
            self.assertEqual(env['LARKSUITE_CLI_CONFIG_DIR'],str((root/'.state/feishu-cli').resolve()))
            self.assertEqual(env['LARKSUITE_CLI_PROFILE'],'anysolvers-next')
            self.assertEqual(env['LARKSUITE_CLI_STRICT_MODE'],'bot')

    def test_api_keeps_non_data_bot_envelope_and_bot_identity(self):
        calls=[]
        def run(command,**kwargs):
            calls.append((command,kwargs))
            return subprocess.CompletedProcess(command,0,stdout='{"code":0,"bot":{"open_id":"ou_bot"}}')
        value=Feishu(None,{},run).call(['api','GET','/open-apis/bot/v3/info'])
        self.assertEqual(value['bot']['open_id'],'ou_bot')
        self.assertEqual(calls[0][0][-4:],['--as','bot','--format','json'])
        self.assertEqual(calls[0][1]['encoding'],'utf-8')

    def test_json_envelope_preserves_pagination_and_rejects_empty_output(self):
        response={'ok':True,'identity':'bot','data':{'items':[{'message_id':'om_1'}],'has_more':True,'page_token':'next'}}
        def run(command,**kwargs):return subprocess.CompletedProcess(command,0,stdout=json.dumps(response))
        value=Feishu(None,{},run).call(['api','GET','/open-apis/im/v1/messages'])
        self.assertTrue(value['has_more']);self.assertEqual(value['page_token'],'next')
        self.assertEqual(value['items'][0]['message_id'],'om_1')
        for output in ['',json.dumps({'ok':False,'error':{'code':230027}})]:
            def empty(command,**kwargs):return subprocess.CompletedProcess(command,0,stdout=output)
            with self.assertRaises(ValueError):Feishu(None,{},empty).call(['api','GET','/open-apis/im/v1/messages'])

    def test_readiness_requires_cli_marker_and_stop_closes_real_child(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            engine=Engine(Store(root/'state.sqlite'),dict(sources={}))
            script=root/'fake_cli.py'
            script.write_text('import sys,time\ntime.sleep(.3)\nprint("[event] ready event_key=im.message.receive_v1",file=sys.stderr,flush=True)\ntime.sleep(30)\n')
            adapter=Feishu(engine,dict(cli_command=[sys.executable,str(script)]))
            stop=threading.Event();failures=[]
            def run():
                try:adapter.consume(stop)
                except Exception as exc:failures.append(exc)
            thread=threading.Thread(target=run);thread.start()
            try:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    status=engine.store.get('source_health','feishu:stream')
                    if status and status['state']=='connecting':break
                    time.sleep(.01)
                self.assertEqual(status['state'],'connecting')
                while time.monotonic()<deadline:
                    status=engine.store.get('source_health','feishu:stream')
                    if status['state']=='ready':break
                    time.sleep(.01)
                self.assertEqual(status['state'],'ready')
            finally:stop.set();thread.join(timeout=7)
            self.assertFalse(thread.is_alive());self.assertEqual(failures,[])

    def test_health_repeats_do_not_spam_events(self):
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(Store(Path(temp)/'state.sqlite'),{})
            adapter=Feishu(engine,{})
            adapter.health('history','error','retry');adapter.health('history','error','retry')
            adapter.health('history','ready','OK')
            self.assertEqual(len(engine.store.events()),2)
            self.assertEqual(engine.snapshot()['source_health'][0]['state'],'ready')


if __name__=='__main__':unittest.main()
