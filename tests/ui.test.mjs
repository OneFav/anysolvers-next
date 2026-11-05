import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import { uiHandler } from '../lib/plugins/ui-study.js'
import { prepareLaunch, parseLaunchInput, registerLaunchCommand } from '../lib/core/launch.js'
import { researcherRows, buildChart, stepPath, readProgress } from '../lib/ui-study/client/index.js'

test('chart uses personal generations and ordered individual submissions, retaining worse points',()=>{
 const sessions=[{session_id:'a1',actor:'alice',generation:1,status:'completed'},
  {session_id:'a2',actor:'alice',generation:2,status:'active'},
  {session_id:'b1',actor:'bob',generation:1,status:'active'}]
 const entry=(id,sid,score,time,extra={})=>({evaluation_id:id,candidate_id:id,dataset_id:'dev',
  actor:sid?.[0]==='a'?'alice':sid?'bob':null,session_id:sid,generation:sid?Number(sid[1]):null,
  submitted_at:`2026-10-30T00:0${time}:00Z`,status:'completed',eligible:true,score,probe:false,...extra})
 const history={direction:'maximize',metric:'value',datasets:[{id:'dev',purpose:'anchor'}],evaluations:[
  entry('z-early','a1',10,1),entry('a-later','a1',8,2),entry('next','a2',15,5),entry('bob','b1',11,3),
  entry('baseline',null,9,0),entry('queued','a2',null,6,{eligible:false,status:'queued'}),
  entry('other','a2',1000,4,{dataset_id:'other'}),
 ]}
 const before=structuredClone(history)
 const chart=buildChart(history,sessions,'dev'),[alice,bob]=chart.series
 assert.deepEqual(alice.points.map(p=>p.evaluation_id),['z-early','a-later','next','queued'])
 assert.deepEqual(alice.points.map(p=>p.x),[1/3,2/3,1+1/3,1+2/3])
 assert.deepEqual(alice.frontier.map(p=>p.score),[10,10,15,15])
 assert.equal(alice.best,15);assert.equal(bob.best,11)
 assert.deepEqual(alice.points.map(p=>p.historical),[true,true,false,false])
 assert.equal(bob.points[0].historical,false,'Bob is still in his first generation')
 assert.equal(chart.baseline.score,9);assert.equal(chart.rounds,2);assert.equal(chart.unscored,1)
 assert.ok(chart.yMax<1000,'other datasets never share the curve scale')
 assert.deepEqual(history,before)
 assert.equal(stepPath([{x:1,score:10},{x:2,score:15}],x=>x,y=>y),'M 1 10 H 2 V 15')
 const minimize=buildChart({...history,direction:'minimize'},sessions,'dev')
 assert.equal(minimize.series[0].best,8)
 assert.deepEqual(minimize.series[0].frontier.map(p=>p.score),[10,8,8,8])
})

test('chart does not invent researcher scores from a baseline, probes, or empty history',()=>{
 const history={direction:'maximize',metric:'value',datasets:[],evaluations:[]}
 const empty=buildChart(history,[],'dev')
 assert.equal(empty.baseline,null);assert.deepEqual(empty.series,[])
 assert.ok(Number.isFinite(empty.yMin)&&empty.yMax>empty.yMin)
 const sessions=[{actor:'a',session_id:'a1',generation:1,status:'active'}]
 const chart=buildChart({...history,evaluations:[{candidate_id:'baseline',evaluation_id:'base',dataset_id:'dev',eligible:true,score:100},
  {candidate_id:'probe',evaluation_id:'probe',dataset_id:'dev',actor:'a',session_id:'a1',generation:1,score:null,eligible:false,probe:true}]},sessions,'dev')
 assert.equal(chart.series[0].best,null)
 assert.deepEqual(chart.series[0].frontier,[])
 assert.equal(chart.unscored,1)
})

