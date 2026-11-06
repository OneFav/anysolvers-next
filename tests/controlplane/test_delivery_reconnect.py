"""Offline delivery fixtures plus actual child-process crash/concurrency tests.

No model, native DSH research, evaluation or live Feishu traffic is involved.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from controlplane.engine import Engine
from controlplane.feishu import Feishu
from controlplane.store import Store


class ReconnectDelivery(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.store=Store(self.root/'router.db'); self.now=1000
        self.engine=Engine(self.store, {}, lambda:self.now)
        self.config=dict(enabled=True,chats=[dict(chat_id='chat'),dict(chat_id='other')])
        self.sent=[]; self.adapter=Feishu(self.engine,self.config)
        self.adapter.call=lambda args,**kw:self.sent.append(args) or dict(message_id='receipt-'+str(len(self.sent)))

    def put(self, key, purpose='answer', at=1, chat='chat', **extra):
        m=dict(id=key,job_id='J',text='同一段文字',purpose=purpose,reference=key,communication_version=1,
               channel=dict(chat_id=chat,message_id='human'),state='pending',attempts=0,due_at=1,created_at=at)
        m.update(extra)
        with self.store.transaction() as tx:tx.put('outbox',key,m)
        return m

    def outages(self, count=7, restored=True):
        for ep in range(1,count+1):
            self.put('loss-'+str(ep),'incident',ep*10,reference=f'incident:J:host_lost:{ep}')
            if ep<count or restored:
                self.put('recovery-'+str(ep),'recovery',ep*10+1,reference=f'connection-restored:A:{ep}')
        with self.store.transaction() as tx:
            tx.put('jobs','J',dict(id='J',state='running' if restored else 'host_lost',attempt_id='A',paused=True))
            tx.put('incidents','J:host_lost',dict(id='J:host_lost',episode=count,state='resolved' if restored else 'open'))

    def test_seven_offline_cycles_are_silent_and_all_evidence_retained(self):
        self.outages(); self.adapter.flush()
        self.assertEqual(self.sent,[])
        self.assertEqual(len(self.store.list('outbox')),14)
        self.assertTrue(all(m['state']=='superseded' for m in self.store.list('outbox')))
        self.assertTrue(self.store.get('jobs','J')['paused'])
        self.assertEqual(self.store.list('inputs'),[])

    def test_visible_outage_gets_only_latest_recovery(self):
        self.outages()
        with self.store.transaction() as tx:
            m=tx.get('outbox','loss-1');m.update(state='sent',delivery_receipt={'message_id':'original'});tx.put('outbox',m['id'],m)
        self.adapter.flush(); self.now+=300; self.adapter.flush()
        self.assertEqual(len(self.sent),1)
        self.assertIn('recovery-7',self.sent[0])
        self.assertEqual(self.store.get('outbox','loss-1')['delivery_receipt']['message_id'],'original')

    def test_current_outage_is_not_hidden_by_old_recoveries(self):
        self.outages(restored=False); self.adapter.flush()
        self.assertEqual(len(self.sent),1); self.assertIn('loss-7',self.sent[0])

    def test_receipt_pending_outage_not_rewritten_and_recovery_not_lost(self):
        self.outages()
        with self.store.transaction() as tx:
            m=tx.get('outbox','loss-1');m.update(attempts=5,attempted_at=12);tx.put('outbox',m['id'],m)
        calls=[]
        def call(args,**kw):
            calls.append(args)
            return dict(items=[]) if args[0]=='api' else dict(message_id='restored-receipt')
        self.adapter.call=call; self.adapter.flush()
        self.assertEqual(self.store.get('outbox','loss-1')['state'],'unknown')
        self.assertEqual(self.store.get('outbox','recovery-7')['state'],'sent')
        self.assertEqual(sum(a[0]=='im' for a in calls),1)
        self.now+=300; self.adapter.flush()
        self.assertEqual(sum(a[0]=='im' for a in calls),1)

    def test_answers_results_attention_signals_preserved_ordered_and_paced_across_restart(self):
        for key,purpose,at in [('z','answer',1),('a','result',2),('b','attention',3),('c','research_signal',4)]:
            self.put(key,purpose,at)
        for index in range(4):
            adapter=Feishu(Engine(Store(self.root/'router.db'),{},lambda:self.now),self.config)
            adapter.call=self.adapter.call
            adapter.flush(); adapter.flush()
            self.assertEqual(len(self.sent),index+1)
            self.now+=30
        self.assertEqual([a[a.index('--idempotency-key')+1] for a in self.sent],['z','a','b','c'])
        self.assertTrue(all(m['state']=='sent' for m in self.store.list('outbox')))

    def test_identical_text_in_another_job_not_deduplicated_or_starved(self):
        for i in range(150):self.put('q'+str(i),at=i)
        self.put('other',chat='other',job_id='other-job',at=200)
        self.adapter.flush()
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.store.get('outbox','other')['state'],'sent')

    def test_recheck_current_state_before_claim_and_no_lock_during_transport(self):
        self.outages(restored=False)
        payload=self.adapter._payload
        def resolve(message):
            result=payload(message)
            with self.store.transaction() as tx:
                incident=tx.get('incidents','J:host_lost');incident['state']='resolved';tx.put('incidents',incident['id'],incident)
            return result
        self.adapter._payload=resolve;self.adapter.flush();self.assertFalse(self.sent)
        self.put('human-question')
        def transport(args,**kwargs):
            with self.store.transaction() as tx:tx.put('probe','unlocked',{'ok':True})
            return dict(message_id='receipt')
        self.adapter.call=transport;self.adapter.flush()
        self.assertTrue(self.store.get('probe','unlocked')['ok'])

    def test_timeout_reconciles_original_receipt_without_any_resend(self):
        self.put('original'); calls=[]
        def send(args,**kw):
            calls.append(args)
            if args[0]=='im':raise subprocess.TimeoutExpired('mock',45)
            return dict(items=[dict(uuid='original',message_id='accepted-before-timeout')])
        self.adapter.call=send;self.adapter.flush()
        self.assertEqual(self.store.get('outbox','original')['state'],'unknown')
        self.now+=60;self.adapter.flush();self.now+=300;self.adapter.flush()
        self.assertEqual(sum(a[0]=='im' for a in calls),1)
        self.assertEqual(self.store.get('outbox','original')['delivery_receipt']['message_id'],'accepted-before-timeout')

    def test_missing_uuid_or_empty_history_never_authorizes_resend(self):
        self.put('ambiguous');calls=[]
        def send(args,**kw):
            calls.append(args)
            if args[0]=='im':return {}  # Lost or malformed acceptance receipt.
            return dict(items=[dict(message_id='unrelated',body={'content':'同一段文字'})])
        self.adapter.call=send;self.adapter.flush()
        for _ in range(3):self.now+=300;self.adapter.flush()
        self.assertEqual(sum(a[0]=='im' for a in calls),1)
        self.assertEqual(self.store.get('outbox','ambiguous')['state'],'unknown')

    def test_real_process_crash_after_acceptance_and_concurrent_flush(self):
        self.put('original');self.put('later',at=2)
        code='''import os,sys,time
from pathlib import Path
from controlplane.engine import Engine
from controlplane.feishu import Feishu
from controlplane.store import Store
root=Path(sys.argv[1]);f=Feishu(Engine(Store(root/'router.db'),{},lambda:1000),dict(enabled=True,chats=[dict(chat_id='chat')]))
def call(args,**kwargs):
    with (root/'accepted').open('a') as out:out.write(args[args.index('--idempotency-key')+1]+'\\n');out.flush();os.fsync(out.fileno())
    time.sleep(.4)
    os._exit(37)
f.call=call;f.flush()
'''
        children=[subprocess.Popen([sys.executable,'-B','-c',code,str(self.root)]) for _ in range(2)]
        try:
            codes=[p.wait(timeout=10) for p in children]
        finally:
            for p in children:
                if p.poll() is None:p.kill();p.wait()
        self.assertEqual(sorted(codes),[0,37])
        self.assertEqual((self.root/'accepted').read_text().splitlines(),['original'])
        self.assertEqual(self.store.get('outbox','original')['state'],'sending')
        self.adapter.flush();self.assertFalse(self.sent)  # Persisted gate, original still leased.
        self.now+=60
        self.adapter.call=lambda args,**kw:(dict(items=[dict(uuid='original',message_id='remote-original')])
            if args[0]=='api' else self.sent.append(args) or dict(message_id='remote-later'))
        self.adapter.flush();self.adapter.flush()
        self.assertEqual(len(self.sent),1);self.assertIn('later',self.sent[0])
        self.assertEqual(self.store.get('outbox','original')['delivery_receipt']['message_id'],'remote-original')


if __name__=='__main__':unittest.main()
