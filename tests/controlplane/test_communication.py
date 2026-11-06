"""Offline state-machine/history fixtures; no models, research, or live IM.

The segmentation check runs an actual local Node process. Transport is mocked.
Reviewed history rewrites are expectations, not evidence of model compliance.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from controlplane.communication import checked_text, notification, word_count, subject
from controlplane.engine import Engine, Conflict
from controlplane.store import Store
from controlplane.feishu import Feishu

HISTORY = json.loads((Path(__file__).parents[1]/'fixtures/communication/history.json').read_text())['cases']


class CommunicationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'router.db')
        self.config = dict(default_host='dev', default_version='v1', sources=dict(feishu=dict(
            chats=['chat'], allowed_users=['ou_owner'], allowed_stages=['initialize', 'research', 'terminal'])))
        self.engine = Engine(self.store, self.config, lambda:1000)
        self.engine.register_version(dict(id='v1', digest='a'*64, state='ready', validation={'passed':True}))

    def trigger(self, key='request'):
        return self.engine.receive(dict(id=key, source='feishu', thread='chat:thread', actor='ou_owner',
            text='Please initialize', channel=dict(chat_id='chat', message_id='original-'+key)))

    def start(self):
        self.trigger()
        result = self.engine.decide('request', dict(action='create', title='样例研究', scope='original scope', reason='fixture'))
        self.jid = result['job_id']
        self.attempt = self.engine.claim('dev', self.jid)['attempt']
        self.event('started', 'started', dict(pid=123, native_id='fixture-session'))
        return self.store.get('jobs', self.jid)

    def event(self, key, kind, data):
        return self.engine.worker_event(self.attempt['id'], self.attempt['token'], dict(id=key, type=kind, data=data))

    def messages(self, purpose):
        return [m for m in self.store.list('outbox') if m.get('purpose') == purpose]

    def test_real_node_segmentation_and_conservative_offline_fallback(self):
        self.assertEqual(word_count('hello world'), 2)
        self.assertLess(word_count('当前研究任务正在运行'), len('当前研究任务正在运行'))
        self.assertEqual(checked_text('word '*100), 'word '*100)
        for text in ['word '*101, '中文研究 '*101, '[报告](https://example.com/'+'path/'*105+')']:
            with self.assertRaisesRegex(ValueError, '100 words'): checked_text(text)
        with patch('controlplane.communication.subprocess.run', side_effect=FileNotFoundError):
            self.assertEqual(word_count('测试离线 abc xyz'), 6)
            self.assertEqual(checked_text('执行端失联，进度尚未确认。'), '执行端失联，进度尚未确认。')

    def test_unrelated_internal_data_never_enters_status(self):
        job = self.start()
        detail = 'INTERNAL receipt=/private/tmp/test sha=secret-placeholder; 未重放评测。'*50
        self.event('progress', 'progress', dict(summary=detail, run_id='internal-study/001'))
        self.assertEqual(len(self.store.list('outbox')), 1)
        current = self.store.get('jobs', self.jid)
        self.assertEqual(current['progress']['summary'], detail)
        self.trigger('status')
        self.engine.decide('status', dict(action='status', job_id=self.jid, reason='human asks'))
        message = self.messages('answer')[0]
        self.assertEqual(message['text'], '「样例研究」的初始化正在运行。')
        self.assertEqual(message['channel']['message_id'], 'original-status')
        for name in ('version_id', 'version_digest', 'mandate', 'handoff', 'attempt_id'):
            self.assertEqual(current[name], job[name])

    def test_reply_rejection_is_atomic_then_rewrite_commits_once(self):
        self.trigger('question')
        bad = dict(action='reply', reason='internal only', text='word '*101)
        with self.assertRaisesRegex(ValueError, '100 words'): self.engine.decide('question', bad)
        self.assertIsNone(self.store.get('decisions', 'question'))
        self.assertEqual(self.store.get('triggers', 'question')['state'], 'pending')
        self.assertFalse(self.store.list('outbox'))
        good = dict(bad, text='目前没有正在运行的遗忘蚂蚁实验。')
        result = self.engine.decide('question', good)
        self.assertEqual(self.engine.decide('question', bad), result)
        self.assertEqual(len(self.messages('answer')), 1)
        self.assertFalse(self.store.list('jobs'))

    def test_issue_and_wait_share_one_question_keep_complete_audit(self):
        job = self.start()
        question = '基线执行结果尚未确认，请维护方核对原执行记录。'
        detail = '完整诊断：'+ 'internal receipt and stack; '*100
        issue = dict(request=detail, evidence=['receipt-original'], question=question, attention_id='baseline-unknown')
        self.event('issue', 'issue', issue)
        self.event('wait', 'waiting_input', dict(question=question, attention_id='baseline-unknown'))
        self.event('wait-again', 'waiting_input', dict(question=question, attention_id='baseline-unknown'))
        self.assertEqual(len(self.messages('attention')), 1)
        request = self.store.list('framework_requests')[0]
        self.assertEqual(request['request'], detail)
        self.assertEqual(request['evidence'], ['receipt-original'])
        self.assertEqual(request['notification_id'], self.messages('attention')[0]['id'])
        current = self.store.get('jobs', self.jid)
        self.assertEqual(current['state'], 'waiting_input')
        self.assertEqual(current['mandate'], job['mandate'])
        self.assertEqual(len(self.store.list('jobs')), 1)
        self.event('changed', 'waiting_input', dict(question='记录已核对，缺少输入数据；请提供数据。', attention_id='baseline-unknown'))
        self.assertEqual(len(self.messages('attention')), 2)

    def test_bad_question_cannot_change_worker_state_or_drop_diagnostic(self):
        self.start()
        with self.assertRaisesRegex(ValueError, '100 words'):
            self.event('wait', 'waiting_input', dict(question='word '*101))
        self.assertEqual(self.store.get('jobs', self.jid)['state'], 'running')
        # Same event can be corrected because no successful receipt was committed.
        self.event('wait', 'waiting_input', dict(question='请提供输入数据。'))
        self.assertEqual(self.store.get('jobs', self.jid)['state'], 'waiting_input')

    def test_unsent_fault_merges_with_question_but_attempted_text_is_immutable(self):
        self.start()
        self.event('fault', 'fault', dict(kind='execution_unknown', message='full diagnostic'))
        question = '执行结果尚未确认，请维护方核对。'
        self.event('issue', 'issue', dict(request='full diagnostic', question=question, attention_id='incident:execution_unknown'))
        self.event('wait', 'waiting_input', dict(question=question, attention_id='incident:execution_unknown'))
        self.assertEqual(len(self.store.list('outbox')), 2)  # acknowledgement + one action
        self.assertEqual(self.messages('attention')[0]['text'], question)
        self.assertEqual(self.store.list('incidents')[0]['message'], 'full diagnostic')
        with self.store.transaction() as tx:
            message = self.messages('attention')[0]
            message.update(state='pending', attempts=1)
            tx.put('outbox', message['id'], message)
        changed = '执行记录已确认，需要维护方修复环境。'
        self.event('new-action', 'waiting_input', dict(question=changed, attention_id='incident:execution_unknown'))
        self.assertEqual(self.store.get('outbox', message['id'])['text'], question)
        self.assertEqual(len(self.messages('attention')), 2)

    def test_faults_remain_visible_without_router_and_recovery_requires_proof(self):
        self.start()
        self.engine.router_health('error', 'model unavailable')
        self.engine.tool_begin(self.attempt['id'], self.attempt['token'], 'original', 'solver_evaluate', {'id':'original'})
        raw = 'provider diagnostic with internal path '+ '/private/tmp/trace '*30
        for i in range(3): self.event('fault-'+str(i), 'fault', dict(kind='network', message=raw))
        self.assertEqual(len(self.messages('incident')), 1)
        self.assertEqual(self.store.list('incidents')[0]['message'], raw)
        self.assertEqual(self.store.list('incidents')[0]['count'], 3)
        with self.assertRaises(Conflict): self.event('recovery', 'recovered', dict(verified=False))
        self.assertEqual(self.store.list('incidents')[0]['state'], 'open')
        with self.assertRaises(Conflict): self.event('recovery', 'recovered', dict(verified=True))
        self.assertFalse(self.messages('recovery'))
        self.assertEqual(len(self.store.list('operations')), 1)

    def test_notification_gate_and_transport_reject_overlong_new_messages(self):
        with self.assertRaises(ValueError): notification('progress', 'progress', 'source')
        with self.assertRaises(ValueError): notification('hello', 'answer', '')
        self.trigger('answer'); self.engine.decide('answer', dict(action='reply', reason='fixture', text='收到。'))
        message = self.messages('answer')[0]
        with self.store.transaction() as tx:
            message['text'] = 'word '*101; tx.put('outbox', message['id'], message)
        f = Feishu(self.engine, dict(enabled=True, chats=[dict(chat_id='chat')]))
        calls=[]; f.call=lambda args, **kw: calls.append(args) or {'message_id':'receipt'}
        f.flush()
        self.assertFalse(calls)
        self.assertEqual(self.store.get('outbox', message['id'])['state'], 'needs_rewrite')
        # Existing queued records are not retroactively migrated by this change.
        with self.store.transaction() as tx:
            message.pop('communication_version'); message['state']='pending'; tx.put('outbox', message['id'], message)
        f.flush(); self.assertEqual(len(calls), 1)

    def test_transport_freezes_wording_before_inflight_issue_arrives(self):
        self.start()
        with self.store.transaction() as tx:
            for message in tx.list('outbox'):
                message['state']='sent';tx.put('outbox',message['id'],message)
        self.event('fault', 'fault', dict(kind='execution_unknown', message='raw failure'))
        incident = self.messages('incident')[0]
        original = incident['text']
        f = Feishu(self.engine, dict(enabled=True, chats=[dict(chat_id='chat')]))
        sent=[]
        def transport(args, **kwargs):
            sent.append(args)
            if incident['id'] in args:
                self.event('during-send', 'issue', dict(request='full explanation',
                    question='结果尚未确认，请维护方核对原始记录。',attention_id='incident:execution_unknown'))
            return {'message_id':'receipt'}
        f.call=transport; f.flush()
        frozen=self.store.get('outbox',incident['id'])
        self.assertEqual(frozen['text'],original)
        self.assertEqual(frozen['state'],'sent')
        self.assertEqual(len(self.messages('attention')),1)
        self.assertNotEqual(self.messages('attention')[0]['id'],incident['id'])
        self.assertEqual(self.messages('attention')[0]['state'],'pending')

    def test_same_attention_id_in_another_job_cannot_suppress_a_question(self):
        self.start()
        self.event('ask', 'waiting_input', dict(question='请提供数据。',attention_id='data'))
        self.trigger('second')
        result=self.engine.decide('second',dict(action='create',title='另一研究',scope='scope',reason='new request'))
        self.attempt=self.engine.claim('dev',result['job_id'])['attempt']
        self.event('start','started',dict(pid=456,native_id='other-fixture'))
        self.event('ask', 'waiting_input', dict(question='请提供数据。',attention_id='data'))
        self.assertEqual(len(self.messages('attention')),2)

    def test_long_title_cannot_break_fault_delivery(self):
        job = self.start(); job['title'] = 'very long title '*100
        self.assertEqual(subject(job), '当前初始化任务')
        with self.store.transaction() as tx:
            self.engine._incident(tx, job, 'host_lost', 'full evidence')
        self.assertIn('无法确认', self.messages('incident')[0]['text'])
        checked_text(self.messages('incident')[0]['text'])

    def test_synthetic_messages_through_actual_offline_delivery_paths(self):
        self.start()
        for case in HISTORY:
            with self.subTest(case=case['id']):
                original, rewrite = case['original'], case['reviewed_rewrite']
                self.assertLess(word_count(rewrite), word_count(original))
                checked_text(rewrite)
                if case['kind'] == 'reply':
                    self.trigger(case['id'])
                    if word_count(original) > 100:
                        with self.assertRaises(ValueError):
                            self.engine.decide(case['id'], dict(action='reply', reason='history', text=original))
                    self.engine.decide(case['id'], dict(action='reply', reason='history', text=rewrite))
                    self.assertEqual(self.store.get('decisions', case['id'])['decision']['text'], rewrite)
                elif case['kind'] == 'issue':
                    self.event(case['id'], 'issue', dict(request=original, evidence=[case['source_sha256']],
                        question=rewrite, attention_id=case['id']))
                    self.event(case['id']+'-wait', 'waiting_input', dict(question=rewrite, attention_id=case['id']))
                    self.assertEqual(self.store.list('framework_requests')[0]['request'], original)
                else:
                    with self.assertRaises(ValueError):
                        self.event(case['id'], 'waiting_input', dict(question=original))
                    self.event(case['id'], 'waiting_input', dict(question=rewrite, attention_id=case['id']))
                self.assertEqual(sum(m['text'] == rewrite for m in self.store.list('outbox')), 1)
        self.assertEqual(len(self.store.list('jobs')), 1)
        self.assertFalse(self.store.list('operations'))

    def test_synthetic_outward_outputs_pass_gate_without_replaying_models(self):
        # Public synthetic fixtures; no model call or historical conversation.
        review=json.loads((Path(__file__).parents[1]/'fixtures/communication/model-review.json').read_text())
        for case in review['cases']:
            with self.subTest(case=case['id']):
                text=checked_text(case['outward_text'])
                self.assertEqual(word_count(text),case['outward_words'])
                self.assertLess(case['outward_chars'],case['original_chars'])
                for marker in ('【','request_sha256','run_id','study_doctor','/private/','<hash>','证据已保留','未重放','未改动'):
                    self.assertNotIn(marker,text)


if __name__ == '__main__': unittest.main()
