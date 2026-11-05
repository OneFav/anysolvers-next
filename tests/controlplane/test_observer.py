"""Deterministic observation fixtures, NOT real scientific/model evaluations."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from controlplane.observer import Observer
from controlplane.engine import Engine, Conflict
from controlplane.store import Store
from controlplane.feishu import Feishu
from controlplane.observer_collector import Collector


class ObserverCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=1000
        self.o=Observer(self.root/'observer.db',clock=lambda:self.now)
        self.e=Engine(Store(self.root/'router.db'),dict(feishu=dict(identity_map=dict(alice='ou_alice'))),lambda:self.now)
        self.s=self.o.open_session('default');self.o.ready(*self.args())
        for name in ('A','B'):
            self.o.register_job(dict(id=name,stage='research',state='running',project_id=name,host_id='dev',
                title=name,version_id='v1',version_digest='a'*64,owner='ou_alice',channel=dict(chat_id='oc_test',message_id='om_source')))

    def args(self):return ('default',self.s['id'],self.s['token'])

    def evidence(self,job='A',position=1,content='normal research',kind='analysis',**extra):
        return dict(job_id=job,batch='study-'+job+'/001',session='session-'+job,position=position,kind=kind,
            object='message',version=hashlib.sha256(content.encode()).hexdigest(),content=content,at=self.now,**extra)

    def push(self,*records):return self.o.ingest(list(records))
    def next(self):return self.o.next(*self.args())
    def judgment(self,batch,**extra):
        return {**dict(batch_id=batch['id'],k_revision=self.o.k()['revision'],scope_revision=self.o.store.get('observers','default')['scope_revision'],
            disposition='ignore',notes='',comparisons_state={},clues=[],report=None),**extra}

    def finish(self,batch,**extra):return self.o.commit(*self.args(),self.judgment(batch,**extra))

    def k(self,expectation='A should beat B'):
        person={f:[] for f in ('shared_background','research_goals','expectations','judgment_basis','human_decisions','communication')}
        person['expectations']=[dict(id='ab-expectation',text=expectation,certainty='assumed',basis=['human:fixture'])]
        return self.o.replace_k(self.o.k()['revision'],dict(alice=person))

    def report(self,refs):
        return dict(judgment='A/B evidence challenges the assumed superiority of A',expectations=['ab-expectation'],
            people=['alice'],decisions='Alice may change experiment allocation',confidence='limited development evidence',
            limitations='fixture only; same evaluator/config required',references=refs,timing='next design decision')

    def test_router_gets_bounded_sent_history_not_pending_or_unknown_delivery(self):
        with self.e.store.transaction() as tx:
            # IDs are deliberately reverse-sorted: history must use delivery time.
            for i in range(15):
                key='message-'+str(99-i)
                tx.put('outbox',key,dict(id=key,observer_report_id='report-'+str(i),job_id='A',
                    state='sent',created_at=i,sent_at=100+i,text='方法改动 '+str(i)))
            for state in ('pending','unknown','needs_rewrite'):
                tx.put('outbox',state,dict(id=state,observer_report_id='report-pending',job_id='A',
                    state=state,created_at=999,text='not delivered'))
            tx.put('outbox','ordinary',dict(id='ordinary',state='sent',sent_at=999,text='ordinary reply'))
            tx.put('outbox','long',dict(id='long',observer_report_id='report-long',job_id='B',
                state='sent',sent_at=200,text='旧版报告'*1000))
        history=self.o.router_inbox(self.e)['recent_communications']
        self.assertEqual(len(history),12)
        self.assertEqual(history[0]['text'],'方法改动 4')
        self.assertEqual(history[-2]['text'],'方法改动 14')
        self.assertFalse(history[-2]['truncated'])
        self.assertEqual(history[-1]['job_id'],'B')
        self.assertTrue(history[-1]['truncated'])
        self.assertEqual(len(history[-1]['text']),2000)
        self.assertEqual(self.e.store.get('outbox','long')['text'],'旧版报告'*1000)
        self.assertFalse(self.e.store.list('decisions'))

    def test_cross_batch_expectation_and_deep_read_idea_without_share(self):
        self.k()
        self.o.configure(comparisons=[dict(id='ab',jobs=['A','B'],expectation='A beats B',metrics=['score'],conditions=['same dataset/backend'])])
        idea='unpublished analysis: '+('mechanism ' * 100)+'try controlled ablation'
        ids=self.push(self.evidence(content=idea),self.evidence('A',2,'{"score":1,"valid":true}',kind='evaluation'),
                      self.evidence('B',1,'{"score":2,"valid":true}',kind='evaluation'))
        self.assertIsNone(self.next()['batch']);self.now+=30
        batch=self.next()['batch']
        self.assertEqual({x['job_id'] for x in batch['events']},{'A','B'})
        self.assertTrue(batch['events'][0]['truncated'])
        read=self.o.read(*self.args(),dict(kind='session',session='session-A',position=1,before=0,after_window=1))
        self.assertIn('try controlled ablation',read['items'][0]['content'])
        context=self.o.read(*self.args(),dict(kind='context'))
        self.assertEqual(context['k']['people']['alice']['expectations'][0]['certainty'],'assumed')
        receipt=self.finish(batch,disposition='report',notes='A/B pending controlled replication',report=self.report(ids))
        inbox=self.o.router_inbox(self.e);self.assertEqual(len(inbox['reports']),1)
        self.assertEqual(inbox['reports'][0]['id'],receipt['report_id'])
        self.assertEqual(inbox['reports'][0]['evidence'][0]['version'],self.evidence(content=idea)['version'])
        self.assertIn('try controlled ablation',self.o.router_read(self.e,receipt['report_id'],ids[0])['content'])
        self.o.router_inbox(self.e);self.assertEqual(len(self.e.store.list('observer_inbox')),1)

    def test_batch_sparse_dense_fixed_window_fairness_and_busy_arrivals(self):
        self.push(self.evidence());self.now+=29;self.assertIsNone(self.next()['batch'])
        self.push(self.evidence(position=2));self.now+=1
        batch=self.next()['batch'];self.assertEqual(len(batch['events']),2) # Window does not slide.
        self.push(*[self.evidence('A',p,'dense-'+str(p)) for p in range(3,53)])
        self.push(*[self.evidence('A',p,'dense-'+str(p)) for p in range(53,103)],self.evidence('B',1,'sparse'))
        self.assertEqual(self.next()['batch']['id'],batch['id']) # One judgment in flight.
        self.finish(batch)
        batch=self.next()['batch'];self.assertIn('B',{e['job_id'] for e in batch['events']})
        self.assertLessEqual(len(batch['events']),40)
        self.finish(batch)
        self.now+=120
        self.assertIsNotNone(self.next()['batch'])

    def test_quiet_materials_do_not_force_a_report_or_model_when_idle(self):
        for _ in range(5):self.assertIsNone(self.next()['batch'])
        self.push(self.evidence());self.now+=30;batch=self.next()['batch'];self.finish(batch)
        self.assertFalse(self.o.store.list('observer_reports'));self.assertIsNone(self.next()['batch'])

    def test_k_update_reopens_clue_atomic_current_only_and_stale_commit(self):
        self.k();self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        clue=dict(id='mechanism',proposition='A may depend on case size',missing='controlled scaling measurement',
            recheck_when='new development evidence or K change',check_at=None,support=[],against=[])
        self.finish(batch,disposition='wait',clues=[clue]);self.assertIsNone(self.next()['batch'])
        self.k('Maybe B is better');batch=self.next()['batch'];self.assertTrue(batch['triggers']['k_changed'])
        old=self.judgment(batch);self.k('Compare uncertainty first')
        with self.assertRaisesRegex(Conflict,'K/scope changed'):self.o.commit(*self.args(),old)
        self.assertEqual(self.o.read(*self.args(),dict(kind='context'))['k']['revision'],3)
        self.finish(batch)
        self.assertEqual(len(self.o.store.list('observer_k')),1)
        self.assertFalse(self.o.store.events())

    def test_wait_clue_requires_missing_evidence_and_due_is_read_not_report(self):
        self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        with self.assertRaises(ValueError):self.finish(batch,disposition='wait',clues=[dict(id='x')])
        self.finish(batch,disposition='wait',clues=[dict(id='x',proposition='x',missing='replication',recheck_when='time/evidence',check_at=self.now+40,support=[],against=[])])
        self.now+=39;self.assertIsNone(self.next()['batch'])
        self.now+=1;batch=self.next()['batch'];self.assertFalse(batch['events'])
        self.finish(batch);self.assertFalse(self.o.store.list('observer_reports'))

    def test_report_can_reference_real_goal_without_inventing_expectation(self):
        self.k();people=self.o.k()['people'];people['alice']['expectations']=[]
        people['alice']['research_goals']=[dict(id='methods',text='Find sufficiently new and effective methods worth extending',certainty='explicit',basis=['human:goal'])]
        self.o.replace_k(self.o.k()['revision'],people)
        ids=self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        report=self.report(ids);report.pop('expectations');ref=dict(person='alice',field='research_goals',id='methods')
        for bad in ([],[dict(ref,id='invented')],[dict(ref,field='expectations')],[dict(ref,person='bob')],[{'id':'methods'}]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                self.finish(batch,disposition='report',report=dict(report,k_refs=bad))
            self.assertEqual(self.next()['batch']['id'],batch['id'])
        receipt=self.finish(batch,disposition='report',report=dict(report,k_refs=[ref,ref]))
        stored=self.o.store.get('observer_reports',receipt['report_id'])['report']
        self.assertEqual(stored['k_refs'],[ref]);self.assertEqual(stored['expectations'],[])
        self.assertEqual(self.o.k()['people']['alice']['expectations'],[])
        self.assertEqual(len(self.o.router_inbox(self.e)['reports']),1)

    def test_k_reference_stale_and_cross_person_ids_cannot_authorize_report(self):
        self.k();people=self.o.k()['people'];people['bob']=copy.deepcopy(people['alice'])
        people['bob']['expectations'][0]['id']='bob-only'
        self.o.replace_k(self.o.k()['revision'],people)
        ids=self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        with self.assertRaisesRegex(ValueError,'reported people'):
            self.finish(batch,disposition='report',report=dict(self.report(ids),expectations=['bob-only']))
        report=dict(self.report(ids),expectations=[],k_refs=[dict(person='alice',field='expectations',id='ab-expectation')])
        value=self.judgment(batch,disposition='report',report=report)
        self.k('new context')
        with self.assertRaisesRegex(Conflict,'K/scope changed'):
            self.o.commit(*self.args(),value)
        self.assertFalse(self.o.store.list('observer_reports'))

    def test_recent_report_memory_survives_rotation_and_is_bounded(self):
        self.k()
        ids=[]
        for position in range(1,7):
            refs=self.push(self.evidence(position=position));self.now+=30;batch=self.next()['batch']
            r=self.report(refs);r['judgment']='new supported proposition '+str(position)+' '+('detail '*500)
            ids.append(self.finish(batch,disposition='report',report=r)['report_id'])
        self.o.router_inbox(self.e)
        previous=self.args();self.s=self.o.rotate(*previous);self.o.ready(*self.args())
        self.push(self.evidence(position=7));self.now+=30;self.next()
        context=self.o.read(*self.args(),dict(kind='context'))
        self.assertEqual([r['id'] for r in context['recent_reports']],list(reversed(ids[-4:])))
        self.assertTrue(all(len(r['judgment_excerpt'])<=600 and r['state']=='received' for r in context['recent_reports']))
        with self.assertRaises(Conflict):self.o.read(*previous,dict(kind='context'))

    def test_model_shaped_bad_references_fail_without_advancing_judgment(self):
        self.k();ids=self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        context=self.o.read(*self.args(),dict(kind='context'))
        self.assertEqual(context['judgment_id'],batch['id'])
        with self.assertRaisesRegex(ValueError,'research run ID is not a judgment ID'):
            self.finish(batch,batch_id='study-a/001')
        self.assertEqual(self.next()['batch']['id'],batch['id'])
        for field in ('expectations','people','references'):
            report=self.report(ids);report[field]=[{'id':'mistaken-object'}]
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'strings'):
                self.finish(batch,disposition='report',report=report)
            self.assertEqual(self.next()['batch']['id'],batch['id'])
        clue=dict(id='pending',proposition='x',missing='measurement',recheck_when='new evidence',
            check_at=None,support=[{'id':ids[0]}],against=[])
        with self.assertRaisesRegex(ValueError,'evidence-ID strings'):
            self.finish(batch,disposition='wait',clues=[clue])
        self.assertFalse(self.o.store.list('observer_commits'))
        self.finish(batch,disposition='report',report=self.report(ids))
        report=self.o.router_inbox(self.e)['reports'][0]
        with self.assertRaisesRegex(ValueError,'decision must be an object'):
            self.o.review(self.e,report['id'],'send')
        self.assertEqual(self.e.store.get('observer_inbox',report['id'])['state'],'received')
        self.assertFalse(self.e.store.list('outbox'))

    def test_rotation_crash_retry_fencing_and_idempotent_report(self):
        self.k();ids=self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        # Crash before commit: same batch and same source evidence restored.
        self.o=Observer(self.root/'observer.db',clock=lambda:self.now)
        previous=self.args();self.s=self.o.open_session('default');self.o.ready(*self.args())
        restored=self.next()['batch'];self.assertEqual(restored['id'],batch['id'])
        with self.assertRaises(Conflict):self.o.commit(*previous,self.judgment(batch))
        value=self.judgment(restored,disposition='report',report=self.report(ids),rotate=True)
        receipt=self.o.commit(*self.args(),value)
        self.assertEqual(receipt,self.o.commit(*self.args(),value))
        self.assertEqual(len(self.o.store.list('observer_reports')),1)
        self.push(self.evidence('B',2,'during handover'));self.now+=30
        old=self.args();self.s=self.o.rotate(*old)
        self.assertEqual(self.s,self.o.rotate(*old)) # Lost rotation response retry.
        with self.assertRaises(Conflict):self.o.next(*old)
        takeover=self.o.ready(*self.args());self.assertTrue(takeover['cursors'])
        self.assertEqual(self.next()['batch']['events'][0]['excerpt'],'during handover')

    def test_cutoff_paging_tool_details_source_version_and_final_boundary(self):
        ids=self.push(self.evidence(content='x'*40000,kind='source'),self.evidence(position=2,kind='tool_input',call_id='call-1'),
                      self.evidence(position=3,kind='tool_result',call_id='call-1',content='{"error":"fixture"}'))
        self.now+=30;batch=self.next()['batch'];self.push(self.evidence(position=4,content='after cutoff'))
        first=self.o.read(*self.args(),dict(kind='material',id=ids[0],bytes=4096))
        self.assertEqual(len(first['items'][0]['content']),4096)
        second=self.o.read(*self.args(),dict(kind='material',id=ids[0],offset=first['items'][0]['next_offset'],bytes=4096))
        self.assertEqual(second['items'][0]['offset'],4096)
        rows=self.o.read(*self.args(),dict(kind='index',limit=1));self.assertEqual(rows['cutoff'],batch['cutoff'])
        page=self.o.read(*self.args(),dict(kind='index',after=rows['next'],limit=50))
        self.assertEqual(len(page['items']),2)
        calls=self.o.read(*self.args(),dict(kind='tool',call_id='call-1',session='session-A'))
        self.assertEqual(len(calls['items']),2);self.assertIn('error',calls['items'][1]['content'])
        with self.assertRaises(ValueError):self.push(self.evidence(purpose='final'))
        with self.assertRaises(ValueError):self.o.register_job(dict(id='terminal',stage='terminal'))

    def test_unicode_and_escaped_material_pagination_always_progresses_with_a_byte_cap(self):
        original='中文"\\\n'*3000
        ref=self.push(self.evidence(content=original))[0];self.now+=30;self.next()
        parts=[];offset=0
        for _ in range(100):
            page=self.o.read(*self.args(),dict(kind='material',id=ref,offset=offset,bytes=4096))
            item=page['items'][0];parts.append(item['content'])
            self.assertGreater(item['next_offset'],offset)
            self.assertLessEqual(page['returned_bytes'],4096+9216)
            offset=item['next_offset']
            if not item['content_truncated']:break
        self.assertEqual(''.join(parts),original)

    def test_scope_default_reuse_remove_end_and_explicit_independent_observer(self):
        self.assertEqual(len(self.o.public()),1)
        self.o.configure(add=['A']);self.o.configure(add=['B']);self.assertEqual(len(self.o.public()),1)
        self.o.configure(remove=['B']);self.push(self.evidence('B'));self.now+=30;self.assertIsNone(self.next()['batch'])
        with self.assertRaises(ValueError):self.o.configure('separate',add=['B'])
        self.o.configure('separate',add=['B'],independent_reason='Different responsible people and decision timing')
        self.assertEqual(len(self.o.public()),2)
        with self.assertRaises(ValueError):self.o.configure('..',independent_reason='unsafe path')
        self.o.register_job(dict(id='A',stage='research',state='completed',progress=dict(run_id='study-A/001')))
        batch=self.next()['batch'];self.assertEqual(batch['events'][0]['kind'],'scope_end')
        self.finish(batch);self.assertIsNone(self.next()['batch'])
        self.assertEqual(len(self.o.public()),2)

    def test_router_receipt_send_feedback_are_separate_and_unknown_send_checked(self):
        self.k();ids=self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        rid=self.finish(batch,disposition='report',report=self.report(ids))['report_id'];self.o.router_inbox(self.e)
        decision=dict(action='send',reason='Changes next experiment',k_revision=self.o.k()['revision'],job_id='A',text='Please reconsider A/B allocation',people=['alice'])
        self.o.review(self.e,rid,decision);self.o.review(self.e,rid,decision)
        self.assertEqual(len(self.e.store.list('outbox')),1)
        sent=[]
        def cli(args):
            sent.append(args)
            if args[0]=='im':raise OSError('lost response')
            return dict(items=[dict(uuid=self.e.store.list('outbox')[0]['id'],message_id='om_receipt')])
        f=Feishu(self.e,dict(enabled=True,chats=[dict(chat_id='oc_test')]))
        f.call=cli;f.flush();self.assertEqual(self.e.store.list('outbox')[0]['state'],'unknown')
        self.now+=300;f.flush();self.assertEqual(self.e.store.list('outbox')[0]['state'],'sent')
        self.assertEqual(sum(a[0]=='im' for a in sent),1)
        self.assertFalse(self.e.store.list('observer_feedback'));self.assertEqual(self.o.k()['revision'],1)
        with self.e.store.transaction() as tx:tx.put('triggers','human-feedback',dict(id='human-feedback',admitted=True,actor='ou_alice'))
        self.o.feedback(self.e,rid,'human-feedback','Alice understood the uncertainty')
        self.assertEqual(len(self.e.store.list('observer_feedback')),1);self.assertEqual(self.o.k()['revision'],1)

    def test_separate_database_does_not_wait_for_worker_write_lock(self):
        started=time.monotonic()
        with self.e.store.transaction():
            self.push(self.evidence());self.now+=30;batch=self.next()['batch'];self.finish(batch)
        self.assertLess(time.monotonic()-started,1)
        self.assertNotEqual(self.e.store.path,self.o.store.path)

    def test_provider_incident_persists_until_verified_recovery(self):
        self.o.health(*self.args(),'unavailable','fixture provider outage')
        recovered=Observer(self.root/'observer.db',clock=lambda:self.now)
        self.assertEqual(recovered.store.list('observer_incidents')[0]['state'],'open')
        self.o.health(*self.args(),'verified_recovered','actual model response and committed judgment')
        self.assertEqual(self.o.store.list('observer_incidents')[0]['state'],'resolved')

    def test_outward_length_rejection_keeps_full_report_and_review_retryable(self):
        self.k(); ids=self.push(self.evidence(content='full scientific evidence'))
        self.now+=30; batch=self.next()['batch']
        report=self.report(ids)
        report['limitations']='complete internal limitations '+('detail '*200)
        rid=self.finish(batch,disposition='report',report=report)['report_id']
        self.o.router_inbox(self.e)
        decision=dict(action='send',k_revision=1,reason='Internal scientific review',job_id='A',people=['alice'],text='word '*101)
        before=self.e.store.get('observer_inbox',rid)
        with self.assertRaisesRegex(ValueError,'100 words'):self.o.review(self.e,rid,decision)
        self.assertEqual(self.e.store.get('observer_inbox',rid),before)
        self.assertFalse(self.e.store.list('outbox'))
        decision['text']='开发集结果提示值得追加对照实验，目前尚不能判断泛化能力。'
        self.o.review(self.e,rid,decision)
        outward=self.e.store.list('outbox')[0]
        self.assertEqual(outward['purpose'],'research_signal')
        self.assertEqual(outward['reference'],rid)
        self.assertEqual(outward['receipt_policy'],'reconcile_unknown')
        self.assertEqual(self.e.store.get('observer_inbox',rid)['report']['limitations'],report['limitations'])
        self.assertFalse(self.e.store.list('inputs'))

    def test_crash_inside_commit_rolls_back_notes_cursor_and_report_together(self):
        self.k();ids=self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        with self.o.store.connect() as conn:
            conn.execute("CREATE TRIGGER crash_before_receipt BEFORE INSERT ON records WHEN NEW.kind='observer_commits' BEGIN SELECT RAISE(ABORT,'fixture crash'); END")
        value=self.judgment(batch,disposition='report',report=self.report(ids))
        with self.assertRaises(sqlite3.IntegrityError):self.o.commit(*self.args(),value)
        state=self.o.store.get('observers','default')
        self.assertEqual(state['cursors'],{});self.assertEqual(state['active']['id'],batch['id'])
        self.assertFalse(self.o.store.list('observer_reports'))
        with self.o.store.connect() as conn:conn.execute('DROP TRIGGER crash_before_receipt')
        self.o.commit(*self.args(),value);self.assertEqual(len(self.o.store.list('observer_reports')),1)

    def test_router_receive_crash_before_ack_is_deduplicated(self):
        self.k();ids=self.push(self.evidence());self.now+=30;batch=self.next()['batch']
        rid=self.finish(batch,disposition='report',report=self.report(ids))['report_id']
        with patch.object(self.o.store,'transaction',side_effect=OSError('crash before acknowledgement')):
            with self.assertRaises(OSError):self.o.router_inbox(self.e)
        self.assertEqual(len(self.e.store.list('observer_inbox')),1)
        self.assertEqual(self.o.store.get('observer_reports',rid)['state'],'pending')
        self.o.router_inbox(self.e)
        self.assertEqual(len(self.e.store.list('observer_inbox')),1)
        self.assertEqual(self.o.store.get('observer_reports',rid)['state'],'received')


if __name__=='__main__':unittest.main()
