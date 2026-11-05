import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,rm,readFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {ToolJournal,hash} from '../lib/next/durable.js'
import {STAGE_TOOLS} from '../lib/plugins/next-runner.js'

class Remote {
 constructor(){this.rows=new Map();this.attempt='one';this.failFinish=false}
 async worker(path,p){
  if(path==='tool-begin'){
   const old=this.rows.get(p.operation_id);if(old)return old
   const item={state:'started'};this.rows.set(p.operation_id,item);return item
  }
  if(this.failFinish)throw new Error('network unavailable after commit')
  const value={state:'completed',result:p.result};this.rows.set(p.operation_id,value);return value
 }
}
async function temporary(t){const dir=await mkdtemp(join(tmpdir(),'as-next-journal-'));t.after(()=>rm(dir,{recursive:true,force:true}));return dir}
test('lost Router acknowledgement reuses fsynced result; no repeated evaluator',async t=>{
 const path=await temporary(t),remote=new Remote(),journal=new ToolJournal(path,remote);let executed=0
 remote.failFinish=true
 await assert.rejects(journal.execute('native:call','solver_evaluate',{id:'original'},async()=>{executed++;return {score:5}}),/network/)
 remote.failFinish=false
 const restored=new ToolJournal(path,remote)
 assert.deepEqual(await restored.execute('native:call','solver_evaluate',{id:'original'},async()=>{executed++;return {score:99}}),{score:5})
 assert.equal(executed,1)
})
test('crash after execution intent never retries an unknown side effect',async t=>{
 const journal=new ToolJournal(await temporary(t),new Remote());let executed=0
 await assert.rejects(journal.execute('call','solver_evaluate',{},async()=>{executed++;throw new Error('killed')}),/killed/)
 await assert.rejects(journal.execute('call','solver_evaluate',{},async()=>{executed++;return {}}),/Uncertain/)
 assert.equal(executed,1)
})
test('same call identity cannot change its arguments',async t=>{
 const journal=new ToolJournal(await temporary(t),new Remote())
 await journal.execute('call','write',{path:'a'},async()=>({ok:true}))
 await assert.rejects(journal.execute('call','write',{path:'b'},async()=>({})),/identity collision/)
})
test('stage tools preserve the three plus three plus two module boundaries',()=>{
 assert.ok(STAGE_TOOLS.initialize.includes('study_seal'))
 assert.ok(!STAGE_TOOLS.initialize.includes('research_start'))
 assert.ok(STAGE_TOOLS.research.includes('research_start'))
 assert.ok(!STAGE_TOOLS.research.includes('final_measure'))
 assert.ok(STAGE_TOOLS.terminal.includes('final_measure'))
 assert.ok(!STAGE_TOOLS.terminal.includes('research_start'))
 assert.equal(STAGE_TOOLS.framework,undefined)
 assert.deepEqual(STAGE_TOOLS.router,['router_decide'])
 for(const tools of Object.values(STAGE_TOOLS))assert.ok(!tools.some(t=>t.startsWith('version_')||t==='next_framework_request'||t==='router_framework_dispatch'))
})
