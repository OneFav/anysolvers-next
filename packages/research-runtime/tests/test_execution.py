import unittest, tempfile, pathlib, hashlib, json, time, multiprocessing, sys, os
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'python'))
from research_runtime.jobs import JobLedger, ExecutionResult
from research_runtime.docker import evaluation_slot, _create_args

def worker(root,index,queue):
    scope=pathlib.Path(root)/'scope.lock'
    ledger=JobLedger(pathlib.Path(root)/'jobs',scope,{'fixture':lambda req: execute(scope,index,queue)})
    ledger.execute('request-'+str(index),'fixture',{},[],{})

def execute(scope,index,queue):
    with evaluation_slot(scope,2):
        queue.put(('start',index,time.monotonic()))
        time.sleep(.12)
        queue.put(('end',index,time.monotonic()))
    return ExecutionResult({'testOnly':True},True)

class ExecutionTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()
    def ledger(self,callback):return JobLedger(self.root/'jobs',self.root/'scope.lock',{'fixture':callback})
    def test_identity_assets_and_replay(self):
        calls=[];asset=self.root/'input.json';asset.write_text('{}');assets=[dict(path=str(asset),sha256=hashlib.sha256(asset.read_bytes()).hexdigest())]
        ledger=self.ledger(lambda req:(calls.append(req) or ExecutionResult({'testOnly':True},True)))
        self.assertEqual(ledger.execute('one','fixture',{},assets,{}),ledger.execute('one','fixture',{},assets,{}));self.assertEqual(len(calls),1)
        with self.assertRaisesRegex(ValueError,'different'):ledger.execute('one','fixture',{'changed':True},assets,{})
        asset.write_text('[]')
        with self.assertRaisesRegex(ValueError,'changed'):ledger.execute('two','fixture',{},assets,{})
    def test_unknown_cleanup_blocks_new_ids(self):
        ledger=self.ledger(lambda req:ExecutionResult({},False))
        self.assertEqual(ledger.execute('one','fixture',{},[],{}),{})
        with self.assertRaisesRegex(ValueError,'blocks'):ledger.execute('two','fixture',{},[],{})
        with self.assertRaisesRegex(ValueError,'replay'):ledger.execute('one','fixture',{},[],{})
    def test_shared_scope_capacity_across_processes(self):
        ctx=multiprocessing.get_context('spawn');queue=ctx.Queue();processes=[ctx.Process(target=worker,args=(str(self.root),i,queue)) for i in range(4)]
        for p in processes:p.start()
        events=[queue.get(timeout=10) for _ in range(8)]
        for p in processes:p.join(10);self.assertEqual(p.exitcode,0)
        active=maximum=0
        for kind,_,_ in sorted(events,key=lambda v:v[2]):active+=1 if kind=='start' else -1;maximum=max(maximum,active)
        self.assertEqual(maximum,2)
    def test_container_boundary_retains_fixed_limits(self):
        args=_create_args('test',self.root/'program',self.root/'wrapper','sha256:'+'a'*64,dict(memory_mb=128,pids=32,cpu_s=2,tmp_mb=16))
        for item in ['--network','none','--read-only','--cpus','1','--pull=never']:self.assertIn(item,args)
    def test_projects_do_not_share_request_identity(self):
        for name in ['a','b']:
            ledger=JobLedger(self.root/name/'jobs',self.root/name/'scope.lock',{'fixture':lambda r:ExecutionResult({'project':name},True)})
            self.assertEqual(ledger.execute('same','fixture',{},[],{})['project'],name)
    def test_portable_receipt_survives_directory_move_without_execution(self):
        import shutil
        original=self.root/'original';original.mkdir();asset=original/'input.json';asset.write_text('{}')
        assets=[dict(path='input.json',sha256=hashlib.sha256(asset.read_bytes()).hexdigest())]
        calls=[]
        def ledger(root):return JobLedger(root/'jobs',root/'scope.lock',{'fixture':lambda r:(calls.append(r) or ExecutionResult({'done':True},True))},path_base=root)
        first=ledger(original).execute('same','fixture',{},assets,{})
        moved=self.root/'moved with spaces';original.rename(moved)
        self.assertEqual(ledger(moved).execute('same','fixture',{},assets,{}),first)
        self.assertEqual(len(calls),1)
        record=json.loads((moved/'jobs/same.json').read_text())
        self.assertEqual(record['assets'][0]['path'],'input.json');self.assertEqual(record['scope'],'scope.lock')
        self.assertNotIn(str(original),(moved/'jobs/same.json').read_text())
        with self.assertRaisesRegex(ValueError,'project-relative'):
            ledger(moved).execute('escape','fixture',{},[dict(path='../input.json',sha256='0'*64)],{})
    def test_portable_unknown_receipt_remains_blocked_after_move(self):
        original=self.root/'original';original.mkdir()
        def ledger(root):return JobLedger(root/'jobs',root/'scope.lock',{'fixture':lambda r:ExecutionResult({},False)},path_base=root)
        ledger(original).execute('unknown','fixture',{},[],{})
        moved=self.root/'moved';original.rename(moved)
        with self.assertRaisesRegex(ValueError,'do not replay'):ledger(moved).execute('unknown','fixture',{},[],{})
        with self.assertRaisesRegex(ValueError,'blocks'):ledger(moved).execute('new','fixture',{},[],{})
if __name__=='__main__':unittest.main()
