import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,mkdir,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {SessionController} from '../lib/core/session-controller.js'

async function fixture(t,closed=false){
 const root=await mkdtemp(join(tmpdir(),'as-next-resume-')),runId='study/001';await mkdir(join(root,'runs',runId),{recursive:true});t.after(()=>rm(root,{recursive:true,force:true}))
 const parent={id:'original-parent'},events=[],active=new Map(),workspace=join(root,'workspace')
 let resolve;const idle=new Promise(r=>resolve=r)
 const backend={actor:'r1',spawn_options:{model:'configured-model',reasoning_effort:'high',fork_turns:'none'},session_id:'r1-r001',native_id:'original-researcher',status:closed?'completed':'active'}
 const agent={id:'original-researcher',ctx:{},session:{header:{cwd:workspace}},inbox:{nextTurn:[],nextStep:[]},status:'running',followup(){events.push('followup')},whenIdle(){return idle}}
 const deps={
  studies:{root,status:async()=>({sessions:[backend],pending_requests:[]}),call:async(op)=>{events.push(op);if(op==='audit')return {pending_requests:[]};if(op==='runtime-wait')return {ok:true};if(op==='config')return {budgets:{researchers:1,rounds_per_researcher:1}};if(op==='research')return {research_time:{pending_request_ids:[],pending_preflight_ids:[]},latest_notebook:{payload:{next_iteration_id:null}}};if(op==='end-session'){backend.status='completed';return {}};throw new Error('Unexpected execution '+op)}},
  sandbox:{prepare:async()=>workspace,release:async()=>{}},create:async()=>{throw new Error('Must not create another native researcher')},get:id=>active.get(id),mountPreset:async()=>{},validateRoute:async()=>{},isCoordinator:p=>p===parent,
  resume:async options=>{assert.equal(options.resumeSessionId,agent.id);assert.equal(options.agentOptions.model,'configured-model');active.set(agent.id,agent);events.push('native.resume');return {agent,dispose:async()=>{active.delete(agent.id);resolve()}}}
 }
 const controller=new SessionController(deps,{provider:'configured',researcherPreset:'researcher',coordinatorPreset:'main'})
 await controller.journal.transaction(runId,async(j,save)=>{j.launches=[{attemptId:'old-launch',runId,actor:'r1',parentNativeId:parent.id,nativeId:agent.id,sessionId:backend.session_id,workspace,generation:1,idea:'seed',iteration:null,nextIteration:null,phase:closed?'completed':'reconciliation',createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',issue:null}];j.plan={parentNativeId:parent.id,assignments:[{actor:'r1',idea:'seed'}],status:'running'};await save()})
 return {controller,parent,runId,events,agent,resolve,deps}
}
test('cold controller reattaches original native/backend identity without begin-session or another generation',async t=>{
 const f=await fixture(t);const restored=await f.controller.recover(f.parent,f.runId)
 assert.equal(restored.needs_reconciliation,false);assert.equal(f.controller.binding(f.agent).generation,1)
 assert.equal(f.events.filter(e=>e==='native.resume').length,1);assert.ok(!f.events.includes('begin-session'))
 f.agent.status='idle';f.resolve();await new Promise(r=>setTimeout(r,40));await f.controller.dispose()
})
test('resumed completed research plan does not allocate or replay a generation',async t=>{
 const f=await fixture(t,true)
 await f.controller.startRun(f.parent,f.runId,[{actor:'r1',idea:'seed'}]);const result=await f.controller.waitRun(f.parent,f.runId)
 assert.equal(result.status,'completed');assert.ok(!f.events.includes('native.resume'));assert.ok(!f.events.includes('begin-session'));await f.controller.dispose()
})

test('crash after Python end-session adopts committed completion instead of creating another generation',async t=>{
 const f=await fixture(t,true)
 await f.controller.journal.transaction(f.runId,async(j,save)=>{j.launches[0].phase='closing';await save()})
 const result=await f.controller.recover(f.parent,f.runId)
 assert.equal(result.needs_reconciliation,false);assert.equal(result.sessions[0].status,'completed');assert.ok(!f.events.includes('native.resume'));await f.controller.dispose()
})


test('recovery retires a pre-registration failure only with no actor or native execution',async t=>{
 const f=await fixture(t)
 await f.controller.journal.transaction(f.runId,async(j,save)=>{Object.assign(j.launches[0],{sessionId:null,workspace:null,generation:null,phase:'reconciliation'});await save()})
 f.deps.studies.status=async()=>({sessions:[],pending_requests:[]})
 const result=await f.controller.recover(f.parent,f.runId)
 assert.equal(result.needs_reconciliation,false)
 assert.equal(result.sessions[0].status,'failed')
 assert.ok(!f.events.includes('native.resume'));assert.ok(!f.events.includes('begin-session'))
 await f.controller.dispose()
})

test('lost registration response is not retired when backend has the actor',async t=>{
 const f=await fixture(t)
 await f.controller.journal.transaction(f.runId,async(j,save)=>{Object.assign(j.launches[0],{sessionId:null,workspace:null,generation:null,phase:'reconciliation'});await save()})
 const result=await f.controller.recover(f.parent,f.runId)
 assert.equal(result.needs_reconciliation,true)
 assert.equal((await f.controller.journal.read(f.runId)).launches[0].phase,'reconciliation')
 assert.ok(!f.events.includes('begin-session'))
 await f.controller.dispose()
})
