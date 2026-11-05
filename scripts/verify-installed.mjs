/** Run from a clean profile after installing tarballs. No development paths or model calls. */
import assert from 'node:assert/strict'
import {Context} from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import {readFile,mkdir,writeFile,realpath} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {join,dirname} from 'node:path'
import {parse} from 'yaml'
import {artifactIdentity} from '@onefav/research-runtime'
import {createScope} from '@deepseek-ai/dsh-scope'
import {randomUUID} from 'node:crypto'
import vm from 'node:vm'
import {execFileSync} from 'node:child_process'
const profile=await realpath(process.cwd()),python=process.env.ANYSOLVERS_PYTHON
assert.ok(python,'Set the independently installed Python interpreter')
const anyRoot=dirname(fileURLToPath(import.meta.resolve('@onefav/anysolvers/package.json'))),runtimeRoot=dirname(fileURLToPath(import.meta.resolve('@onefav/research-runtime/package.json'))),dynamicRoot=dirname(fileURLToPath(import.meta.resolve('@onefav/dynamic-research-fixture/package.json')))
const pythonRuntimePath=execFileSync(python,['-B','-c','import research_runtime; print(research_runtime.__file__)'],{encoding:'utf8',env:{PATH:process.env.PATH,PYTHONPATH:join(runtimeRoot,'python')}}).trim();assert.ok(pythonRuntimePath.startsWith(runtimeRoot+'/python/'))
for(const root of [anyRoot,runtimeRoot,dynamicRoot]){assert.ok(root.startsWith(profile+'/node_modules/'));await artifactIdentity(root)}
const values={'process.env.ANYSOLVERS_ROOT || undefined':undefined,'process.env.ANYSOLVERS_PYTHON':python,'process.env.ANYSOLVERS_WORKSPACE_IMAGE':'sha256:'+'a'.repeat(64),'process.env.ANYSOLVERS_PROVIDER':'fixture-only'}
const patch=parse(await readFile(join(anyRoot,'cordis.patch.yml'),'utf8'),{customTags:[{tag:'tag:yaml.org,2002:js',resolve:x=>values[x]}]})
const baseUrl=new URL('./',import.meta.url).href,ctx=new Context().extend({baseUrl}),routes=new Map(),handles=[],messages=[]
ctx.provide('approval',{request:async()=>{throw new Error('No final approval during installation test')}})
ctx.provide('llm',{resolveCallConfig:async()=>{throw new Error('No model route during installation test')}})
ctx.provide('connection',{browserPeer:{principal:{kind:'owner'}},fetch:{register(route){assert.equal(routes.has(route.path),false);routes.set(route.path,route);return()=>routes.delete(route.path)}}})
const loader=ctx.plugin(Loader,{baseUrl});await loader
await ctx.loader.root.update([
 {id:'sessions',name:'@deepseek-ai/dsh-session'},{id:'agents',name:'@deepseek-ai/dsh-agent'},{id:'projections',name:'@deepseek-ai/dsh-session-projection'},
 {id:'prompt',name:'@deepseek-ai/dsh-system-prompt'},{id:'tools',name:'@deepseek-ai/dsh-tools',config:{mode:'native'}},{id:'commands',name:'@deepseek-ai/dsh-commands'},
 {id:'presets',name:'@deepseek-ai/dsh-agent-preset-registry',config:{default:'anysolvers-main'}},
 ...patch.flatMap(p=>p.insert).map(entry=>['research-runtime','anysolvers-runtime'].includes(entry.id)?{...entry,config:{...entry.config,stateRoot:join(profile,'cache',entry.id)}}:entry),
 {id:'dynamic',name:'@onefav/dynamic-research-fixture',config:{python}},
 {id:'dynamic-preset',name:'@deepseek-ai/dsh-agent-preset',config:{id:'dynamic-fixture-main',plugins:[{name:'@onefav/research-runtime/command'},{name:'@onefav/dynamic-research-fixture/command'}]}}
]);await ctx.loader.await()
assert.throws(()=>ctx.studies.root,/工作区/);assert.deepEqual(ctx.researchRuntime.implementations.list().map(v=>v.id).sort(),['anysolvers','dynamic-fixture'])
const loop=ctx.plugin({name:'installed-model-free-loop',inject:['agents','sessions','tools','systemPrompt'],apply:owner=>owner.agents.setFactory({async createAgent(host,options){const session=host.sessions.prepare(options.sessionId,{meta:options.meta}),agent={id:session.id,session,options:options.agentOptions??{},status:'idle',inbox:{nextTurn:[],nextStep:[]},whenIdle:async()=>{},followup:m=>messages.push(m)};const scoped=createScope(owner,agent);agent.ctx=scoped.ctx;await options.setup?.(scoped.ctx,agent);const releaseSession=scoped.ctx.sessions.enter(session),releaseAgent=scoped.ctx.agents.enter(agent,options.parentAgent);scoped.ctx.sessions.announce(session);await scoped.ctx.agents.announce(agent,'create',options.signal);const handle={agent,async dispose(){releaseAgent();releaseSession();await scoped.dispose()}};handles.push(handle);return handle},resume(){throw new Error('No replay')}})});await loop
const make=(cwd,preset)=>ctx.agents.create({sessionId:randomUUID(),meta:{cwd,agentPreset:preset},agentOptions:{provider:'test-only',model:'mock'},setup:async child=>{await ctx.agentPresets.mount(child,preset)}})
const command=(handle,line)=>ctx.commands.execute(handle.agent,line,[],new AbortController().signal)
const project=join(profile,'project');await mkdir(join(project,'configs'),{recursive:true});await writeFile(join(project,'configs/dynamic-fixture.json'),JSON.stringify({format:'dynamic-fixture-v1',testOnly:true,values:[1,2],studyId:'contract-study'}))
const anyMain=await make(project,'anysolvers-main'),checked=await command(anyMain,'/as-research --check')
// Optional sealed fixture input was prepared outside this script, never copied from a real run.
const initialized=checked.result.kind==='success'
if(initialized){ctx.studySandbox.forRoot=()=>({doctor:async()=>({ok:true,testOnly:true})});const launched=await command(anyMain,'/as-research');assert.equal(launched.result.kind,'success',JSON.stringify(launched));assert.equal(messages.length,1)}
else {assert.match(checked.result.text,/初始化/);assert.equal(messages.length,0)}
const dynamicMain=await make(project,'dynamic-fixture-main'),result=await command(dynamicMain,'/as-research');assert.equal(result.result.kind,'success',JSON.stringify(result))
const binding=await ctx.researchRuntime.projects.binding(dynamicMain.agent.id);assert.equal(binding.runId,initialized?'contract-study/002':'contract-study/001');assert.equal((await ctx.researchRuntime.status(project,binding.runId)).complete,true)
const project2=join(profile,'project2');await mkdir(join(project2,'configs'),{recursive:true});await writeFile(join(project2,'configs/dynamic-fixture.json'),JSON.stringify({format:'dynamic-fixture-v1',testOnly:true,values:[3,4],studyId:'contract-study'}));const other=await make(project2,'dynamic-fixture-main');assert.equal((await command(other,'/as-research')).result.kind,'success');assert.equal((await ctx.researchRuntime.projects.binding(other.agent.id)).runId,'contract-study/001')
for(const [root,id] of [[runtimeRoot,'@onefav/research-runtime'],[anyRoot,'@onefav/anysolvers'],[dynamicRoot,'@onefav/dynamic-research-fixture']]){let entry;const slots=[];vm.runInNewContext(await readFile(join(root,'lib/client.js'),'utf8'),{window:{__ModuleLoader__:{load:v=>entry=v}}});assert.equal(entry.id,id);entry.factory(()=>({})).apply({slots:{inject:(_name,fn)=>fn(),register:row=>{slots.push(row);return()=>{}}}});assert.ok(slots.length)}
const route=routes.get('/api/research-runtime/navigation');const response=await route.fetch(new Request('http://localhost/api/research-runtime/navigation',{method:'POST',body:JSON.stringify({type:'client-request',rpcId:'1',method:'research-runtime/navigation',payload:{}})}));assert.equal((await response.json()).result.ok,true)
await ctx.loader.remove('dynamic');await ctx.loader.await();assert.equal((await ctx.researchRuntime.runs(binding.projectId)).find(r=>r.implementationId==='dynamic-fixture').available,false);assert.ok(ctx.studies)
for(const handle of handles.reverse())await handle.dispose();await loop.dispose();await loader.dispose();assert.equal(routes.size,0)
console.log(JSON.stringify({ok:true,installedRoots:[anyRoot,runtimeRoot,dynamicRoot],sourceFree:true,pythonRuntimePath,initializedAnySolvers:initialized,modelCalls:0,realCandidateExecutions:0,clients:3},null,2))
