import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { createScope } from '@deepseek-ai/dsh-scope'
import StudyPolicy from '../lib/plugins/study-policy.js'
import { MAIN_TOOLS, SOLVER_TOOLS, authorizationScope, scopeDigest, readAuthorization, saveAuthorization } from '../lib/core/policy-rules.js'

async function setup(t, { outcome = 'allowed-once', researcher = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'anysolvers-policy-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  const ctx = new Context()
  const status = { selection: { candidate_id: 'c1', source_sha: 'aaa' }, artifacts: { dataset: ['anchor', 'holdout'] }, final_datasets: [{ dataset_id: 'holdout', manifest_sha: 'bbb', files: { 'datasets/holdout/manifest.json': 'bbb', 'datasets/holdout/cases.json': 'ccc' } }], sessions: [], pending_requests: [] }
  const config = { final: { authorized: true }, budgets: { evaluation_calls: 4 }, initialization: { sha: 'sealed' } }
  const captures = { approvals: 0, restrictions: [], guards: [], modes: [], disposals: 0 }
  let exhausted = false
  let binding = { runId: 'demo/001', actor: 'r1', sessionId: 'r1-r001', nativeId: 'native-1', workspace: '/unused', generation: 1, status: 'active' }
  ctx.provide('studies', { root, status: async () => status, call: async operation => operation === 'config' ? config : { research_time: { exhausted } } })
  ctx.provide('studySessions', { binding: agent => agent.id === 'native-1' ? binding : undefined })
  const dispose = () => { captures.disposals++ }
  ctx.provide('tools', {
    schemas: () => [...MAIN_TOOLS, ...SOLVER_TOOLS, 'bash', 'plugin_manager'].map(name => ({ name })),
    presentAs: mode => { captures.modes.push(mode); return dispose },
    restrict: rule => { captures.restrictions.push(rule); return dispose },
    guard: callback => { captures.guards.push(callback); return dispose },
  })
  ctx.provide('agents', { list: () => [] })
  ctx.provide('agentPresets', { composedPreset: () => researcher ? 'anysolvers-researcher' : 'anysolvers-main' })
  ctx.provide('approval', { request: async () => { captures.approvals++; return outcome } })
  const fiber = ctx.plugin(StudyPolicy, { coordinatorPreset: 'anysolvers-main', researcherPreset: 'anysolvers-researcher' })
  await fiber
  t.after(() => fiber.dispose())
  const agent = { id: researcher ? 'native-1' : 'coordinator-1', session: { header: { id: researcher ? 'native-1' : 'coordinator-1' } } }
  const scoped = createScope(ctx, agent)
  agent.ctx = scoped.ctx
  t.after(() => scoped.dispose())
  await ctx.serial('agent/created', { agent, source: 'create' })
  return { root, ctx, agent, status, config, captures, fiber, setExhausted: value => { exhausted = value }, setBinding: value => { binding = value } }
}

test('final permission is scoped to immutable inputs and survives a new service instance', async t => {
  const item = await setup(t)
  await item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal)
  await item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal)
  assert.equal(item.captures.approvals, 1)
  const saved = JSON.parse(await readFile(join(item.root, 'runs/demo/001/dsh/authorization.json'), 'utf8'))
  assert.equal(saved.approved_by, 'coordinator-1')
  assert.equal(await readAuthorization(item.root, 'demo/001', saved.digest), true)
  await item.fiber.dispose()
  const replacement = item.ctx.plugin(StudyPolicy, { coordinatorPreset: 'anysolvers-main', researcherPreset: 'anysolvers-researcher' })
  await replacement
  t.after(() => replacement.dispose())
  await item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal)
  assert.equal(item.captures.approvals, 1)
  item.config.budgets.evaluation_calls = 5
  await item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal)
  assert.equal(item.captures.approvals, 2)
  item.status.final_datasets[0].files['datasets/holdout/cases.json'] = 'changed'
  await item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal)
  assert.equal(item.captures.approvals, 3)
})

test('configuration true does not replace approval, and rejection leaves no grant', async t => {
  const item = await setup(t, { outcome: 'rejected' })
  await assert.rejects(item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal), /not been authorized/)
  assert.equal(item.captures.approvals, 1)
  assert.equal(await readAuthorization(item.root, 'demo/001', 'anything'), false)
})

test('researcher identity cannot be supplied as another run and expiration still permits handover', async t => {
  const item = await setup(t, { researcher: true })
  await assert.rejects(item.ctx.studyPolicy.authorize(item.agent, 'solver_result', { runId: 'other/001' }), /another run/)
  await assert.rejects(item.ctx.studyPolicy.authorize(item.agent, 'final_measure', { runId: 'demo/001' }), /not available/)
  for (const tool of ['study_selection_report_context','study_selection_report_submit']) {
    await assert.rejects(item.ctx.studyPolicy.authorize(item.agent, tool, { runId: 'demo/001' }), /not available/)
  }
  item.setExhausted(true)
  await assert.rejects(item.ctx.studyPolicy.authorize(item.agent, 'solver_evaluate'), /exhausted/)
  await assert.rejects(item.ctx.studyPolicy.authorize(item.agent, 'solver_write'), /exhausted/)
  await item.ctx.studyPolicy.authorize(item.agent, 'solver_checkpoint')
  await item.ctx.studyPolicy.authorize(item.agent, 'solver_result')
})

