import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,mkdir,writeFile,rm,realpath} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {Context} from '@deepseek-ai/cordis'
import StudyRuntime from '../lib/plugins/study-runtime.js'
import {ProjectRegistry} from '../lib/core/project-registry.js'
import {apply,formatPreflight,researchPlan} from '../lib/plugins/tool-as-research.js'
import {apply as installMethod} from '../lib/plugins/research-method.js'
import {uiHandler} from '../lib/plugins/ui-study.js'

async function root(t){const value=await realpath(await mkdtemp(join(tmpdir(),'as-projects-')));t.after(()=>rm(value,{recursive:true,force:true}));return value}
async function workspace(path){for(const p of ['tools','modules','configs'])await mkdir(join(path,p),{recursive:true});for(const p of ['common.py','dsh_bridge.py','dsh_launch.py','schemas.json'])await writeFile(join(path,'tools',p),'{}')}

test('project bindings survive reload and never fall back when a workspace disappears',async t=>{
 const dir=await root(t),a=join(dir,'a'),b=join(dir,'b');await workspace(a);await workspace(b)
 const registry=new ProjectRegistry(join(dir,'index')),pa=await registry.register(a),pb=await registry.register(b)
 assert.notEqual(pa.id,pb.id)
 await registry.bind('main-a',{projectId:pa.id,root:a,runId:'demo/001'})
 await registry.bind('main-b',{projectId:pb.id,root:b,runId:'demo/001'})
 const restored=new ProjectRegistry(join(dir,'index'))
 assert.equal((await restored.binding('main-a')).root,a);assert.equal((await restored.binding('main-b')).root,b)
 await rm(a,{recursive:true});await assert.rejects(restored.binding('main-a'))
 assert.equal(await restored.binding('legacy'),undefined)
})

test('runtime async scope and default recent project stay independent for identical run IDs',async t=>{
 const dir=await root(t),a=join(dir,'a'),b=join(dir,'b');await workspace(a);await workspace(b)
 for(const [path,time] of [[a,'2026-10-30T01:00:00Z'],[b,'2026-10-31T01:00:00Z']]){
  await mkdir(join(path,'runs/demo/001'),{recursive:true});await writeFile(join(path,'runs/demo/001/state.json'),JSON.stringify({created_at:time}))
 }
 const ctx=new Context(),fiber=ctx.plugin(StudyRuntime,{root:a,stateRoot:join(dir,'cache'),python:process.execPath,maxInputBytes:2048,maxOutputBytes:4096});await fiber;t.after(()=>fiber.dispose())
 const pa=await ctx.studies.projects.register(a),pb=await ctx.studies.projects.register(b)
 const agent=(id,cwd)=>({id,session:{header:{cwd}}})
 const aa=agent('a',a),bb=agent('b',b),child={id:'child',session:{header:{cwd:'/private-sandbox',origin:'subagent'}}}
 await ctx.studies.bindProject(aa,{projectId:pa.id,root:a,runId:'demo/001'})
 await ctx.studies.bindProject(bb,{projectId:pb.id,root:b,runId:'demo/001'})
 await ctx.studies.projects.bind('child',{projectId:pb.id,root:b,runId:'demo/001'})
 const seen=await Promise.all([aa,bb,child].map((agent,i)=>ctx.studies.withAgent(agent,async()=>{
  await new Promise(resolve=>setTimeout(resolve,15-i*5));return ctx.studies.root
 })))
 assert.deepEqual(seen,[a,b,b]);assert.equal(ctx.studies.root,a)
 const catalog=await ctx.studies.projectCatalog()
 assert.equal(catalog.defaultProjectId,pb.id);assert.equal(catalog.projects[0].latestRun,'demo/001')
 aa.session.header.cwd=b;await assert.rejects(ctx.studies.withAgent(aa,async()=>{}),/工作区/)
})

