import base64,hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from controlplane.engine import Engine
from controlplane.feishu import Feishu
from controlplane.store import Store
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
class FigureDelivery(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.now=1000;self.store=Store(Path(temp.name)/'router.sqlite3')
  cfg={'default_host':'dev','default_version':'v1','sources':{'feishu':{'chats':['oc_test'],'allowed_users':['ou_owner'],'allowed_stages':['initialize','terminal']}}};self.e=Engine(self.store,cfg,lambda:self.now)
  self.e.register_version({'id':'v1','digest':'a'*64,'state':'ready','validation':{'passed':True}})
  self.e.receive({'id':'human-image-request','source':'feishu','thread':'oc_test:chart','actor':'ou_owner','text':'show this chart','channel':{'chat_id':'oc_test','message_id':'om_source'}})
  result=self.e.decide('human-image-request',{'action':'create','stage':'initialize','title':'fixture','scope':'fixture transport only','reason':'test'})
  with self.store.transaction() as tx:
   job=tx.get('jobs',result['job_id']);job.update(stage='terminal',handoff={'run_id':'study/002'});tx.put('jobs',job['id'],job)
   for item in tx.list('outbox'):item['state']='sent';tx.put('outbox',item['id'],item)
  self.job=job;self.a=self.e.claim('dev',job['id'])['attempt'];self.e.worker_event(self.a['id'],self.a['token'],{'id':'start','type':'started','data':{'pid':123,'native_id':'fixture'}})
  self.body={'run_id':'study/002','figure_id':'frozen-chart','mime':'image/png','encoding':'base64','content':base64.b64encode(PNG).decode(),'sha256':hashlib.sha256(PNG).hexdigest()}
 def queue(self,body=None):return self.e.worker_artifact(self.a['id'],self.a['token'],body or self.body)
 def test_source_bound_idempotent_image_delivery(self):
  one=self.queue();two=self.queue();self.assertEqual(one,two)
  messages=[m for m in self.store.list('outbox') if m.get('media')];self.assertEqual(len(messages),1)
  self.assertEqual(messages[0]['channel'],{'chat_id':'oc_test','message_id':'om_source'})
  calls=[]
  def runner(argv,**kwargs):
   calls.append((argv,kwargs));self.assertIn('--image',argv);self.assertNotIn('--markdown',argv)
   name=argv[argv.index('--image')+1];self.assertEqual((Path(kwargs['cwd'])/name).read_bytes(),PNG)
   return subprocess.CompletedProcess(argv,0,stdout=json.dumps({'code':0,'data':{'message_id':'om_image','msg_type':'image'}}))
  f=Feishu(self.e,{'enabled':True,'chats':[{'chat_id':'oc_test'}]},runner);f.flush();f.flush()
  self.assertEqual(len(calls),1);self.assertEqual(self.store.get('outbox',one['delivery_id'])['state'],'sent')
  self.assertNotIn('content',[m for m in self.e.snapshot()['outbox'] if m.get('media')][0]['media'])
 def test_retry_keeps_delivery_identity(self):
  one=self.queue();ids=[]
  def runner(argv,**kwargs):
   ids.append(argv[argv.index('--idempotency-key')+1])
   if len(ids)==1:raise subprocess.TimeoutExpired(argv,45)
   return subprocess.CompletedProcess(argv,0,stdout='{"code":0,"data":{"message_id":"om_image"}}')
  f=Feishu(self.e,{'enabled':True,'chats':[{'chat_id':'oc_test'}]},runner);f.flush();self.now+=20;f.flush()
  self.assertEqual(ids,[one['delivery_id']]*2);self.assertEqual(self.store.get('outbox',one['delivery_id'])['state'],'sent')
 def test_wrong_scope_tamper_and_researcher_rejected(self):
  for patch in [{'run_id':'other/001'},{'sha256':'0'*64},{'figure_id':'../secret'},{'content':'not-base64'}]:
   with self.assertRaises(ValueError):self.queue(dict(self.body,**patch))
  with self.store.transaction() as tx:
   job=tx.get('jobs',self.job['id']);job['stage']='research';tx.put('jobs',job['id'],job)
  with self.assertRaises(ValueError):self.queue()
