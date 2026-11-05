import {fileURLToPath} from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import {Context} from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import {ImplementationRegistry,artifactIdentity} from '../lib/index.js'
import {ProjectRegistry} from '../lib/project-registry.js'
import {mkdtemp,mkdir,rm,realpath} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
const method=id=>({id,version:'1',apiVersion:1,mainPreset:id+'-main',packageRoot:'/unused',ui:{module:id,panel:id,route:id},preflight:async()=>{},start:async()=>{},status:async()=>{}})
test('relative project and registry directories use a fixed base independently of cwd',async t=>{
 const base=await realpath(await mkdtemp(join(tmpdir(),'relative registry ')));t.after(()=>rm(base,{recursive:true,force:true}))
 await mkdir(join(base,'project'));const registry=new ProjectRegistry('.state/projects',base)
 const project=await registry.register('project')
 assert.equal(project.root,join(base,'project'));assert.equal(registry.directory,join(base,'.state/projects'))
 assert.deepEqual(await registry.get(project.id),project)
})
test('registration isolates method IDs, presets and routes and unloads only its owner',()=>{
 const r=new ImplementationRegistry(),remove=r.register(method('first'));r.register(method('second'))
 assert.throws(()=>r.register(method('first')),/already/);assert.throws(()=>r.register({...method('third'),mainPreset:'first-main'}),/already/)
 assert.equal(r.forPreset('second-main').id,'second');remove();assert.throws(()=>r.get('first'),/not installed/);assert.equal(r.get('second').id,'second')
})
test('standalone generic package loads in real Loader with no AnySolvers service or tools',async t=>{
 const baseUrl=new URL('../../../',import.meta.url).href,ctx=new Context().extend({baseUrl}),loader=ctx.plugin(Loader,{baseUrl});await loader;t.after(()=>loader.dispose())
 await ctx.loader.root.update([{id:'runtime',name:'@onefav/research-runtime/service',config:{stateRoot:'/private/tmp/research-runtime-registry-test'}}]);await ctx.loader.await()
 assert.ok(ctx.researchRuntime);assert.equal(ctx.studies,undefined);assert.equal(ctx.tools,undefined)
 const dispose=ctx.researchRuntime.register(method('example'));assert.equal(ctx.researchRuntime.implementations.list().length,1);dispose();assert.equal(ctx.researchRuntime.implementations.list().length,0)
 await ctx.loader.remove('runtime');await ctx.loader.await();assert.equal(ctx.researchRuntime,undefined)
})
test('build manifests bind the two physical packages independently',async()=>{
 const runtime=await artifactIdentity(fileURLToPath(new URL('../',import.meta.url))),method=await artifactIdentity(fileURLToPath(new URL('../../../',import.meta.url)))
 assert.equal(runtime.name,'@onefav/research-runtime');assert.equal(method.name,'@onefav/anysolvers');assert.notEqual(runtime.digest,method.digest)
})