async function command(t,{ready=true,environment=true}={}){
 const path=await root(t);await workspace(path)
 let handler,followups=[],calls=[],bindings=[]
 const backend={async call(op,payload,scope){calls.push({op,payload,scope});return op==='as-preflight'?{ready,revision:'rev',study_id:'demo',issues:ready?[]:[{path:'datasets/final.jsonl',message:'缺少文件'}]}:{ready:true,run_id:'demo/002',profile:{assignments:[],datasets:[]}}}}
 const ctx={systemPrompt:{section(){}},commands:{register(value){handler=value.handler}},inject(_keys,callback){callback(this)},studyPolicy:{async authorize(){}},studySandbox:{forRoot(){return this},async doctor(){return {ok:environment}}},
  studies:{projects:{async register(){return {id:'p',root:path}}},forRoot(){return backend},async bindProject(agent,binding){bindings.push(binding)}}}
 let method
 ctx.effect=callback=>callback()
 ctx.researchRuntime={register(value){method=value;return ()=>{}},preflight:input=>method.preflight(input),start:async(input,checked)=>{await method.validateEnvironment?.(input);return {...await method.start({...input,checked,requestId:'00000000-0000-0000-0000-000000000000'}),projectId:'p',implementationId:'anysolvers'}}}
 installMethod(ctx)
 apply(ctx)
 const agent={id:'main',status:'idle',inbox:{nextTurn:[],nextStep:[]},session:{header:{cwd:path}},followup(message){followups.push(message)}}
 const invoke=(rawInput='')=>handler({agent,rawInput,signal:new AbortController().signal})
 return {invoke,agent,calls,followups,bindings,path}
}
test('new command validates first, creates exactly one run and dispatches full terminal plan',async t=>{
 const h=await command(t);const reply=await h.invoke()
 assert.equal(reply.kind,'success');assert.deepEqual(h.calls.map(c=>c.op),['as-preflight','as-start'])
 assert.equal(h.bindings[0].runId,'demo/002');assert.equal(h.followups.length,1)
 const plan=researchPlan('demo/002',{})
 for(const word of ['research_start','final_measure','final_deliver','visualization_render','visualization_deliver','final_status.complete'])assert.ok(plan.includes(word))
 assert.match(plan,/不要再次调用/)
})
test('missing initialization, dry check and unavailable Docker never create a run or queue a model',async t=>{
 const bad=await command(t,{ready:false});const reply=await bad.invoke()
 assert.equal(reply.kind,'error');assert.match(reply.text,/datasets\/final.jsonl/)
 const dry=await command(t);assert.equal((await dry.invoke('--check')).kind,'success')
 const env=await command(t,{environment:false});assert.equal((await env.invoke()).kind,'error')
 for(const h of [bad,dry,env]){assert.deepEqual(h.calls.map(c=>c.op),['as-preflight']);assert.equal(h.followups.length,0);assert.equal(h.bindings.length,0)}
})
test('owner UI routes same run ID through selected registry roots and denies arbitrary paths',async()=>{
 const calls=[],projects={async get(id){if(!['a','b'].includes(id))throw new Error('unknown');return {root:'/'+id}}}
 const handler=uiHandler({studies:{projects,forRoot(root){return {call:async(op,payload,scope)=>{calls.push({root,op,scope});return {root}}}}}})
 const signal=new AbortController().signal,owner={principal:{kind:'owner'}}
 for(const projectId of ['a','b'])assert.equal((await handler('anysolvers/progress',{projectId,runId:'demo/001'},signal,owner)).value.root,'/'+projectId)
 assert.deepEqual(calls.map(v=>v.root),['/a','/b'])
 assert.equal((await handler('anysolvers/progress',{projectId:'/evil',runId:'demo/001'},signal,owner)).ok,false)
 assert.equal((await handler('anysolvers/progress',{projectId:'a',runId:'demo/001'},signal,{principal:{kind:'researcher'}})).ok,false)
 assert.equal(calls.length,2)
})
test('new runtime retains legacy project discovery and native bindings without rewriting the old index',async t=>{
 const {ResearchRuntime}=await import('@onefav/research-runtime'),{readFile}=await import('node:fs/promises')
 const dir=await root(t),project=join(dir,'previous-workspace');await workspace(project)
 const legacy=new ProjectRegistry(join(dir,'legacy/projects')),registered=await legacy.register(project);await legacy.bind('old-main',{projectId:registered.id,root:project,runId:'demo/001'})
 const before=await readFile(join(dir,'legacy/projects','project-'+registered.id+'.json'))
 const ctx=new Context(),runtime=ctx.plugin(ResearchRuntime,{stateRoot:join(dir,'new-cache')});await runtime
 const study=ctx.plugin(StudyRuntime,{stateRoot:join(dir,'legacy'),python:process.execPath,maxInputBytes:2048,maxOutputBytes:4096});await study;t.after(async()=>{await study.dispose();await runtime.dispose()})
 assert.equal((await ctx.researchRuntime.projects.get(registered.id)).root,project)
 const agent={id:'old-main',session:{header:{cwd:project,agentPreset:'anysolvers-main'}}}
 assert.equal((await ctx.studies.projectBinding(agent)).runId,'demo/001')
 assert.equal((await ctx.researchRuntime.projects.binding('old-main')).root,project)
 assert.deepEqual(await readFile(join(dir,'legacy/projects','project-'+registered.id+'.json')),before)
})