test('researcher overview preserves independent generations and unfinished work after freezing',()=>{
 const progress={phase:'orchestrate',values:{rounds_per_researcher:2},sessions:[
  {actor:'bob',session_id:'bob-2',generation:2,status:'active'},
  {actor:'alice',session_id:'alice-1',generation:1,status:'active'},
  {actor:'bob',session_id:'bob-1',generation:1,status:'completed'},
 ]}
 const original=structuredClone(progress)
 const [alice,bob]=researcherRows(progress)
 assert.equal(alice.latest.generation,1)
 assert.equal(bob.latest.generation,2)
 assert.equal(alice.live,true);assert.equal(bob.live,true)
 assert.equal(bob.completed,1);assert.equal(alice.completed,0)
 assert.deepEqual(progress,original,'display must not reorder or change trusted records')
 progress.sessions[1].status='completed'
 assert.equal(researcherRows(progress)[0].status,'待续接')
 progress.phase='finalize'
 assert.equal(researcherRows(progress)[0].status,'已结束')
 assert.equal(researcherRows(progress)[0].completed,1,'freezing does not imply all planned sessions completed')
 progress.sessions[0].status='failed'
 assert.equal(researcherRows(progress)[1].issue,true)
})

test('UI RPC requires owner and exposes only typed research operations',async()=>{
 const calls=[]; const handler=uiHandler({studies:{root:'/research',call:async(...args)=>{calls.push(args);return {studies:[]}}}})
 const signal=new AbortController().signal
 assert.equal((await handler('anysolvers/catalog',{},signal,{principal:{kind:'researcher'}})).ok,false)
 assert.equal((await handler('anysolvers/save',{studyId:'demo',runId:'demo/001'},signal,{principal:{kind:'owner'}})).ok,false)
 assert.equal((await handler('anysolvers/configuration',{studyId:'../x'},signal,{principal:{kind:'owner'}})).ok,false)
 assert.equal((await handler('anysolvers/measure',{},signal,{principal:{kind:'owner'}})).ok,false)
 assert.equal(calls.length,0)
 assert.equal((await handler('anysolvers/catalog',{},signal,{principal:{kind:'owner'}})).value.root,'/research')
 await handler('anysolvers/progress',{runId:'demo/001',offset:50,final_offset:100},signal,{principal:{kind:'owner'}})
 assert.deepEqual(calls[1].slice(0,3),['ui-progress',{offset:50,final_offset:100},{runId:'demo/001'}])
})

test('progress reads survive an older loaded host while preserving both page cursors',async()=>{
 const signal=new AbortController().signal, calls=[]
 const apiFor=handler=>async(method,payload,receivedSignal)=>{
  assert.equal(receivedSignal,signal)
  calls.push(payload)
  const result=await handler('anysolvers/'+method,payload,receivedSignal,{principal:{kind:'owner'}})
  if(!result.ok)throw new Error(result.error.message)
  return result.value
 }
 const host=uiHandler({studies:{async call(_op,payload){return {evaluations:[payload.offset??0],final_evaluations:[payload.final_offset??0]}}}})
 // Reproduce the pre-final-pagination host retained by an already running Desktop process.
 const oldApi=apiFor(async(...args)=>'final_offset' in args[1]
  ?{ok:false,error:{message:'Unknown research UI field'}}:host(...args))
 await assert.rejects(oldApi('progress',{runId:'demo/001',offset:0,final_offset:0},signal),/Unknown research UI field/)
 assert.deepEqual((await readProgress(oldApi,'demo/001',0,0,signal)).evaluations,[0])
 assert.deepEqual((await readProgress(oldApi,'demo/001',50,0,signal)).evaluations,[50])
 const count=calls.length
 await assert.rejects(readProgress(oldApi,'demo/001',50,50,signal),/重启 DSH App/)
 assert.equal(calls.length,count+1,'never silently retry a final page as page one')
 const page=await readProgress(apiFor(host),'demo/001',50,100,signal)
 assert.deepEqual(page,{evaluations:[50],final_evaluations:[100]})
 const failure=new Error('Only the host owner can use the research panel')
 await assert.rejects(readProgress(async()=>{throw failure},'demo/001',0,50,signal),error=>error===failure)
})

