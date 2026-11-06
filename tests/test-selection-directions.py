"""Deterministic filesystem/provenance contracts, no model or candidate execution."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'packages/research-runtime/python')]
from tools import common as c
from tools.execution import template
from modules.research.notebook_collaboration import runtime as research, client
from modules.selections.research_directions import policy as selection
from modules.selections.research_directions import reports
from tools import dsh_bridge
from unittest.mock import patch
from dsh import stage

class Directions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.run=Path(self.tmp.name).resolve()
        self.cfg=template('defaults/smoke.yaml')
        self.cfg.update(run_id='fixture/001',created_at=c.utc())
        self.cfg['task']={'objective':{'direction':'maximize','metric':'total_value'}}
        self.state=dict(config_sha=c.digest(self.cfg),sessions=[],selection=None,evaluations={},deliveries={k:{} for k in ['candidate','iteration','dataset','ideas']})
        self.state['deliveries']['ideas']['initial']={'manifest':{'ideas':[dict(idea_id='seed',hypothesis='Greedy may fail on packing.',mechanism='Inspect small instances.',first_change='Test a competing route.',risks='Small sample.',source_refs=[])]}}

    def begin(self,actor,iteration=None):
        return research.begin_session(self.run,self.cfg,self.state,actor,'researcher',None if iteration else 'seed',iteration)

    def finish(self,s,report=True,selected=None):
        research.activate_session(self.run,self.cfg,self.state,s['actor'],'fixture-native-'+s['session_id'])
        research._put(self.run,self.cfg,self.state,'notebook','note-'+s['session_id'],s['session_id'],dict(actor=s['actor'],id='note-'+s['session_id'],session_id=s['session_id'],candidate_id=selected,evidence_ids=[],learned='The starting approach has an unresolved limitation.',failed_or_uncertain='No performance claim; test another route.',next_iteration_id=None))
        research.end_session(self.run,self.cfg,self.state,s['actor'],'completed',s['native_id'])
        if report:reports.report_submit(self.run,self.cfg,self.state,s['session_id'],{'candidate':None})

    def candidate(self,s,cid):
        source='research/candidates/'+cid+'/solver.py'
        path=self.run/source;path.parent.mkdir(parents=True)
        path.write_text('def solve(case):\n    return {"chosen": []}\n')
        self.state['deliveries']['candidate'][cid]={'manifest':dict(candidate_id=cid,session_id=s['session_id'],source_file=source),'files':{source:c.sha(path)}}

    def evaluation(self,cid,eid='eval-one',status='completed',purpose='anchor',probe=False):
        self.state['deliveries']['dataset'][purpose]={'manifest':{'purpose':purpose}}
        path='research/evaluations/'+eid+'/summary.json';details='research/evaluations/'+eid+'/results.json'
        rec=self.state['deliveries']['candidate'][cid]
        summary=dict(evaluation_id=eid,status=status,dataset_id=purpose,dataset_purpose=purpose,candidate_id=cid,
                     source_sha=rec['files'][rec['manifest']['source_file']],details_file=details,score=12 if status=='completed' else None,
                     all_valid=status=='completed',valid_cases=4 if status=='completed' else 0,total_cases=4,completed_cases=4,
                     infrastructure_error=False,cleanup_confirmed=True,probe=probe)
        c.write_json(self.run/path,summary);c.write_json(self.run/details,[])
        self.state['evaluations'][eid]=path
        self.state.setdefault('evaluation_hashes',{})[eid]={'summary_sha':c.sha(self.run/path),'results_sha':c.sha(self.run/details)}

    def prose(self,cid):
        return {'candidate':dict(candidate_id=cid,title='空选择基线',method='读取输入后返回空的物品选择列表，不进行搜索。',limitations='未尝试提高目标值。')}

    def decision(self,actor):
        slot=selection.pending(self.run,self.cfg,self.state,actor)['pending'][0]
        return dict(schema_version=2,iteration_id=slot['expected_iteration_id'],actor=actor,generation=slot['generation'],based_on_session=slot['based_on_session'],source_notebook_id=slot['source_notebook_id'],parent_candidate=None,source_idea_ids=['seed'],evidence_ids=[],history_iteration_ids=[],observations='No measured success; original mechanism is uncertain.',hypothesis='An exact state-space method can answer the feasibility question.',expected_effect='Distinguish approximation error from input ambiguity.',test_plan='Compare against baseline on the sealed development cases.',risks=['State explosion'],rationale='A distinct route addresses the unresolved limitation.',idea_text='Investigate exact state-space search from scratch; test whether the original heuristic loses feasible solutions.',research_question='Can state-space search resolve the uncertain cases?',exploration_plan=['Construct an independent method.','Evaluate within the accounted evaluator.'],stop_conditions=['Stop this route if state growth exceeds fixed limits.'],direction_type='pivot')

    def test_two_by_two_independent_directions_and_immutable_starting_ideas(self):
        a=self.begin('alice');b=self.begin('bob')
        self.finish(a)
        decision=self.decision('alice')
        selection.submit(self.run,self.cfg,self.state,'alice',decision)
        self.assertTrue(selection.submit(self.run,self.cfg,self.state,'alice',decision)['cached'])
        a2=self.begin('alice',decision['iteration_id'])
        self.assertEqual(b['status'],'active','no global round barrier')
        self.assertIsNone(a2['parent_candidate'])
        self.assertEqual(a2['starting_idea']['idea_text'],decision['idea_text'])
        self.finish(a2);self.finish(b)
        d2=self.decision('bob');selection.submit(self.run,self.cfg,self.state,'bob',d2)
        b2=self.begin('bob',d2['iteration_id']);self.finish(b2)
        research.audit(self.run,self.cfg,self.state)
        self.assertEqual(len(list((self.run/'research/ideas').glob('*.json'))),4)
        self.assertEqual([s['starting_idea']['kind'] for s in self.state['sessions']],['initial','initial','selection','selection'])
        self.assertEqual(selection.pending(self.run,self.cfg,self.state)['pending'],[])
        original=c.read_json(self.run/a2['starting_idea']['path'])
        self.assertEqual(original['content_sha'],c.digest(decision))
        (self.run/a2['starting_idea']['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'starting idea'):research.audit(self.run,self.cfg,self.state)

    def test_no_direction_before_completion_and_wrong_actor_and_overwrite_rejected(self):
        a=self.begin('alice');self.assertEqual(selection.pending(self.run,self.cfg,self.state)['pending'],[])
        self.finish(a);d=self.decision('alice');selection.submit(self.run,self.cfg,self.state,'alice',d)
        b=self.begin('bob');self.finish(b)
        with self.assertRaisesRegex(ValueError,'different actor'):self.begin('bob',d['iteration_id'])
        changed=copy.deepcopy(d);changed['idea_text']='Overwrite original.'
        with self.assertRaisesRegex(ValueError,'cannot be changed'):selection.submit(self.run,self.cfg,self.state,'alice',changed)
        changed=copy.deepcopy(d);changed['iteration_id']='another-id'
        with self.assertRaisesRegex(ValueError,'deterministic'):selection.submit(self.run,self.cfg,self.state,'alice',changed)

    def test_researcher_cannot_author_selection_and_no_private_history_or_final_feedback(self):
        a=self.begin('alice');self.finish(a);d=self.decision('alice');selection.submit(self.run,self.cfg,self.state,'alice',d)
        b=self.begin('bob')
        with self.assertRaisesRegex(ValueError,'coordinator owns'):client._deliver(self.run,self.cfg,self.state,b['session_id'],'iteration','unused.json')
        self.finish(b);d2=self.decision('bob');d2['history_iteration_ids']=[d['iteration_id']]
        with self.assertRaisesRegex(ValueError,'private or unavailable'):selection.submit(self.run,self.cfg,self.state,'bob',d2)
        d2['history_iteration_ids']=[];d2['evidence_ids']=['final-eval']
        self.state['deliveries']['dataset']['holdout']={'manifest':{'purpose':'final'}}
        self.state['evaluations']['final-eval']='final/summary.json'
        c.write_json(self.run/'final/summary.json',dict(evaluation_id='final-eval',status='completed',dataset_id='holdout',dataset_purpose='final'))
        with self.assertRaisesRegex(ValueError,'final evaluation'):selection.submit(self.run,self.cfg,self.state,'bob',d2)

    def test_report_only_checkpoint_selected_method_with_exact_results_and_source(self):
        a=self.begin('alice');self.candidate(a,'first');self.candidate(a,'later-better')
        self.evaluation('first');self.evaluation('first','failed-eval','failed');self.evaluation('first','probe-eval',probe=True)
        self.evaluation('first','final-eval',purpose='final')
        self.evaluation('later-better','later-eval')
        other=self.run/self.state['evaluations']['later-eval'];summary=c.read_json(other);summary['score']=999;c.write_json(other,summary)
        self.state['evaluation_hashes']['later-eval']['summary_sha']=c.sha(other)
        self.finish(a,report=False,selected='first')
        with self.assertRaisesRegex(ValueError,'method report first'):selection.submit(self.run,self.cfg,self.state,'alice',self.decision('alice'))
        ctx=reports.report_context(self.run,self.cfg,self.state,a['session_id'])
        self.assertEqual(ctx['source']['candidate_id'],'first');self.assertIn('def solve',ctx['source']['source'])
        self.assertEqual(ctx['facts']['candidate']['candidate_id'],'first')
        self.assertEqual(ctx['facts']['checkpoint']['record_id'],'note-'+a['session_id'])
        with self.assertRaisesRegex(ValueError,'only the final checkpoint'):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('later-better'))
        with self.assertRaisesRegex(ValueError,'only the final checkpoint'):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],{'candidate':None})
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],{'candidates':[self.prose('first')['candidate'],self.prose('later-better')['candidate']]})
        data=self.prose('first')
        ref=reports.report_submit(self.run,self.cfg,self.state,a['session_id'],data)
        result=c.read_json(self.run/ref['path']);facts=result['facts']['candidate']
        self.assertEqual(len(facts['evaluations']),3)
        self.assertEqual([v['score'] for v in facts['evaluations']],[12,None,12])
        self.assertEqual(facts['evaluations'][1]['result_kind'],'diagnostic')
        self.assertTrue(facts['evaluations'][2]['probe'])
        self.assertNotIn('later-better',str(result))
        self.assertTrue((self.run/self.state['deliveries']['candidate']['later-better']['manifest']['source_file']).exists())
        self.assertTrue(reports.report_submit(self.run,self.cfg,self.state,a['session_id'],data)['cached'])
        d=self.decision('alice');selection.submit(self.run,self.cfg,self.state,'alice',d)
        self.begin('alice',d['iteration_id'])
        data['candidate']['method']='Changed.'
        with self.assertRaisesRegex(ValueError,'cannot be changed'):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],data)
        (self.run/ref['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'sealed method report changed'):research.audit(self.run,self.cfg,self.state)

    def test_last_round_report_gate_and_unknown_evaluations(self):
        self.cfg['budgets'].update(researchers=1,rounds_per_researcher=1)
        a=self.begin('alice');self.candidate(a,'first');self.finish(a,report=False,selected='first');self.evaluation('first',status='unknown')
        self.assertEqual(selection.pending(self.run,self.cfg,self.state)['pending'],[])
        self.assertEqual(len(selection.pending(self.run,self.cfg,self.state)['pending_reports']),1)
        with self.assertRaisesRegex(ValueError,'not complete'):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('first'))
        self.evaluation('first')
        path=self.run/self.state['evaluations']['eval-one'];summary=c.read_json(path)
        summary['cleanup_confirmed']=False;c.write_json(path,summary)
        self.state['evaluation_hashes']['eval-one']['summary_sha']=c.sha(path)
        with self.assertRaisesRegex(ValueError,'cleanup must be reconciled'):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('first'))
        self.evaluation('first')
        with patch('tools.common.load_run',return_value=(self.run,self.cfg,self.state)), patch('dsh.terminal_bridge.settled',return_value=True):
            with self.assertRaisesRegex(ValueError,'method report first'):stage.command({'operation':'stage-validate','run_id':'fixture/001','payload':{'stage':'research'}})
            reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('first'))
            with patch('tools.dsh_bridge._evidence',return_value={}):
                self.assertTrue(stage.command({'operation':'stage-validate','run_id':'fixture/001','payload':{'stage':'research'}})['validated'])
        # Later final evidence is excluded, leaving the report intact.
        self.evaluation('first','final-eval',purpose='final');research.audit(self.run,self.cfg,self.state)
        self.evaluation('first','late-development')
        with self.assertRaisesRegex(ValueError,'evidence or session'):research.audit(self.run,self.cfg,self.state)

    def test_report_size_forgery_authority_and_source_tampering(self):
        a=self.begin('alice');self.candidate(a,'first');self.evaluation('first');self.finish(a,report=False,selected='first')
        from jsonschema import ValidationError
        data=self.prose('first');data['candidate']['method']='字'*281
        with self.assertRaises(ValidationError):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],data)
        data=self.prose('first');data['candidate']['score']=999
        with self.assertRaises(ValidationError):reports.report_submit(self.run,self.cfg,self.state,a['session_id'],data)
        for op in ('selection-report-submit','selection-report-context'):
            with self.assertRaisesRegex(ValueError,'coordinator operation'):dsh_bridge._request(dict(schema_version=1,operation=op,run_id='fixture/001',session_id=a['session_id'],payload={}))
        ref=reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('first'))
        source=self.state['deliveries']['candidate']['first']['manifest']['source_file'];(self.run/source).write_text('# changed')
        with self.assertRaisesRegex(ValueError,'source changed'):research.audit(self.run,self.cfg,self.state)

    def test_last_checkpoint_wins_and_can_retain_own_earlier_method(self):
        a=self.begin('alice');self.candidate(a,'chosen');self.candidate(a,'discarded')
        self.evaluation('chosen')
        research.activate_session(self.run,self.cfg,self.state,'alice','fixture-native-'+a['session_id'])
        research._put(self.run,self.cfg,self.state,'notebook','zzz-early-note',a['session_id'],
                      dict(actor='alice',session_id=a['session_id'],candidate_id='discarded'))
        self.finish(a,report=False,selected='chosen')
        # Registration order survives restart; lexicographic ID order is not chronology.
        self.state=c.read_json(self.run/'state.json')
        reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('chosen'))
        d=self.decision('alice');selection.submit(self.run,self.cfg,self.state,'alice',d)
        a2=self.begin('alice',d['iteration_id']);self.candidate(a2,'new-trial')
        self.finish(a2,report=False,selected='chosen')
        context=reports.report_context(self.run,self.cfg,self.state,a2['session_id'])
        self.assertEqual(context['source']['candidate_id'],'chosen')
        self.assertEqual(context['facts']['candidate']['source_session_id'],a['session_id'])
        self.assertEqual(context['facts']['session_id'],a2['session_id'])
        ref=reports.report_submit(self.run,self.cfg,self.state,a2['session_id'],self.prose('chosen'))
        self.assertNotIn('new-trial',str(c.read_json(self.run/ref['path'])))
        reports.require_reports(self.run,self.cfg,self.state)

    def test_unmeasured_selected_method_and_explicit_no_method(self):
        a=self.begin('alice');self.candidate(a,'unmeasured');self.candidate(a,'measured')
        self.evaluation('measured');self.finish(a,report=False,selected='unmeasured')
        ref=reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('unmeasured'))
        candidate=c.read_json(self.run/ref['path'])['facts']['candidate']
        self.assertEqual(candidate['evaluation_status'],'not_evaluated');self.assertEqual(candidate['evaluations'],[])
        b=self.begin('bob');self.candidate(b,'abandoned');self.finish(b,report=False)
        ctx=reports.report_context(self.run,self.cfg,self.state,b['session_id'])
        self.assertIsNone(ctx['source']);self.assertEqual(ctx['facts']['outcome'],'no_sealed_method')
        with self.assertRaisesRegex(ValueError,'only the final checkpoint'):reports.report_submit(self.run,self.cfg,self.state,b['session_id'],self.prose('abandoned'))
        reports.report_submit(self.run,self.cfg,self.state,b['session_id'],{'candidate':None})

    def test_report_rejects_foreign_candidate_and_checkpoint_tampering(self):
        a=self.begin('alice');self.candidate(a,'alice-method');self.finish(a,report=False,selected='alice-method')
        b=self.begin('bob');self.finish(b,report=False,selected='alice-method')
        with self.assertRaisesRegex(ValueError,'belong to this actor'):reports.report_context(self.run,self.cfg,self.state,b['session_id'])
        reports.report_submit(self.run,self.cfg,self.state,a['session_id'],self.prose('alice-method'))
        ref=self.state['extensions']['research_exchange']['note-'+a['session_id']]
        note=c.read_json(self.run/ref['path']);note['payload']['candidate_id']=None;c.write_json(self.run/ref['path'],note)
        with self.assertRaisesRegex(ValueError,'sealed checkpoint changed'):reports.report_audit(self.run,self.cfg,self.state)
        ref['sha']=c.sha(self.run/ref['path'])
        with self.assertRaisesRegex(ValueError,'evidence or session'):reports.report_audit(self.run,self.cfg,self.state)

    def test_failed_without_checkpoint_is_explicit_no_method(self):
        a=self.begin('alice');self.candidate(a,'unfinished')
        research.end_session(self.run,self.cfg,self.state,'alice','failed')
        ref=reports.report_submit(self.run,self.cfg,self.state,a['session_id'],{'candidate':None})
        facts=c.read_json(self.run/ref['path'])['facts']
        self.assertIsNone(facts['checkpoint']);self.assertIsNone(facts['candidate'])
        self.assertEqual(facts['outcome'],'no_sealed_method')

if __name__=='__main__':unittest.main(verbosity=2)
