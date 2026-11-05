import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from tools.common import available_wall_s
from modules.research.notebook_collaboration.sessions import research_time


def at(seconds):return '2026-01-01T00:%02d:%02d+00:00' % divmod(seconds,60)
def session(start=0,end=None,waits=()):return dict(started_at=at(start),activated_at=at(start),finished_at=at(end) if end is not None else None,runtime_waits={str(i):dict(started_at=at(a),finished_at=at(b) if b is not None else None) for i,(a,b) in enumerate(waits)})
class ClockCase(unittest.TestCase):
    def setUp(self):self.cfg=dict(created_at=at(0),budgets=dict(run_wall_s=100,session_wall_s=50))
    def test_quota_wait_preserves_original_session_budget(self):
        result=research_time(self.cfg,session(waits=[(10,90)]),at(100))
        self.assertEqual(result['active_research_s'],20);self.assertEqual(result['remaining_s'],30)
        self.assertEqual(result['pending_preflight_ids'],[])
    def test_total_outage_excluded_but_one_working_researcher_still_counts(self):
        state=dict(sessions=[session(waits=[(10,90)]),session(waits=[(30,80)])])
        self.assertEqual(available_wall_s(self.cfg,state,at(100)),50)
    def test_overlapping_retries_do_not_inflate_credit(self):
        state=dict(sessions=[session(waits=[(10,80),(20,90)])])
        self.assertEqual(available_wall_s(self.cfg,state,at(100)),20)
    def test_coordinator_outage_only_excludes_time_without_active_researchers(self):
        state=dict(sessions=[session(0,50)],supervisor_waits=dict(a=dict(started_at=at(10),finished_at=at(90))))
        self.assertEqual(available_wall_s(self.cfg,state,at(100)),60)
    def test_no_waits_preserves_original_deadline(self):
        self.assertEqual(available_wall_s(self.cfg,dict(sessions=[]),at(100)),100)

if __name__=='__main__':unittest.main()
