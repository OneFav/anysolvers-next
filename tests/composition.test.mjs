import test from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import { createScope } from '@deepseek-ai/dsh-scope'
import { MAIN_TOOLS, SOLVER_TOOLS } from '../lib/core/policy-rules.js'

/** A model-free loop adapter; registries, preset mounts and tool dispatch remain real DSH. */
function loopAdapter() {
  const handles = new Set()
  const queued = []
  let capture = false
  let loopContext
  return {
    handles, queued, captureFollowups() { capture = true },
    attach(ctx) { loopContext = ctx; ctx.agents.setFactory(this) },
    async createAgent(owner, options) {
      const session = owner.sessions.prepare(options.sessionId, { meta: options.meta, seed: options.seed })
      const agent = { id: session.id, session, options: options.agentOptions ?? {}, status: 'idle',
        inbox: { nextTurn: [], nextStep: [] },
        whenIdle: async () => {},
        followup: message => {
          if (!capture) throw new Error('This composition smoke must never drive a model')
          queued.push({ agent, message })
        },
      }
      const scoped = createScope(loopContext, agent)
      agent.ctx = scoped.ctx
      let detachSession = () => {}
      let detachAgent = () => {}
      let disposed = false
      const handle = { agent, async dispose() {
        if (disposed) return
        disposed = true
        detachAgent()
        detachSession()
        await scoped.dispose()
        handles.delete(handle)
      } }
      try {
        await options.setup?.(scoped.ctx, agent)
        options.signal?.throwIfAborted()
        // The real registry must not publish a partially composed identity.
        assert.equal(owner.agents.get(agent.id), undefined)
        assert.equal(owner.sessions.get(session.id), undefined)
        detachSession = scoped.ctx.sessions.enter(session)
        detachAgent = scoped.ctx.agents.enter(agent, options.parentAgent)
        scoped.ctx.sessions.announce(session)
        await scoped.ctx.agents.announce(agent, 'create', options.signal)
        handles.add(handle)
        return handle
      } catch (error) {
        await handle.dispose()
        throw error
      }
    },
    async resume() { throw new Error('The smoke adapter never restores or replays model sessions') },
  }
}

