"""Actual native subprocess fixtures; no model calls or production research."""
import json,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'packages/research-runtime/python')]
os.environ['ANYSOLVERS_NATIVE_CONFINE']=str(ROOT/'lib/core/native-confine.js')
from modules.evaluation.queued_sandbox import sandbox
from modules.evaluation.queued_sandbox.native import IMAGE,confine

@unittest.skipUnless(sys.platform=='darwin','native candidate adapter currently supports macOS')
class NativeEvaluation(unittest.TestCase):
 def setUp(self): self.tmp=tempfile.TemporaryDirectory(prefix='as-native-tests-');self.root=Path(self.tmp.name).resolve()
 def tearDown(self): self.tmp.cleanup()
 def run_case(self,source,**limits):
  return sandbox.execute(source,{},IMAGE,{**sandbox.DEFAULT_LIMITS,'wall_s':4,**limits},lambda solution,case:{'score':1 if solution==42 else 0},self.root/'slots.lock')
 def test_doctor(self):
  from tools.dsh_bridge import _doctor
  result=_doctor({});self.assertTrue(result['ok'],result);self.assertEqual(result['executor'],'dsh-native')
 def test_success(self):
  r=self.run_case('def solve(case): return 42');self.assertTrue(r['valid'],r);self.assertEqual(r['score'],1);self.assertTrue(r['cleanup_confirmed']);self.assertFalse(r['execution_boundary']['hard_memory_limit'])
 def test_boundary(self):
  sentinel=self.root/'private.txt';sentinel.write_text('not-readable')
  code="""def solve(case):
 import os,socket
 denied=0
 for operation in [lambda:open(%r).read(),lambda:open(%r,'w').write('bad'),lambda:socket.create_connection(('127.0.0.1',9),timeout=.2),lambda:os.fork()]:
  try: operation()
  except PermissionError: denied+=1
 return 42 if denied==4 else denied
"""%(str(sentinel),str(self.root/'outside'))
  r=self.run_case(code);self.assertTrue(r['valid'],r);self.assertEqual(r['score'],1,r);self.assertEqual(sentinel.read_text(),'not-readable');self.assertFalse((self.root/'outside').exists())
 def test_wall_and_output(self):
  r=self.run_case('def solve(case):\n while True: pass',wall_s=1,cpu_s=2);self.assertEqual(r['error'],'wall_timeout',r);self.assertTrue(r['cleanup_confirmed'])
  r=self.run_case('def solve(case):\n print("x"*200000);return 42',output_bytes=1024);self.assertEqual(r['error'],'output_limit',r);self.assertTrue(r['cleanup_confirmed'])
 def test_fail_closed(self):
  old=os.environ['ANYSOLVERS_NATIVE_CONFINE'];os.environ['ANYSOLVERS_NATIVE_CONFINE']='/missing/broker.js'
  try:
   r=self.run_case('def solve(case): return 42');self.assertTrue(r['infrastructure_error'],r);self.assertFalse(r['valid'])
  finally:os.environ['ANYSOLVERS_NATIVE_CONFINE']=old
 def test_desktop_files(self):
  import subprocess
  script="""import {Context} from '@deepseek-ai/cordis';import {NativeWorkspaces} from './lib/core/workspace-native.js';
const root=process.argv[1],provider=new NativeWorkspaces(new Context(),{root,python:process.argv[2],maxFileBytes:1048576});
const workspace=await provider.prepare('native-probe/001','r1-r001');const binding={runId:'native-probe/001',sessionId:'r1-r001',workspace,status:'active'};
await provider.write(binding,'nested/solver.py','# file access verified');
if(await provider.read(binding,'nested/solver.py')!=='# file access verified')throw Error('read mismatch');
console.log(JSON.stringify(await provider.list(binding,'nested')));
"""
  # Under Desktop, the location Docker could not read. Temporary test project only.
  with tempfile.TemporaryDirectory(prefix='native-files-',dir=ROOT/'.state') as directory:
   p=subprocess.run(['node','--input-type=module','-e',script,directory,sys.executable],cwd=ROOT,capture_output=True,text=True,timeout=40)
   self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout),['solver.py'])
if __name__=='__main__':unittest.main(verbosity=2)
