import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import { createScope } from '@deepseek-ai/dsh-scope'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import StudyPolicy from '../lib/plugins/study-policy.js'
import { MAIN_TOOLS, SOLVER_TOOLS } from '../lib/core/policy-rules.js'

function definition(name, invoked) {
  return {
    name, description: 'Test capability', parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    execute: async () => { invoked.push(name); return name },
  }
}

for (const researcher of [true, false]) test(`real DSH tools inherit ${researcher ? 'researcher' : 'coordinator'} capabilities from the preset scope`, async t => {
  const ctx = new Context()
  ctx.provide('systemPrompt', { tools: () => () => {}, section: () => () => {}, getSectionOrder: () => 1 })
  const runtime = ctx.plugin(ToolRuntime, { mode: 'native' })
  await runtime
  t.after(() => runtime.dispose())
  let scopedOwner
  const owner = ctx.inject(['tools'], child => { scopedOwner = child })
  await owner
  t.after(() => owner.dispose())
  const invoked = []
  ctx.tools.register(definition('bash', invoked))
  ctx.tools.register(definition('plugin_manager', invoked))
  const presetKey = {}
  const preset = createScope(scopedOwner, presetKey)
  t.after(() => preset.dispose())
  const names = researcher ? SOLVER_TOOLS : MAIN_TOOLS
  for (const name of names) preset.ctx.tools.register(definition(name, invoked))
  // A second capability inherited through the preset must also be masked.
  preset.ctx.tools.register(definition('inherited_unsafe', invoked))
  const agent = { id: 'native-test', session: { header: { id: 'native-test', origin: researcher ? 'subagent' : 'interactive' } } }
  const scope = createScope(scopedOwner, agent, { parent: presetKey })
  agent.ctx = scope.ctx
  t.after(() => scope.dispose())
  let active = true
  ctx.provide('studies', { root: '/unused' })
  ctx.provide('studySessions', { binding: () => active ? { nativeId: agent.id, status: 'active' } : undefined })
  ctx.provide('agents', { list: () => [] })
  ctx.provide('agentPresets', { composedPreset: () => researcher ? 'anysolvers-researcher' : 'anysolvers-main' })
  ctx.provide('approval', {})
  const policy = ctx.plugin(StudyPolicy, { coordinatorPreset: 'anysolvers-main', researcherPreset: 'anysolvers-researcher' })
  await policy
  t.after(() => policy.dispose())
  await ctx.serial('agent/created', { agent, source: 'create' })
  assert.deepEqual(ctx.tools.schemas(agent).map(item => item.name).sort(), [...names].sort())
  const execute = name => ctx.tools.execute({ agent, callId: name, name, arguments: {}, signal: new AbortController().signal })
  const accepted = await execute(names[0])
  assert.equal(accepted.isError, false, JSON.stringify(accepted))
  assert.deepEqual(invoked, [names[0]])
  for (const name of ['bash', 'plugin_manager', 'inherited_unsafe', 'run_code']) assert.equal((await execute(name)).isError, true, name)
  // Scope-local tools cannot be hidden by restrict(), but the monotonic guard denies them.
  agent.ctx.tools.register(definition('scoped_unsafe', invoked))
  ctx.on('tools/pre-execute', async () => ({ kind: 'allow' }))
  assert.equal((await execute('scoped_unsafe')).isError, true)
  if (researcher) {
    active = false
    assert.equal((await execute(names[0])).isError, true)
  }
  assert.deepEqual(invoked, [names[0]])
})
