import test from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import { MAIN_TOOLS, SOLVER_TOOLS, FINAL_TOOLS, VISUALIZATION_TOOLS } from '../lib/core/policy-rules.js'
async function setup(t, role, launchProfile) {
  const ctx = new Context(), calls = []
  const binding = { runId: 'demo/001', sessionId: 'r1-r001', nativeId: 'native', status: 'active' }
  ctx.provide('studies', { call: async (operation, payload, scope) => {
    calls.push({ operation, payload, scope })
    return operation === 'launch-profile' ? launchProfile : operation === 'candidate-import' ? { manifest: 'research/candidates/c1/candidate.json' } : { ok: true }
  } })
  ctx.provide('studySessions', { binding: () => binding, startRun: async () => ({ status: 'running' }), waitRun: async () => { calls.push({ operation: 'wait-run' }); return { status: 'completed' } } })
  ctx.provide('studySandbox', { read: async () => 'source', write: async () => {}, list: async () => ['solver.py'] })
  ctx.provide('studyPolicy', { authorize: async () => {}, authorizeFinal: async () => {} })
  const loader = ctx.plugin(Loader, { baseUrl: new URL('../', import.meta.url).href })
  await loader
  t.after(() => loader.dispose())
  await ctx.loader.root.update([
    { id: 'prompt', name: '@deepseek-ai/dsh-system-prompt' },
    { id: 'tools', name: '@deepseek-ai/dsh-tools', config: { mode: 'native' } },
    { id: 'surface', name: `./lib/plugins/tool-${role}.js` },
  ])
  await ctx.loader.await()
  const agent = { id: 'native', ctx }
  const invoke = (name, args = {}, withAgent = true) => ctx.tools.execute({ callId: randomUUID(), name,
    arguments: args, ...(withAgent ? { agent } : {}), signal: new AbortController().signal })
  return { ctx, calls, invoke }
}
test('Loader exposes coordinator tools and unregisters on removal', async t => {
  const h = await setup(t, 'study')
  assert.deepEqual(h.ctx.tools.schemas().map(v => v.name).sort(), MAIN_TOOLS.filter(n=>!FINAL_TOOLS.includes(n)&&!VISUALIZATION_TOOLS.includes(n)).sort())
  const result = await h.invoke('baseline_measure', { run_id: 'demo/001', dataset: 'anchor', evaluation: 'e1', candidate: 'evil', scope: 'final' })
  assert.equal(result.isError, false, JSON.stringify(result))
  assert.deepEqual(h.calls[0], { operation: 'measure', scope: { runId: 'demo/001' }, payload: { candidate: 'baseline', scope: 'development', dataset: 'anchor', evaluation: 'e1' } })
  await h.ctx.loader.remove('surface')
  await h.ctx.loader.await()
  assert.equal(h.ctx.tools.schemas().length, 0)
})
test('plain-language launcher works without commands and returns a fixed plan without executing research', async t => {
  const profile = { schema_version: 1, study_id: 'demo', config: 'configs/demo.yaml',
    budgets: { researchers: 2, rounds_per_researcher: 2 }, existing_runs: [],
    assignments: [{ actor: 'r1', idea: 'seed' }, { actor: 'r2', idea: 'seed' }],
    datasets: [{ dataset_id: 'anchor', purpose: 'anchor' }] }
  const h = await setup(t, 'study', profile)
  const result = await h.invoke('study_launch', { target: 'demo' })
  assert.equal(result.isError, false, JSON.stringify(result))
  assert.equal(result.value.action, 'start')
  assert.match(result.value.instructions, /research_start/)
  assert.match(result.value.instructions, /visualization_render/)
  assert.deepEqual(h.calls, [{ operation: 'launch-profile', payload: {}, scope: { studyId: 'demo' } }])
  assert.equal((await h.invoke('study_launch', { action: 'final', target: 'demo' })).isError, true)
  profile.existing_runs = ['demo/001']
  assert.equal((await h.invoke('study_launch', { target: 'demo' })).value.action, 'status')
})
test('researcher parameters cannot replace trusted identity or operation', async t => {
  const h = await setup(t, 'solver')
  assert.deepEqual(h.ctx.tools.schemas().map(v => v.name).sort(), [...SOLVER_TOOLS].sort())
  const result = await h.invoke('solver_result', { id: 'q1', operation: 'evaluate', run_id: 'other/001', session_id: 'other' })
  assert.equal(result.isError, false, JSON.stringify(result))
  assert.deepEqual(h.calls[0], { operation: 'research', payload: { operation: 'result', id: 'q1' }, scope: { runId: 'demo/001', sessionId: 'r1-r001' } })
  assert.equal((await h.invoke('final_measure', {})).isError, true)
  assert.equal((await h.invoke('solver_context', {}, false)).isError, true)
})
test('source is imported before research evaluation', async t => {
  const h = await setup(t, 'solver')
  const result = await h.invoke('solver_evaluate', { candidate_id: 'c1', source: 'solver.py', rationale: 'test', dataset: 'anchor', id: 'q1' })
  assert.equal(result.isError, false, JSON.stringify(result))
  assert.deepEqual(h.calls.map(item => item.operation), ['candidate-import', 'research'])
  assert.equal(h.calls[1].payload.manifest, 'research/candidates/c1/candidate.json')
  assert.equal((await h.invoke('solver_result', { id: 'q1', evaluation: 'e1' })).isError, true)
})