test('UI new-batch command uses the existing procedure after durable claim',async()=>{
 const profile={schema_version:1,study_id:'demo',config:'configs/ui/demo.yaml',existing_runs:[],new_batch:true,
  budgets:{researchers:2,rounds_per_researcher:2,evaluation_calls:null},datasets:[{dataset_id:'dev',purpose:'anchor'}],assignments:[]}
 let command; const calls=[],messages=[]
 const ctx={commands:{register(v){command=v}},studyPolicy:{authorize:async()=>{}},studies:{async call(op,payload,scope){calls.push({op,payload,scope});return op==='ui-launch-profile'?profile:{}}}}
 registerLaunchCommand(ctx)
 const request=parseLaunchInput('new demo --config configs/ui/demo.yaml')
 assert.equal((await prepareLaunch(ctx,request,new AbortController().signal)).action,'start')
 for(const raw of ['new','new demo','new demo/001 --config x'])assert.throws(()=>parseLaunchInput(raw))
 const agent={id:'main-123',session:{firstLiveSeq:0},status:'idle',inbox:{nextTurn:[],nextStep:[]},followup(m){messages.push(m)}}
 const response=await command.handler({agent,rawInput:'new demo --config configs/ui/demo.yaml',signal:new AbortController().signal})
 assert.equal(response.kind,'success');assert.equal(messages.length,1)
 assert.equal(calls.at(-1).op,'ui-claim');assert.equal(calls.at(-1).payload.session,'main-123')
 assert.match(JSON.stringify(messages),/batch_init/)
 await command.handler({agent,rawInput:'new demo --config configs/ui/demo.yaml',signal:new AbortController().signal})
 assert.equal(messages.length,1)
})

test('browser artifact is a lazy native DSH module and contributes additive slots',async()=>{
 let row;const slots=[]
 vm.runInNewContext(await readFile(new URL('../lib/client.js',import.meta.url),'utf8'),{window:{__ModuleLoader__:{load(r){row=r}}}})
 assert.equal(row.id,'@onefav/anysolvers')
 const client=row.factory(name=>{assert.equal(name,'react');return {}})
 client.apply({slots:{inject(_name,fn){fn()},register(o){slots.push(o);return()=>{}}}})
 assert.deepEqual(slots.map(s=>s.name),['main','sidebar.panellist'])
 assert.equal(slots[0].key,'anysolvers')
 assert.equal(slots[1].id,'anysolvers')
})

test('Host routes use the native authenticated exact-Fetch registry and reject mismatched envelopes',async()=>{
 const { Context }=await import('@deepseek-ai/cordis')
 const ui=await import('../lib/plugins/ui-study.js')
 const ctx=new Context(), routes=new Map()
 ctx.provide('studies',{root:'/test',async call(){return {studies:[]}}})
 ctx.provide('connection',{browserPeer:{principal:{kind:'owner'}},fetch:{register(route){routes.set(route.path,route);return async()=>routes.delete(route.path)}}})
 const fiber=ctx.plugin(ui);await fiber
 assert.equal(routes.size,6)
 const route=routes.get('/api/anysolvers/catalog')
 assert.equal(route.requestBody,'buffered')
 const request=body=>new Request('http://localhost/api/anysolvers/catalog',{method:'POST',body:JSON.stringify(body)})
 assert.equal((await route.fetch(request({type:'client-request',rpcId:'1',method:'anysolvers/save',payload:{}}))).status,400)
 const reply=await (await route.fetch(request({type:'client-request',rpcId:'2',method:'anysolvers/catalog',payload:{}}))).json()
 assert.equal(reply.result.ok,true);assert.equal(reply.result.value.root,'/test');assert.equal(reply.rpcId,'2')
 await fiber.dispose();assert.equal(routes.size,0)
})

test('artifact UI is owner-only and addresses registered IDs rather than filesystem paths',async()=>{
 const calls=[],handler=uiHandler({studies:{call:async(...a)=>{calls.push(a);return {content:'report'}}}})
 const signal=new AbortController().signal,args={runId:'demo/001',kind:'report',id:'report',asset:'report'}
 assert.equal((await handler('anysolvers/artifact',args,signal,{principal:{kind:'researcher'}})).ok,false)
 assert.equal((await handler('anysolvers/artifact',{...args,path:'state.json'},signal,{principal:{kind:'owner'}})).ok,false)
 assert.equal(calls.length,0)
 assert.equal((await handler('anysolvers/artifact',args,signal,{principal:{kind:'owner'}})).ok,true)
 assert.deepEqual(calls[0].slice(0,3),['terminal-artifact',{kind:'report',id:'report',asset:'report'},{runId:'demo/001'}])
})
