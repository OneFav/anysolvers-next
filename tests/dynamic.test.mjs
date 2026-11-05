import {fileURLToPath} from 'node:url'
import {tmpdir} from 'node:os'
import test from 'node:test'
import assert from 'node:assert/strict'
import {Context} from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath} from 'node:fs/promises'
import {join} from 'node:path'
import {randomUUID} from 'node:crypto'
import {loopAdapter} from './support/native-fixture.mjs'
import {artifactIdentity} from '@onefav/research-runtime'
import {execFileSync} from 'node:child_process'
const python=execFileSync('python3',['-c','import sys; print(sys.executable)'],{encoding:'utf8'}).trim()
test('independent dynamic plugin uses real Loader presets and shared runtime with no AnySolvers dependency',async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'as-dynamic-'))),project=join(root,'project');await mkdir(join(project,'configs'),{recursive:true});await writeFile(join(project,'configs/dynamic-fixture.json'),JSON.stringify({format:'dynamic-fixture-v1',testOnly:true,studyId:'same',values:[1,2]}))
 const before=await artifactIdentity(fileURLToPath(new URL('../packages/research-runtime/',import.meta.url)))
 const baseUrl=new URL('../',import.meta.url).href,ctx=new Context().extend({baseUrl}),loader=ctx.plugin(Loader,{baseUrl});await loader
 const adapter=loopAdapter();let loopFiber
 t.after(async()=>{for(const handle of adapter.handles)await handle.dispose();await loopFiber?.dispose();await loader.dispose();await rm(root,{recursive:true,force:true})})
 await ctx.loader.root.update([
 {id:'sessions',name:'@deepseek-ai/dsh-session'},{id:'agents',name:'@deepseek-ai/dsh-agent'},{id:'projections',name:'@deepseek-ai/dsh-session-projection'},
 {id:'prompt',name:'@deepseek-ai/dsh-system-prompt'},{id:'tools',name:'@deepseek-ai/dsh-tools',config:{mode:'native'}},{id:'commands',name:'@deepseek-ai/dsh-commands'},
 {id:'presets',name:'@deepseek-ai/dsh-agent-preset-registry',config:{default:'dynamic-fixture-main'}},
 {id:'runtime',name:'@onefav/research-runtime/service',config:{stateRoot:join(root,'cache')}},
 {id:'dynamic',name:'@onefav/dynamic-research-fixture',config:{python}},
 {id:'dynamic-preset',name:'@deepseek-ai/dsh-agent-preset',config:{id:'dynamic-fixture-main',plugins:[{name:'@onefav/research-runtime/command'},{name:'@onefav/dynamic-research-fixture/command'}]}}
 ]);await ctx.loader.await()
 loopFiber=ctx.plugin({name:'fixture-loop',inject:['agents','sessions','tools','systemPrompt'],apply:child=>adapter.attach(child)});await loopFiber
 const handle=await ctx.agents.create({sessionId:randomUUID(),meta:{cwd:project,agentPreset:'dynamic-fixture-main'},agentOptions:{provider:'test-only',model:'mock'},setup:async child=>{await ctx.agentPresets.mount(child,'dynamic-fixture-main')}})
 assert.equal(ctx.studies,undefined);assert.equal(ctx.tools.schemas(handle.agent).some(t=>t.name.startsWith('solver_')),false)
 const command=line=>ctx.commands.execute(handle.agent,line,[],new AbortController().signal)
 const checked=await command('/as-research --check');assert.equal(checked.result.kind,'success');await assert.rejects(readFile(join(project,'runs/same/001/.runtime/run.json')),{code:'ENOENT'})
 const result=await command('/as-research');assert.equal(result.result.kind,'success',JSON.stringify(result));assert.equal(adapter.queued.length,0)
 const delivery=JSON.parse(await readFile(join(project,'runs/same/001/dynamic/delivery.json')))
 assert.equal(delivery.testOnly,true);assert.equal(delivery.snapshotRefs.length,2);assert.equal(delivery.measurements.length,4);assert.equal(delivery.selectedConfiguration,'one');assert.equal(delivery.comparisonDataset,'v2')
 assert.notEqual(delivery.snapshotRefs[0].sha256,delivery.snapshotRefs[1].sha256)
 for(const measurement of delivery.measurements)assert.equal(measurement.testOnly,true)
 assert.equal((await ctx.researchRuntime.status(project,'same/001')).complete,true)
 const deliveryPath=join(project,'runs/same/001/dynamic/delivery.json')
 const bad={...delivery};delete bad.updateRationale;await writeFile(deliveryPath,JSON.stringify(bad));await assert.rejects(ctx.researchRuntime.status(project,'same/001'),/missing updateRationale/)
 await writeFile(deliveryPath,JSON.stringify({...delivery,selectedConfiguration:3}));await assert.rejects(ctx.researchRuntime.status(project,'same/001'),/expected string/)
 await writeFile(deliveryPath,JSON.stringify(delivery))
 assert.equal((await command('/as-research')).result.kind,'error')
 const binding=await ctx.researchRuntime.projects.binding(handle.agent.id);assert.equal(binding.implementationId,'dynamic-fixture')
 await ctx.loader.remove('dynamic');await ctx.loader.await();const runs=await ctx.researchRuntime.runs(binding.projectId);assert.equal(runs[0].available,false);assert.equal(runs[0].implementationId,'dynamic-fixture')
 await assert.rejects(ctx.researchRuntime.verify(project,'same/001'),/not installed/)
 assert.deepEqual(await artifactIdentity(fileURLToPath(new URL('../packages/research-runtime/',import.meta.url))),before)
})
test('dynamic native client updates retained panels across projects and ignores late prior responses',async()=>{
 const vm=await import('node:vm');let entry,Panel,hookIndex=0,effectIndex=0,pendingRender=false,rendering=false
 const states=[],effects=[],listeners=new Map(),calls=[]
 const selection={implementationId:'dynamic-fixture',projectId:'project-a',runId:'same/001'}
 const window={__ModuleLoader__:{load:value=>entry=value},addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)}
 const render=()=>{if(rendering){pendingRender=true;return}rendering=true;hookIndex=effectIndex=0;Panel();rendering=false;if(pendingRender){pendingRender=false;render()}}
 const React={createElement:()=>null,useState(initial){const index=hookIndex++;if(!(index in states))states[index]=typeof initial==='function'?initial():initial;return [states[index],value=>{if(states[index]===value)return;states[index]=value;pendingRender=true;if(!rendering){pendingRender=false;render()}}]},useEffect(callback,deps){const index=effectIndex++,old=effects[index];if(old&&JSON.stringify(old.deps)===JSON.stringify(deps))return;old?.cleanup?.();effects[index]={deps};effects[index].cleanup=callback()}}
 vm.runInNewContext(await readFile(new URL('../examples/dynamic-research/lib/client.js',import.meta.url),'utf8'),{window,sessionStorage:{getItem:()=>JSON.stringify(selection)},AbortController})
 entry.factory(()=>React).apply({slots:{inject:(_name,fn)=>fn(),register:(_row,component)=>{Panel=component;return()=>{}}},connection:{rpc:{call:(_channel,_method,payload,signal)=>new Promise(resolve=>calls.push({payload,signal,resolve}))}}})
 render();assert.equal(calls.length,1);listeners.get('research-runtime-selection')({detail:{implementationId:'dynamic-fixture',projectId:'project-b',runId:'same/001'}})
 assert.equal(calls.length,2);assert.equal(calls[0].signal.aborted,true);assert.equal(calls[1].payload.projectId,'project-b')
 calls[1].resolve({ok:true,value:{progress:{marker:'new'}}});await Promise.resolve();calls[0].resolve({ok:true,value:{progress:{marker:'old'}}});await Promise.resolve();assert.equal(states[0].marker,'new')
 for(const effect of effects)effect?.cleanup?.();assert.equal(listeners.size,0)
})
