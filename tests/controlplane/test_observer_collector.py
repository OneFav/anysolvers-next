"""Fixture records through real Node reader processes; no model or evaluator."""
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch
from controlplane.observer import Observer
from controlplane.observer_collector import Collector


def compressed(records):
    return subprocess.check_output(['node','-e',
        "let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>process.stdout.write(require('zlib').zstdCompressSync(Buffer.from(s))))"],
        input=('\n'.join(json.dumps(r) for r in records)+'\n').encode())


class CollectorCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.o=Observer(self.root/'observer.db')
        self.job=dict(id='A',stage='research',state='running',project_id='p',host_id='dev')
        self.o.register_job(self.job)
        self.config=dict(_base=str(self.root),worker_host=dict(state_root='host',work_root='projects'))
        self.records=[]
        def post(name,body):
            if name=='observer-jobs':return [self.job]
            self.records.extend(body['records']);return self.o.ingest(body['records'])
        self.post=post
        self.c=Collector(self.config,post)
        self.workspace=self.root/'projects/p';self.workspace.mkdir(parents=True)
        binding=self.root/'host/A/session/run.json';binding.parent.mkdir(parents=True);binding.write_text('{"run_id":"study/001"}')
        self.file=self.root/'host/A/dsh-sessions/workspace/session-A/session.v4.jsonl.zstd';self.file.parent.mkdir(parents=True)
        self.header=dict(type='session',version=4,id='session-A',createdAt=1,agentPreset='anysolvers-researcher')

    def event(self,seq,kind,data):return dict(seq=seq,time=1000+seq,type=kind,data=data)

    def test_explicit_idea_tool_pair_and_sources_without_worker_requests_or_final_reads(self):
        events=[self.event(0,'assistant/message',dict(message=dict(content=[dict(type='reasoning',text='new idea before artifact')]))) ,
            self.event(1,'tool/call',dict(callId='call',name='solver_result',arguments='{"id":"eval-1"}')),
            self.event(2,'tool/result',dict(message=dict(toolCallId='call',content=[dict(type='text',text='measured development result')]))),
            self.event(3,'tool/call',dict(callId='final',name='final_measure',arguments='{"forbidden":"final-data"}')),
            self.event(4,'tool/result',dict(message=dict(toolCallId='final',content=[dict(type='text',text='forbidden-final-sentinel')])))]
        self.file.write_bytes(compressed([self.header])+compressed(events))
        source=self.workspace/'runs/study/001/research/candidates/one/solver.py';source.parent.mkdir(parents=True);source.write_text('# fixture source; never executed\n')
        final=self.workspace/'runs/study/001/final/secret.json';final.parent.mkdir(parents=True);final.write_text('forbidden-final-sentinel')
        before=self.file.read_bytes();self.c.tick()
        kinds={r['kind'] for r in self.records}
        self.assertTrue({'analysis','tool_input','tool_result','source'}<=kinds)
        self.assertNotIn('forbidden-final-sentinel',json.dumps(self.records))
        self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(self.c.metrics['worker_locks'],0);self.assertEqual(self.c.metrics['worker_flushes'],0)
        # An unchanged log is stat-checked, not decompressed again.
        with patch('controlplane.observer_collector.subprocess.run',side_effect=AssertionError('full reread')):
            self.c.stream(self.job,'study/001',self.file)

    def test_round_idea_and_selection_decision_are_observable_without_final(self):
        events=[self.event(0,'tool/call',dict(callId='selection',name='study_selection_submit',arguments='{"idea_text":"Change the research route"}')),
                self.event(1,'tool/result',dict(message=dict(toolCallId='selection',content=[dict(type='text',text='sealed-direction')])))]
        self.file.write_bytes(compressed([self.header])+compressed(events))
        start=self.workspace/'runs/study/001/research/ideas/alice-r002.json'
        start.parent.mkdir(parents=True);start.write_text('{"idea_text":"Change the research route","generation":2}')
        report=self.workspace/'runs/study/001/research/reports/alice-r002.json'
        report.parent.mkdir(parents=True);report.write_text('{"method":"Enumerate feasible subsets","score":14}')
        for _ in range(4):self.c.tick()
        self.assertTrue(any(r['object']=='study_selection_submit' and r['kind']=='tool_input' for r in self.records))
        self.assertTrue(any('/ideas/' in r['object'] and 'Change the research route' in r['content'] for r in self.records))
        self.assertTrue(any('/reports/' in r['object'] and 'Enumerate feasible subsets' in r['content'] for r in self.records))
        self.assertEqual(self.c.metrics['worker_locks'],0)

    def test_upload_crash_replays_stable_ids_and_retains_source_cursor(self):
        self.file.write_bytes(compressed([self.header])+compressed([
            self.event(0,'assistant/message',dict(message=dict(content=[dict(type='text',text='idea A')]))),
            self.event(1,'assistant/message',dict(message=dict(content=[dict(type='text',text='idea B')])))]))
        calls=0
        def failure(name,body):
            nonlocal calls
            self.post(name,body);calls+=1
            if calls==2:raise OSError('crash after upload before cursor checkpoint')
        self.c.post=failure
        with self.assertRaises(OSError):self.c.stream(self.job,'study/001',self.file)
        self.assertFalse(self.c.saved['streams'])
        self.c.post=self.post;self.c.stream(self.job,'study/001',self.file)
        self.assertEqual(self.c.saved['streams'][str(self.file)]['next_seq'],2)
        with self.o.store.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],2)

    def test_torn_frame_waits_and_appended_records_arrive_without_full_reread(self):
        first=compressed([self.header]);tail=compressed([self.event(0,'assistant/message',dict(message=dict(content=[dict(type='text',text='unfinished until frame complete')])) )])
        self.file.write_bytes(first+tail[:8]);self.c.stream(self.job,'study/001',self.file)
        self.assertEqual(self.c.saved['streams'][str(self.file)]['offset'],len(first))
        with self.file.open('ab') as stream:stream.write(tail[8:])
        self.c.stream(self.job,'study/001',self.file)
        self.assertEqual(len(self.records),1)
        self.assertIn('unfinished until frame complete',self.records[0]['content'])

    def test_collector_does_not_take_the_real_research_metadata_lock(self):
        if __import__('os').name=='nt':self.skipTest('POSIX metadata lock contract')
        lock=self.workspace/'runs/study/001/.mutation.lock'
        lock.parent.mkdir(parents=True,exist_ok=True)
        self.file.write_bytes(compressed([self.header])+compressed([self.event(0,'assistant/message',dict(message=dict(content=[dict(type='text',text='fixture')])))]))
        # This is the exact lock path/protocol from runtime/tools/common.py:locked.
        code="import fcntl,sys,time; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(10)"
        holder=subprocess.Popen([sys.executable,'-B','-c',code,str(lock)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(),'ready')
            before=time.monotonic();self.c.tick()
            self.assertLess(time.monotonic()-before,2)
        finally:
            holder.terminate();holder.wait(timeout=5);holder.stdout.close()
        self.assertTrue(self.records)


if __name__=='__main__':unittest.main()