test('real DSH Loader, AgentRegistry and presets isolate both tool surfaces before agent publication', async (t) => {
  const baseUrl = new URL('../', import.meta.url).href
  const ctx = new Context().extend({ baseUrl })
  const calls = []
  const profile = { schema_version: 1, study_id: 'demo', config: 'configs/demo.yaml',
    budgets: { researchers: 2, rounds_per_researcher: 2, evaluation_calls: 11 }, existing_runs: [], final_permitted: true,
    assignments: [{ actor: 'r1', idea: 'seed' }, { actor: 'r2', idea: 'seed' }],
    datasets: [{ dataset_id: 'anchor', purpose: 'anchor' }, { dataset_id: 'test-set', purpose: 'final' }] }
  const catalog = { schema_version: 1, studies: [{ study_id: 'demo', task_id: 'scheduling' }] }
  const bindings = new Map()
  ctx.provide('studies', {
    root: '/unused',
    async call(operation, payload, scope) { calls.push({ operation, payload, scope }); return operation === 'ui-launch-profile' ? {...profile, existing_runs:[], new_batch:true} : operation === 'launch-list' ? catalog : operation === 'launch-profile' ? profile : { ok: true } },
  })
  ctx.provide('studySessions', { binding: (agent) => bindings.get(agent) })
  ctx.provide('studySandbox', { read: async () => '', write: async () => {}, list: async () => [] })
  ctx.provide('approval', { request: async () => { throw new Error('The smoke test never requests final evaluation') } })
  const loader = ctx.plugin(Loader, { baseUrl })
  await loader
  const adapter = loopAdapter()
  let loopFiber
  const unregisterPresets = []
  t.after(async () => {
    for (const handle of adapter.handles) await handle.dispose()
    for (const unregister of unregisterPresets.reverse()) await unregister()
    await loopFiber?.dispose()
    await loader.dispose()
    assert.equal(ctx.agents, undefined)
    assert.equal(ctx.sessions, undefined)
    assert.equal(ctx.tools, undefined)
  })
  await ctx.loader.root.update([
    { id: 'execution', name:'@onefav/research-runtime/service', config:{stateRoot:'/private/tmp/as-composition-runtime'} },
    { id: 'sessions', name: '@deepseek-ai/dsh-session' },
    { id: 'agents', name: '@deepseek-ai/dsh-agent' },
    { id: 'projections', name: '@deepseek-ai/dsh-session-projection' },
    { id: 'system-prompt', name: '@deepseek-ai/dsh-system-prompt' },
    { id: 'tools', name: '@deepseek-ai/dsh-tools', config: { mode: 'native' } },
    { id: 'commands', name: '@deepseek-ai/dsh-commands' },
    { id: 'presets', name: '@deepseek-ai/dsh-agent-preset-registry', config: { default: 'anysolvers-main' } },
    { id: 'policy', name: './lib/plugins/study-policy.js' },
  ])
  await ctx.loader.await()
  loopFiber = ctx.plugin({ name: 'model-free-test-loop', inject: ['agents', 'sessions', 'tools', 'systemPrompt'],
    apply: (loopCtx) => { adapter.attach(loopCtx) },
  })
  await loopFiber
  ctx.tools.register({ name: 'bash', description: 'Unavailable ambient capability',
    parameters: { type: 'object', properties: {} },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    execute: async () => { throw new Error('Ambient shell must never dispatch') },
  })
  for (const [id, module] of [['anysolvers-main', 'study'], ['anysolvers-researcher', 'solver']]) {
    unregisterPresets.push(await ctx.agentPresets.register({ id,
      plugins: (module==='study'?['study','final','visualization','as-research']:['solver']).map(m=>({name:`./lib/plugins/tool-${m}.js`})),
    }))
  }
  assert.deepEqual((await ctx.agentPresets.list()).map((preset) => ({ id: preset.id, broken: preset.broken })), [
    { id: 'anysolvers-main', broken: undefined }, { id: 'anysolvers-researcher', broken: undefined },
  ])
  const order = []
  const invoke = (agent, name, args = {}) => ctx.tools.execute({ agent, name, arguments: args,
    callId: randomUUID(), signal: new AbortController().signal })
  // Registered after the real policy: a created observer must already see its guard.
  ctx.on('agent/created', async ({ agent }) => {
    const preset = ctx.agentPresets.composedPreset(agent.ctx)
    const names = preset === 'anysolvers-main' ? MAIN_TOOLS : SOLVER_TOOLS
    assert.deepEqual(ctx.tools.schemas(agent).map((tool) => tool.name).sort(), [...names].sort())
    assert.equal((await invoke(agent, 'bash')).isError, true)
    if (preset === 'anysolvers-researcher') {
      const result = await invoke(agent, 'solver_context')
      assert.equal(result.isError, true)
      assert.match(JSON.stringify(result), /binding/)
    }
    order.push(`created:${preset}`)
  })
  const make = (preset, parentAgent) => ctx.agents.create({ sessionId: randomUUID(),
    ...(parentAgent ? { parentAgent } : {}),
    meta: { cwd: '/private/tmp', agentPreset: preset,
      ...(parentAgent ? { parentSession: parentAgent.id, origin: 'subagent', delegationDepth: 1 } : {}) },
    agentOptions: { provider: 'no-network-test', model: 'fake-model' },
    setup: async (agentCtx, agent) => {
      await ctx.agentPresets.mount(agentCtx, preset)
      assert.equal(ctx.agentPresets.composedPreset(agent.ctx), preset)
      order.push(`setup:${preset}`)
    },
  })
  const main = await make('anysolvers-main')
  const researcher = await make('anysolvers-researcher', main.agent)
  assert.deepEqual(order, ['setup:anysolvers-main', 'created:anysolvers-main',
    'setup:anysolvers-researcher', 'created:anysolvers-researcher'])
  assert.equal(ctx.agents.isOwnedBy(researcher.agent.id, main.agent), true)
  assert.equal(researcher.agent.session.firstLiveSeq, 0)
  assert.equal(researcher.agent.session.header.isSeeded, false)
  assert.equal(ctx.tools.get('solver_context', main.agent), undefined)
  assert.equal(ctx.tools.get('study_status', researcher.agent), undefined)
  bindings.set(researcher.agent, { runId: 'demo/001', sessionId: 'r1-r001', nativeId: researcher.agent.id,
    actor: 'r1', generation: 1, workspace: '/private/tmp', status: 'active' })
  const accepted = await invoke(researcher.agent, 'solver_context')
  assert.equal(accepted.isError, false, JSON.stringify(accepted))
  assert.deepEqual(calls, [{ operation: 'research', payload: { operation: 'context' }, scope: { runId: 'demo/001', sessionId: 'r1-r001' } }])
  assert.equal(ctx.commands.find(researcher.agent, 'as-research'), undefined)
  assert.equal(ctx.commands.list(main.agent).some(item => item.name === 'as-research'), true)
  assert.equal(ctx.commands.find(researcher.agent, 'anysolvers'), undefined)
  assert.equal(ctx.commands.list(main.agent).some(item => item.name === 'anysolvers'), true)
  const command = (agent, line) => ctx.commands.execute(agent, line, [], new AbortController().signal)
  assert.equal((await command(main.agent, '/anysolvers help')).result.kind, 'success')
  assert.equal(adapter.queued.length, 0)
  adapter.captureFollowups()
  catalog.studies.push({ study_id: 'another-study', task_id: 'routing' })
  const choices = await command(main.agent, '/anysolvers')
  assert.equal(choices.result.kind, 'success')
  assert.match(choices.result.text, /another-study/)
  assert.equal(adapter.queued.length, 0)
  catalog.studies.pop()
  assert.equal((await command(main.agent, '/anysolvers')).result.kind, 'success')
  assert.equal(adapter.queued.length, 1)
  assert.equal(adapter.queued[0].message.source.kind, 'anysolvers-command')
  assert.equal(adapter.queued[0].message.source.action, 'start')
  assert.equal(adapter.queued[0].message.source.studyId, 'demo')
  assert.match(adapter.queued[0].message.content[0].text, /batch_init/)
  assert.equal((await command(main.agent, '/anysolvers')).result.kind, 'error')
  assert.equal(adapter.queued.length, 1)
  const other = await make('anysolvers-main')
  other.agent.status = 'running'
  assert.equal((await command(other.agent, '/anysolvers')).result.kind, 'error')
  other.agent.status = 'idle'
  profile.existing_runs = ['demo/001']
  assert.equal((await command(other.agent, '/anysolvers')).result.kind, 'success')
  assert.equal(adapter.queued.at(-1).message.source.action, 'status')
  assert.equal((await command(other.agent, '/anysolvers final demo/001')).result.kind, 'success')
  assert.equal(adapter.queued.at(-1).message.source.action, 'final')
  assert.match(adapter.queued.at(-1).message.content[0].text, /未获批准就停止/)
  assert.equal(calls.at(-1).scope.runId, 'demo/001')
  assert.equal(calls.filter(item => !['research', 'launch-profile', 'launch-list'].includes(item.operation)).length, 0)
  const uiMain = await make('anysolvers-main')
  const uiResult = await command(uiMain.agent, '/anysolvers new demo --config configs/ui/demo.yaml')
  assert.equal(uiResult.result.kind, 'success')
  assert.equal(calls.at(-1).operation, 'ui-claim')
  assert.equal(calls.at(-1).payload.session, uiMain.agent.id)
  assert.equal(adapter.queued.at(-1).message.source.action, 'start')
  assert.match(adapter.queued.at(-1).message.content[0].text, /batch_init/)
  await uiMain.dispose()
  await other.dispose()
  for (const handle of [researcher, main]) {
    await handle.dispose()
    assert.equal(ctx.agents.get(handle.agent.id), undefined)
    assert.equal(ctx.sessions.get(handle.agent.id), undefined)
  }
  assert.equal(adapter.handles.size, 0)
  assert.deepEqual(ctx.agents.list(), [])
  for (const composition of ctx.agentPresets.inspectCompositions()) assert.deepEqual(composition.leakedServices, [])
  for (const unregister of unregisterPresets.splice(0).reverse()) await unregister()
  assert.deepEqual(ctx.agentPresets.inspectCompositions(), [])
  assert.deepEqual(ctx.tools.schemas().map((tool) => tool.name), ['bash'])
})