test('reuse uses the dedicated export payload without an extra operation', async t => {
  const h = await setup(t, 'solver')
  const record = { reuse_id: 'reuse-1', finding_id: 'finding-1' }
  assert.equal((await h.invoke('solver_reuse', { record })).isError, false)
  assert.deepEqual(h.calls[0], { operation: 'reuse-export', payload: { record }, scope: { runId: 'demo/001', sessionId: 'r1-r001' } })
})

test('research_start waits for researcher settlement unless explicitly launched asynchronously', async t => {
  const h = await setup(t, 'study')
  const args = { run_id: 'demo/001', assignments: [{ actor: 'r1', idea: 'seed' }] }
  const finished = await h.invoke('research_start', args)
  assert.equal(finished.isError, false)
  assert.equal(finished.value.status, 'completed')
  assert.deepEqual(h.calls, [{ operation: 'wait-run' }])
  const started = await h.invoke('research_start', { ...args, wait: false })
  assert.equal(started.value.status, 'running')
  assert.equal(h.calls.length, 1)
})

test('final and visualization mount independently and dispatch only their bounded operations',async t=>{
 const f=await setup(t,'final'),v=await setup(t,'visualization')
 assert.deepEqual(f.ctx.tools.schemas().map(x=>x.name).sort(),[...FINAL_TOOLS].sort())
 assert.deepEqual(v.ctx.tools.schemas().map(x=>x.name).sort(),[...VISUALIZATION_TOOLS].sort())
 assert.equal((await f.invoke('final_measure',{run_id:'demo/001',candidate:'baseline',dataset:'holdout',evaluation:'final-baseline'})).isError,false)
 assert.deepEqual(f.calls.at(-1),{operation:'measure',payload:{candidate:'baseline',dataset:'holdout',evaluation:'final-baseline',scope:'final'},scope:{runId:'demo/001'}})
 assert.equal((await f.invoke('final_write',{run_id:'demo/001',path:'final/report.md',content:'report'})).isError,false)
 assert.equal(f.calls.at(-1).operation,'terminal-report-write')
 assert.equal((await f.invoke('final_deliver',{run_id:'demo/001',kind:'figure',manifest:'visualization/x.json'})).isError,true)
 assert.equal((await v.invoke('visualization_render',{run_id:'demo/001',figure_id:'comparison',evaluation_ids:['e1'],chart:'comparison',code:'unexpected'})).isError,false)
 assert.equal('code' in v.calls.at(-1).payload,false,'native schema drops extras before bounded dispatch')
 assert.equal((await v.invoke('visualization_render',{run_id:'demo/001',figure_id:'comparison',evaluation_ids:['e1'],chart:'comparison'})).isError,false)
 assert.deepEqual(v.calls.at(-1),{operation:'visualization-render',payload:{figure_id:'comparison',evaluation_ids:['e1'],chart:'comparison'},scope:{runId:'demo/001'}})
 await f.ctx.loader.remove('surface');await f.ctx.loader.await()
 assert.equal(f.ctx.tools.schemas().length,0)
 assert.equal(v.ctx.tools.schemas().length,VISUALIZATION_TOOLS.length)
})
