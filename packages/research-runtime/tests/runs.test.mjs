import test from 'node:test'
import assert from 'node:assert/strict'
import {Context} from '@deepseek-ai/cordis'
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath,symlink} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {ResearchRuntime,sha,atomic} from '../lib/index.js'
async function fixture(t){const root=await realpath(await mkdtemp(join(tmpdir(),'as-runs-')));t.after(()=>rm(root,{recursive:true,force:true}));const ctx=new Context(),fiber=ctx.plugin(ResearchRuntime,{stateRoot:join(root,'cache')});await fiber;t.after(()=>fiber.dispose());return {root,runtime:ctx.researchRuntime}}
async function method(f,id='fixture'){
 const packageRoot=join(f.root,'package-'+id);await mkdir(join(packageRoot,'lib'),{recursive:true});await writeFile(join(packageRoot,'lib/worker.js'),'// fixture version one\n')
 const value={name:'@fixture/'+id,version:'1',files:{'lib/worker.js':sha(await readFile(join(packageRoot,'lib/worker.js')))}};await atomic(join(packageRoot,'lib/implementation.json'),{schemaVersion:1,...value,digest:sha(JSON.stringify(value))})
 const method={id,version:'1',apiVersion:1,mainPreset:id+'-main',packageRoot,ui:{module:id,panel:id,route:id},preflight:async()=>({ready:true,target:'same',revision:'immutable',message:'ready',payload:{}}),start:async({allocate})=>({runId:await allocate('same'),message:'mock run',methodRef:'method.json'}),status:async()=>({complete:true,progress:{methodOwned:true}})}
 f.runtime.register(method);return method
}
function input(root,preset,id='main'){return {root,agent:{id,session:{header:{cwd:root,agentPreset:preset}}},signal:new AbortController().signal}}
test('same workspace sequential methods and same run ID across workspaces remain independent',async t=>{
 const f=await fixture(t);await method(f,'one');await method(f,'two');const a=join(f.root,'a'),b=join(f.root,'b');await mkdir(a);await mkdir(b)
 const runs=[];for(const [root,preset,id] of [[a,'one-main','m1'],[a,'two-main','m2'],[b,'two-main','m3']]){const i=input(root,preset,id);runs.push(await f.runtime.start(i,await f.runtime.preflight(i)))}
 assert.deepEqual(runs.map(r=>r.runId),['same/001','same/002','same/001']);assert.notEqual(runs[0].projectId,runs[2].projectId)
 await assert.rejects(f.runtime.verify(a,'same/001','two'),/belongs/);assert.equal((await f.runtime.status(a,'same/001')).complete,true)
 await assert.rejects(f.runtime.preflight(input(a,'two-main','m1')),/Bound/)
 const lock=await readFile(join(a,'runs/same/001/.runtime/implementation.lock.json'),'utf8');assert.equal(JSON.parse(lock).method.name,'@fixture/one')
})
test('unchanged package version cannot conceal changed executable bytes or record identity',async t=>{
 const f=await fixture(t),m=await method(f),root=join(f.root,'project');await mkdir(root);const i=input(root,m.mainPreset);await f.runtime.start(i,await f.runtime.preflight(i))
 await writeFile(join(m.packageRoot,'lib/worker.js'),'// changed');await assert.rejects(f.runtime.verify(root,'same/001'),/changed/)
 const path=join(root,'runs/same/001/.runtime/run.json'),record=JSON.parse(await readFile(path));await atomic(path,{...record,root:f.root});await assert.rejects(f.runtime.verify(root,'same/001'),/identity/)
})
test('interrupted start blocks new IDs and stale preflight never allocates',async t=>{
 const f=await fixture(t),m=await method(f),root=join(f.root,'project');await mkdir(root);const i=input(root,m.mainPreset)
 await assert.rejects(f.runtime.start(i,{...await f.runtime.preflight(i),revision:'old'}),/changed/)
 m.start=async({allocate})=>{await allocate('same');throw new Error('interrupted initialization')}
 await assert.rejects(f.runtime.start(i,await f.runtime.preflight(i)),/interrupted/)
 await assert.rejects(f.runtime.start(input(root,m.mainPreset,'another'),await f.runtime.preflight(i)),/Unresolved launch/)
})
test('redirected run directories are rejected before metadata or external files are written',async t=>{
 const f=await fixture(t),m=await method(f),root=join(f.root,'project'),outside=join(f.root,'outside');await mkdir(root);await mkdir(outside);await symlink(outside,join(root,'runs'))
 const i=input(root,m.mainPreset);await assert.rejects(f.runtime.start(i,await f.runtime.preflight(i)),/redirected/)
})
test('known environment failure leaves no launch claim and permits retry after repair',async t=>{
 const f=await fixture(t),m=await method(f),root=join(f.root,'project');await mkdir(root);const i=input(root,m.mainPreset);let ready=false;m.validateEnvironment=async()=>{if(!ready)throw new Error('Docker unavailable; no run allocated')}
 await assert.rejects(f.runtime.start(i,await f.runtime.preflight(i)),/Docker/);ready=true
 assert.equal((await f.runtime.start(i,await f.runtime.preflight(i))).runId,'same/001')
})
test('one-sided runtime metadata loss never downgrades execution into legacy mode',async t=>{
 const f=await fixture(t),m=await method(f)
 for(const field of ['run.json','implementation.lock.json']){const root=join(f.root,field);await mkdir(root);const i=input(root,m.mainPreset,field);await f.runtime.start(i,await f.runtime.preflight(i));await rm(join(root,'runs/same/001/.runtime',field));await assert.rejects(f.runtime.verify(root,'same/001'),/registration|lock/)}
})
