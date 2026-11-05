"""Deterministic first-session recovery budgets; no model or candidate execution."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
from tools import common as c
from modules.research.notebook_collaboration import recovery, sessions, runtime


def at(seconds):
    return dt.datetime.fromtimestamp(1793800000+seconds, dt.timezone.utc).isoformat()


class Handoff(unittest.TestCase):
    def setUp(self):
        self.proof = dict(schema_version=1, kind='interrupted-first-session', destination_run_id='fixture/004',
                          source_run_id='fixture/003', initialization_sha='f'*64, researchers=3,
                          rounds_per_researcher=8, session_wall_s=2400, original_deadline=1793801000,
                          blocked_source_sha256=['a'*64], actors={
                              f'r{i}': dict(generation=1, charged_active_s=500+i*100) for i in range(1, 4)})
        self.cfg = dict(run_id='fixture/004', initialization={'sha':'f'*64},
                        budgets=dict(researchers=3, rounds_per_researcher=8, session_wall_s=2400, run_wall_s=999999),
                        modules={'research': {'params': {'recovery_proof': {'path':'proof.json','sha':'b'*64}}}})

    def read(self, *args):
        return json.dumps(self.proof).encode()

    def session(self, actor='r1', generation=1):
        return dict(actor=actor, generation=generation, started_at=at(0), activated_at=at(0),
                    session_id=actor+'-r001', status='active', evaluation_waits={}, preflight_waits={},
                    runtime_waits={'pause':dict(started_at=at(10),finished_at=at(90))})

    def test_different_remaining_budgets_preserved_and_only_charged_once(self):
        with patch.object(c,'read_pinned',side_effect=self.read):
            for actor, expected in [('r1',1780),('r2',1680),('r3',1580)]:
                s=self.session(actor)
                self.assertEqual(sessions.research_time(self.cfg,s,at(100))['remaining_s'],expected)
                s['generation']=2
                self.assertEqual(sessions.research_time(self.cfg,s,at(100))['remaining_s'],2380)

    def test_launch_wait_does_not_reset_carried_budget(self):
        with patch.object(c,'read_pinned',side_effect=self.read):
            s=self.session();s['activated_at']=None
            self.assertEqual(sessions.research_time(self.cfg,s,at(100))['remaining_s'],1800)

    def test_original_deadline_blocks_admission_even_with_unused_budget(self):
        with patch.object(c,'read_pinned',side_effect=self.read):
            self.assertEqual(recovery.remaining(self.cfg,at(900)),100)
            with self.assertRaisesRegex(ValueError,'original recovery'):
                runtime._admit(self.cfg,{'sessions':[self.session()]},'r1-r001',at(1001))

    def test_lost_result_snapshot_cannot_be_replayed_with_new_id(self):
        with patch.object(c,'read_pinned',side_effect=self.read):
            with self.assertRaisesRegex(ValueError,'without replay'):
                recovery.replay_guard(self.cfg,'a'*64)
            recovery.replay_guard(self.cfg,'c'*64)

    def test_bad_handoff_cannot_add_time_rounds_or_change_initialization(self):
        original=copy.deepcopy(self.proof)
        for field,value in [('rounds_per_researcher',9),('initialization_sha','x'*64),('destination_run_id','fixture/005')]:
            self.proof=copy.deepcopy(original);self.proof[field]=value
            with patch.object(c,'read_pinned',side_effect=self.read),self.assertRaises(ValueError):
                recovery.charge(self.cfg,self.session())
        for invalid in [-1,2401,float('inf')]:
            self.proof=copy.deepcopy(original);self.proof['actors']['r1']['charged_active_s']=invalid
            with patch.object(c,'read_pinned',side_effect=self.read),self.assertRaises(ValueError):
                recovery.charge(self.cfg,self.session())
        self.proof=original
        with patch.object(c,'read_pinned',side_effect=self.read),self.assertRaisesRegex(ValueError,'actor'):
            recovery.charge(self.cfg,self.session('outsider'))

    def test_handoff_context_is_actor_specific(self):
        self.proof['actors']['r1']['context']={'path':'research/recovery/r1.json','sha':'c'*64}
        with patch.object(c,'read_pinned',side_effect=self.read),patch.object(c,'contained') as path,patch.object(c,'sha',return_value='c'*64),patch.object(c,'read_json',return_value={'actor':'r2','source_run_id':'fixture/003'}):
            path.return_value.stat.return_value.st_size=100
            with self.assertRaisesRegex(ValueError,'different researcher'):
                recovery.context(Path('/fixture'),self.cfg,self.session())


if __name__=='__main__':unittest.main(verbosity=2)
