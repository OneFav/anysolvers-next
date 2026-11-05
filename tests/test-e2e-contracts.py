"""Regressions from real Feishu E2E, deterministic only (no model calls)."""
import copy, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'packages/research-runtime/python')]
from modules.evaluation.queued_sandbox.measure import validate_compute
from modules.final.frozen_holdout import policy
from modules.datasets.stratified_holdout import package

class Contracts(unittest.TestCase):
 def test_impossible_total_budget_rejected_without_change(self):
  for budget in [60,72]:
   compute={'limits':{'wall_s':2},'evaluation_wall_s':budget};before=copy.deepcopy(compute)
   with self.assertRaisesRegex(ValueError,'cannot start a case'):validate_compute(compute)
   self.assertEqual(compute,before)
  validate_compute({'limits':{'wall_s':2},'evaluation_wall_s':360})
 def test_seal_resolves_evaluation_outside_initialization_manifest(self):
  from tools import study
  from contextlib import nullcontext
  manifest={'status':'draft','source':{'path':'configs/task.yaml'},'modules':{'task':{},'datasets':{},'ideas':{}},'deliveries':{'dataset':{},'ideas':{}}}
  cfg={'modules':{'evaluation':{'use':'modules/evaluation/queued_sandbox'}},'compute':{'limits':{'wall_s':2},'evaluation_wall_s':360}}
  method={'handlers':{'validate_compute':'measure.py'}}
  with patch.object(study,'_lock',return_value=nullcontext()),patch.object(study,'_load',return_value=(Path('/tmp'),manifest)),patch.object(study,'_ready'),patch.object(study,'prepare',return_value=cfg),patch.object(study,'read_module',return_value=method) as read,patch.object(study,'invoke') as invoke,patch.object(study,'_validate_manifest'),patch.object(study.c,'write_json'):
   self.assertEqual(study.seal('seal-test')['status'],'sealed')
   read.assert_called_once_with('modules/evaluation/queued_sandbox',None)
   self.assertEqual(invoke.call_args.args[1:3],('evaluation','validate_compute'))
   self.assertEqual(invoke.call_args.args[0]['modules']['evaluation'],method)

 def test_selection_preview_and_rejection_follow_same_tie_rule(self):
  rows=[dict(candidate_id='exact-a',source_sha='bbb',evaluation_id='e1',dataset_id='dev',score=1),
        dict(candidate_id='exact-b',source_sha='aaa',evaluation_id='e2',dataset_id='dev',score=1)]
  cfg={'task':{'objective':{'direction':'maximize'}}};state={}
  with patch.object(policy,'_eligible',return_value=rows),patch.object(policy,'safe_boundary'),patch.object(policy,'_summaries',return_value=[]),patch.object(policy,'save_state'),patch.object(policy,'event'):
   best=policy.recommendation(None,cfg,state)
   self.assertEqual(best['candidate_id'],'exact-b');self.assertEqual(state,{})
   with self.assertRaisesRegex(ValueError,"expected 'exact-b'"):policy.select(None,cfg,state,'exact-a')
   cfg['config_sha']='unused';state['config_sha']='fixed'
   self.assertEqual(policy.select(None,cfg,state,best['candidate_id'])['source_sha'],'aaa')
 def test_coordinator_measurement_waits_for_real_os_locks(self):
  import fcntl,threading,time
  from contextlib import ExitStack
  from modules.evaluation.queued_sandbox import measure
  from tools import common
  with tempfile.TemporaryDirectory() as name:
   run=Path(name);cfg={};state={};request={'fixture':'same-input'}
   sha=common.digest({**request,'probe':False});summary={'evaluation_id':'eval-one','request_sha256':sha}
   for relative in ['.mutation.lock','research/.measurement.lock','research/.snapshot-locks/'+sha+'.lock','research/.evaluation-locks/eval-one.lock']:
    file=run/relative;file.parent.mkdir(parents=True,exist_ok=True);ready=threading.Event()
    def hold():
     with file.open('a+') as f:
      fcntl.flock(f,fcntl.LOCK_EX);ready.set();time.sleep(.15);fcntl.flock(f,fcntl.LOCK_UN)
    thread=threading.Thread(target=hold);thread.start();ready.wait();start=time.monotonic()
    with ExitStack() as stack:
     for target,value in [('run_path',run),('load_run',(run,cfg,state))]:stack.enter_context(patch.object(common,target,return_value=value))
     stack.enter_context(patch.object(measure,'_research_admission'))
     stack.enter_context(patch.object(measure,'_inputs',return_value=('',None,{},[],None,request)))
     stack.enter_context(patch.object(measure,'_remaining',return_value=60))
     stack.enter_context(patch.object(measure,'_final_gate'))
     stack.enter_context(patch.object(measure,'_previous',return_value={'eval-one':summary}))
     result=measure._evaluate_domain('test/001','baseline','dev','eval-one')
    thread.join();self.assertTrue(result['cached']);self.assertGreaterEqual(time.monotonic()-start,.12)

 def test_python_fixture_error_identifies_json_contract_and_path(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);(root/'tests.py').write_text('def test_solver(): pass')
   b=dict(benchmark_id='b',task_id='t',protocol={'kind':'function'},evaluator_file='eval.py',baseline_file='base.py',tests_file='tests.py',support_files=[])
   files={k:'sha' for k in ['eval.py','base.py','tests.py']}
   with patch.object(package,'inventory',return_value=[]),patch.object(package,'_benchmark',return_value=b),patch.object(package,'_load',return_value=SimpleNamespace(validate_input=lambda x:None,score=lambda x:None)):
    with self.assertRaisesRegex(ValueError,"tests.py.*JSON fixtures"):
     package.load_environment(root,{'benchmark_file':'benchmark.json'},files)
if __name__=='__main__':unittest.main()
