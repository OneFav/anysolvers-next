import hashlib,json,tempfile,unittest
from pathlib import Path
from controlplane.runtime_engines import materialize_run_engine
from controlplane.host import Host
class EngineHandoff(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
  self.work=self.root/'projects/project-test';run=self.work/'runs/study/002';(run/'.runtime').mkdir(parents=True)
  files={'dsh/entry.py':hashlib.sha256(b'# old pinned engine').hexdigest()}
  value={'files':files,'version':'old'};self.identity=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  self.source=self.root/'versions/old/lib/runtime/engines';folder=self.source/self.identity;(folder/'dsh').mkdir(parents=True)
  (folder/'dsh/entry.py').write_bytes(b'# old pinned engine');(folder/'manifest.json').write_text(json.dumps(dict(value,engine_id=self.identity)))
  marker=run/'.runtime/engine.json';marker.write_text(json.dumps({'engine_id':self.identity}))
  (run/'state.json').write_text(json.dumps({'configuration':{'_pins':{'runtime-engine':{'path':'runs/study/002/.runtime/engine.json','sha':hashlib.sha256(marker.read_bytes()).hexdigest()}}}}))
  self.dest=self.root/'new-worker/engine-cache/engines'
 def test_new_host_worker_materializes_pinned_old_engine(self):
  host=object.__new__(Host);host.root=self.root/'host';host.work=self.root/'projects';host.config={'_base':str(self.root)};host.options={'versions':{'old':'versions/old'}}
  host.prepare_engines({'stage':'terminal','project_id':'project-test','handoff':{'run_id':'study/002'}},self.root/'new-worker')
  self.assertEqual((self.dest/self.identity/'dsh/entry.py').read_bytes(),b'# old pinned engine')
  materialize_run_engine(self.work,'study/002',self.dest,[self.source])
 def test_missing_engine_never_substitutes_latest(self):
  with self.assertRaisesRegex(ValueError,'Pinned engine.*unavailable'):materialize_run_engine(self.work,'study/002',self.dest,[])
  self.assertFalse(self.dest.exists())
 def test_tampered_source_and_existing_cache_fail_closed(self):
  materialize_run_engine(self.work,'study/002',self.dest,[self.source]);(self.dest/self.identity/'dsh/entry.py').write_text('changed')
  with self.assertRaisesRegex(ValueError,'changed'):materialize_run_engine(self.work,'study/002',self.dest,[self.source])
  self.assertEqual((self.dest/self.identity/'dsh/entry.py').read_text(),'changed')
  (self.source/self.identity/'dsh/entry.py').write_text('changed')
  with self.assertRaisesRegex(ValueError,'changed'):materialize_run_engine(self.work,'study/002',self.root/'another',[self.source])
 def test_pin_and_paths_cannot_escape(self):
  for rid in ['../study/002','study/../../x','/study/002']:
   with self.assertRaises(ValueError):materialize_run_engine(self.work,rid,self.dest,[self.source])
  (self.work/'runs/study/002/.runtime/engine.json').write_text('{}')
  with self.assertRaisesRegex(ValueError,'pin changed'):materialize_run_engine(self.work,'study/002',self.dest,[self.source])