test('the scoped guard denies inherited arbitrary execution and unbound researchers', async t => {
  const item = await setup(t, { researcher: true })
  assert.deepEqual(item.captures.modes, ['native'])
  assert.ok(!item.captures.restrictions[0].allow.includes('bash'))
  const guard = item.captures.guards[0]
  assert.match(guard({ agent: item.agent, name: 'plugin_manager' }), /outside/)
  assert.match(guard({ agent: item.agent, name: 'run_code' }), /outside/)
  assert.equal(guard({ agent: item.agent, name: 'solver_result' }), undefined)
  item.setBinding(undefined)
  assert.match(guard({ agent: item.agent, name: 'solver_result' }), /binding/)
  await item.fiber.dispose()
  assert.equal(item.captures.disposals, 3)
})

test('final measurement rejects unfrozen and active runs before requesting permission', async t => {
  const item = await setup(t)
  item.status.selection = null
  await assert.rejects(item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal), /Freeze/)
  item.status.selection = { candidate_id: 'c1' }
  item.status.sessions = [{ status: 'active' }]
  await assert.rejects(item.ctx.studyPolicy.authorizeFinal(item.agent, 'demo/001', new AbortController().signal), /End all/)
  assert.equal(item.captures.approvals, 0)
})

test('failure reports can be written and delivered without a frozen candidate only after research is settled', async t => {
  const item = await setup(t)
  item.status.selection = null
  item.status.sessions = [{ status: 'completed' }, { status: 'failed' }]
  for (const operation of ['final_write', 'final_deliver','visualization_render','visualization_deliver']) {
    await item.ctx.studyPolicy.authorize(item.agent, operation, { runId: 'demo/001' })
    item.status.sessions.push({ status: 'active' })
    await assert.rejects(item.ctx.studyPolicy.authorize(item.agent, operation, { runId: 'demo/001' }), /End all/)
    item.status.sessions.pop()
    item.status.pending_requests = ['unfinished-attempt']
    await assert.rejects(item.ctx.studyPolicy.authorize(item.agent, operation, { runId: 'demo/001' }), /Reconcile/)
    item.status.pending_requests = []
  }
  assert.equal(item.captures.approvals, 0)
})

test('authorization records reject symlink redirection and scope tampering', async t => {
  const item = await setup(t)
  const scope = authorizationScope('demo/001', item.status, item.config)
  await saveAuthorization(item.root, 'demo/001', scope, 'coordinator-1')
  const path = join(item.root, 'runs/demo/001/dsh/authorization.json')
  const value = JSON.parse(await readFile(path, 'utf8'))
  value.scope.selection.candidate_id = 'different'
  await writeFile(path, JSON.stringify(value))
  await assert.rejects(readAuthorization(item.root, 'demo/001', scopeDigest(scope)), /checksum/)
  await rm(path)
  await writeFile(join(item.root, 'external'), '{}')
  await symlink(join(item.root, 'external'), path)
  await assert.rejects(readAuthorization(item.root, 'demo/001', scopeDigest(scope)))
})

test('researchers cannot invoke final or visualization tools even when registered elsewhere',async t=>{
 const item=await setup(t,{researcher:true})
 for(const op of ['final_status','final_evidence','visualization_evidence','visualization_render','visualization_deliver']){
  await assert.rejects(item.ctx.studyPolicy.authorize(item.agent,op,{runId:'demo/001'}),/not available/)
 }
})


test('as-research final consent is tied to the bound run; legacy approval remains explicit',async t=>{
  const item=await setup(t)
  item.ctx.studies.projectBinding=async()=>({root:item.root,projectId:'p',runId:'demo/001'})
  const call=item.ctx.studies.call
  item.ctx.studies.call=async(op,payload,...rest)=>op==='as-consent'?{authorized:payload.session===item.agent.id}:call(op,payload,...rest)
  await item.ctx.studyPolicy.authorizeFinal(item.agent,'demo/001',new AbortController().signal)
  assert.equal(item.captures.approvals,0)
  await assert.rejects(item.ctx.studyPolicy.authorize(item.agent,'final_status',{runId:'demo/002'}),/绑定项目/)
  await assert.rejects(item.ctx.studyPolicy.authorize(item.agent,'batch_init',{studyId:'demo'}),/独立主会话/)
  await item.ctx.studyPolicy.authorize(item.agent,'as_research')
  const saved=JSON.parse(await readFile(join(item.root,'runs/demo/001/dsh/authorization.json'),'utf8'))
  assert.equal(saved.approved_by,item.agent.id)
})
