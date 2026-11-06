import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,mkdir,readFile,rm,realpath,symlink,writeFile} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {NativeSessions,atomic,read} from '../lib/index.js'
import {SessionJournal} from '../../../lib/core/session-journal.js'
test('arbitrary roles create fresh native sessions without default research fields',async()=>{
 const events=[],parent={id:'main',session:{header:{}}},options={provider:'test',model:'mock',reasoningEffort:'high'}
 const handle={agent:{id:'native',options,followup:m=>events.push(m),status:'idle',inbox:{nextTurn:[],nextStep:[]}},dispose:async()=>events.push('disposed')}
 const native=new NativeSessions({create:async spec=>{assert.equal(spec.seed,undefined);assert.equal(spec.meta.agentPreset,'reviewer');return handle},validateRoute:async()=>{}})
 await native.validate(options,new AbortController().signal)
 const result=await native.create({nativeId:'native',parent,preset:'reviewer',role:'critic',workspace:await realpath(tmpdir()),options,signal:new AbortController().signal,setup:async()=>{}})
 assert.equal(events.length,0);native.deliver(result,parent,'method-provided material');assert.equal(events[0].content[0].text,'method-provided material');await native.close(result)
})
test('new execution records and method plans have a single authority with correlated IDs',async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'as-split-journal-')));t.after(()=>rm(root,{recursive:true,force:true}));const dir=join(root,'runs/demo/001/.runtime');await mkdir(dir,{recursive:true});await atomic(join(dir,'engine.json'),{});await atomic(join(dir,'implementation.lock.json'),{})
 const journal=new SessionJournal(root);await journal.transaction('demo/001',async(value,save)=>{value.plan={parentNativeId:'main',assignments:[{actor:'r1',idea:'seed'}],status:'running'};value.launches.push({attemptId:'a',runId:'demo/001',actor:'r1',parentNativeId:'main',nativeId:'n',sessionId:'r1-r001',workspace:'/work',generation:1,idea:'seed',iteration:null,nextIteration:null,phase:'active',createdAt:'now',updatedAt:'now',issue:null});await save()})
 const execution=JSON.parse(await readFile(join(dir,'controller.json'))),method=JSON.parse(await readFile(join(dir,execution.methodRef)))
 assert.equal(execution.schemaVersion,2);assert.equal(execution.launches[0].idea,undefined);assert.equal(execution.plan,undefined);assert.equal(method.launches[0].nativeId,undefined)
 assert.equal((await journal.read('demo/001')).launches[0].idea,'seed');method.revision++;await atomic(join(dir,execution.methodRef),method);await assert.rejects(journal.read('demo/001'),/reconciliation/)
})
test('runtime records refuse redirected directories and linked files',async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'as-path-')));t.after(()=>rm(root,{recursive:true,force:true}));await mkdir(join(root,'elsewhere'));await symlink(join(root,'elsewhere'),join(root,'redirect'))
 await assert.rejects(atomic(join(root,'redirect/run.json'),{}),/redirected/);await assert.rejects(read(join(root,'redirect/run.json')),/redirected/)
 await writeFile(join(root,'real.json'),'{}');await symlink(join(root,'real.json'),join(root,'link.json'));await assert.rejects(read(join(root,'link.json')))
})
