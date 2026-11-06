import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile, symlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { SessionController, launchOptions } from '../lib/core/session-controller.js'
import { SessionJournal } from '../lib/core/session-journal.js'

const runId = 'sample/001'
const config = { provider: 'configured-provider', researcherPreset: 'anysolvers-researcher', coordinatorPreset: 'anysolvers-main' }

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

async function until(check, message = 'expected lifecycle transition') {
  const limit = Date.now() + 4000
  while (Date.now() < limit) {
    if (await check()) return
    await new Promise((done) => setTimeout(done, 5))
  }
  assert.fail(message)
}

async function fixture(t, settings = {}) {
  const root = await mkdtemp(join(tmpdir(), 'anysolvers-sessions-'))
  await mkdir(join(root, 'runs', runId), { recursive: true })
  const events = []
  const sessions = []
  const natives = new Map()
  const children = []
  const notebooks = new Map()
  const pending = new Map()
  const controllers = []
  const parent = { id: 'real-coordinator', session: { header: {} }, ctx: {} }
  let controller
  const studies = {
    root, python: '/unused/python',
    async status() { return { sessions: structuredClone(sessions), pending_requests: [...pending.values()].flat() } },
    async call(operation, payload = {}, scope = {}) {
      events.push({ operation, payload: structuredClone(payload), scope: structuredClone(scope) })
      if (settings.beforeCall) await settings.beforeCall(operation, payload, scope)
      if (operation === 'config') return { budgets: { researchers: 2, rounds_per_researcher: settings.rounds ?? 2 }, modules:{selections:{capabilities:{coordinator_authored:settings.coordinatorSelection===true}}} }
      if(operation==='selection-context')return {pending:[],selected:settings.directions??[]}
      if (operation === 'begin-session') {
        const generation = sessions.filter((item) => item.actor === payload.actor).length + 1
        const session = { session_id: `${payload.actor}-r${String(generation).padStart(3, '0')}`, actor: payload.actor,
          generation, status: 'active', native_id: null, activated_at: null,
          spawn_options: { model: 'requested-model', reasoning_effort: 'high', fork_turns: 'none' } }
        sessions.push(session)
        if (settings.beginErrorAfterCommit) throw new Error('registration response lost')
        return structuredClone(session)
      }
      if (operation === 'activate-session') {
        if (settings.activateError) throw new Error('activation unavailable')
        const session = sessions.findLast((item) => item.actor === payload.actor)
        session.native_id = payload.native_id
        session.activated_at = '2026-01-01T00:00:00Z'
        return structuredClone(session)
      }
      if (operation === 'research') {
        assert.equal(payload.operation, 'context')
        const session = sessions.find((item) => item.session_id === scope.sessionId)
        return { session: structuredClone(session), latest_notebook: notebooks.get(scope.sessionId) ?? null,
          research_time: { pending_request_ids: pending.get(scope.sessionId) ?? [], pending_preflight_ids: [], exhausted: settings.exhausted ?? false } }
      }
      if (operation === 'research-materials') return {
        task_description: 'A sealed development task', researcher_instructions: 'Use your own notebook.',
        development_datasets: [{ dataset_id: 'anchor', purpose: 'anchor' }], schemas: {},
      }
      if (operation === 'audit') return { pending_requests: [...pending.values()].flat() }
      if (operation === 'end-session') {
        const session = sessions.findLast((item) => item.actor === payload.actor && item.status === 'active')
        assert.ok(session, 'Python session must still be active')
        if (payload.native_id !== undefined) assert.equal(payload.native_id, session.native_id)
        if (payload.status === 'completed') assert.ok(notebooks.has(session.session_id), 'completed requires checkpoint')
        assert.equal((pending.get(session.session_id) ?? []).length, 0)
        session.status = payload.status
        return structuredClone(session)
      }
      throw new Error(`unexpected operation: ${operation}`)
    },
  }
  const sandbox = {
    async prepare(run, sid) { events.push({ operation: 'sandbox.prepare', sid }); return join(root, 'workspace', sid) },
    async release(run, sid) { events.push({ operation: 'sandbox.release', sid }) },
  }
  const deps = {
    studies, sandbox,
    get: (id) => natives.get(id),
    isCoordinator: (agent) => agent === parent,
    async mountPreset(ctx, agent) { events.push({ operation: 'preset.mount', id: agent.id }) },
    async validateRoute(options) {
      events.push({ operation: 'route.validate', options })
      if (settings.routeError) throw new Error('model is unsupported by configured provider')
    },
    async create(options) {
      events.push({ operation: 'native.create', options })
      const durable = JSON.parse(await readFile(join(root, 'runs', runId, 'dsh/controller.json'), 'utf8'))
      assert.ok(durable.launches.some((item) => item.nativeId === options.sessionId && item.phase === 'creating'),
        'native launch intent must be durable before creation')
      if (settings.createError) throw new Error('unknown factory failure')
      const idle = deferred()
      const agent = {
        id: options.sessionId, ctx: {}, session: { header: options.meta }, status: 'idle', inbox: { nextTurn: [], nextStep: [] },
        options: settings.changedOptions ? { ...options.agentOptions, model: 'unexpected-model' } : options.agentOptions,
        followup(message) {
          assert.equal(controller.binding(agent)?.status, 'active', 'first synchronous tool must see the active binding')
          const python = sessions.find((item) => item.native_id === agent.id)
          assert.ok(python?.activated_at, 'Python activation must precede any model prompt')
          events.push({ operation: 'native.followup', id: agent.id, message })
          agent.status = 'running'
          if (settings.followupError) throw new Error('delivery outcome unknown')
        },
        whenIdle() { return idle.promise },
      }
      const handle = { agent, async dispose() {
        events.push({ operation: 'native.dispose', id: agent.id })
        if (settings.disposeError) throw new Error('native cleanup unknown')
        agent.status = 'idle'
        natives.delete(agent.id)
        idle.resolve()
      } }
      natives.set(agent.id, agent)
      const child = { agent, handle, options, idle }
      children.push(child)
      await options.setup?.(agent.ctx, agent)
      return handle
    },
  }
  function makeController() {
    const result = new SessionController(deps, config)
    controllers.push(result)
    return result
  }
  controller = makeController()
  t.after(async () => {
    settings.disposeError = false
    await Promise.allSettled(controllers.map((item) => item.dispose()))
    await rm(root, { recursive: true, force: true })
  })
  const complete = (child, next = null) => {
    const session = sessions.find((item) => item.native_id === child.agent.id)
    notebooks.set(session.session_id, { payload: { next_iteration_id: next } })
    child.agent.status = 'idle'
    child.idle.resolve()
  }
  return { root, events, sessions, natives, children, notebooks, pending, parent, controller, makeController, complete }
}

test('fresh native creation is durably bound and activated before tools; repeats are idempotent', async (t) => {
  const f = await fixture(t)
  const result = await f.controller.start(f.parent, runId, 'alice', 'seed')
  assert.equal(result.status, 'active')
  assert.equal(f.children.length, 1)
  const child = f.children[0]
  assert.equal(result.native_id, child.agent.id)
  assert.equal(child.options.parentAgent, f.parent)
  assert.equal(child.options.seed, undefined, 'parent transcript is never copied')
  assert.equal(child.options.meta.agentPreset, 'anysolvers-researcher')
  assert.deepEqual(child.options.agentOptions, { provider: config.provider, model: 'requested-model', reasoningEffort: 'high' })
  assert.equal(f.controller.binding({ id: child.agent.id }), undefined, 'an ID-shaped impostor is not the real agent')
  assert.equal(f.controller.binding(child.agent).sessionId, result.session_id)
  assert.equal(f.events.filter((event) => event.operation === 'preset.mount').length, 1)
  const delivered = f.events.find((event) => event.operation === 'native.followup').message
  assert.equal(delivered.source.senderSessionId, f.parent.id)
  assert.match(delivered.content[0].text, /A sealed development task/)
  assert.match(delivered.content[0].text, /Use your own notebook/)
  const repeated = await f.controller.start(f.parent, runId, 'alice', 'seed')
  assert.equal(repeated.native_id, result.native_id)
  assert.equal(f.events.filter((event) => event.operation === 'begin-session').length, 1)
  await assert.rejects(f.controller.start(f.parent, runId, 'alice', 'other'), /unresolved launch/)
  const journal = await f.controller.journal.read(runId)
  assert.equal('budgets' in journal, false)
  assert.equal('research_time' in journal.launches[0], false)
  f.complete(child)
  await until(async () => (await f.controller.journal.read(runId)).launches[0].phase === 'completed')
  assert.equal(f.controller.binding(child.agent), undefined)
  assert.equal(f.sessions[0].status, 'completed')
  assert.equal((await f.controller.start(f.parent, runId, 'alice', 'seed')).native_id, result.native_id)
  assert.equal(f.children.length, 1)
})

test('recovery never creates a replacement or replays a prompt for an unowned native identity', async (t) => {
  const f = await fixture(t)
  const first = await f.controller.start(f.parent, runId, 'alice', 'seed')
  const recoveredController = f.makeController()
  const recovered = await recoveredController.recover(f.parent, runId)
  assert.equal(recovered.needs_reconciliation, true)
  assert.equal(recovered.sessions[0].native_id, first.native_id)
  assert.equal(recovered.sessions[0].native_live, true)
  assert.equal(recovered.sessions[0].python_identity_matches, true)
  const repeated = await recoveredController.start(f.parent, runId, 'alice', 'seed')
  assert.equal(repeated.needs_reconciliation, true)
  assert.equal(repeated.native_id, first.native_id)
  assert.equal(f.events.filter((event) => event.operation === 'native.followup').length, 1)
  assert.equal(f.events.filter((event) => event.operation === 'begin-session').length, 1)
})

test('known unsupported model fails closed without starting a model or substituting its parameters', async (t) => {
  const f = await fixture(t, { routeError: true })
  const result = await f.controller.start(f.parent, runId, 'alice', 'seed')
  assert.equal(result.status, 'failed')
  assert.match(result.issue, /unsupported/)
  assert.equal(f.children.length, 0)
  assert.equal(f.sessions[0].status, 'failed')
  const same = await f.controller.start(f.parent, runId, 'alice', 'seed')
  assert.equal(same.native_id, result.native_id)
  assert.equal(f.events.filter((event) => event.operation === 'begin-session').length, 1)
  assert.throws(() => launchOptions({ spawn_options: { model: 'm', reasoning_effort: 'high', fork_turns: 'all' } }, 'p'), /fresh context/)
  assert.throws(() => launchOptions({ spawn_options: { model: 'm', reasoning_effort: 'high', fork_turns: 'none', temperature: 1 } }, 'p'), /Unsupported launch option/)
})

test('ambiguous factory and delivery failures preserve reconciliation instead of blind relaunch', async (t) => {
  for (const settings of [{ createError: true }, { followupError: true }]) {
    const f = await fixture(t, settings)
    const result = await f.controller.start(f.parent, runId, 'alice', 'seed')
    assert.equal(result.needs_reconciliation, true)
    assert.equal(f.sessions[0].status, 'active')
    assert.equal(f.events.filter((event) => event.operation === 'end-session').length, 0)
    const same = await f.controller.start(f.parent, runId, 'alice', 'seed')
    assert.equal(same.native_id, result.native_id)
    assert.equal(f.events.filter((event) => event.operation === 'native.create').length, 1)
  }
})

test('a lost begin response preserves its durable intent and never registers a second session', async (t) => {
  const f = await fixture(t, { beginErrorAfterCommit: true })
  const result = await f.controller.start(f.parent, runId, 'alice', 'seed')
  assert.equal(result.needs_reconciliation, true)
  assert.equal(result.session_id, null)
  assert.equal(f.sessions.length, 1)
  assert.equal(f.children.length, 0)
  assert.equal((await f.controller.start(f.parent, runId, 'alice', 'seed')).native_id, result.native_id)
  assert.equal(f.sessions.length, 1)
  assert.equal((await f.controller.recover(f.parent, runId)).needs_reconciliation, true)
})

test('factory-side model substitution is rejected before activation or prompt delivery', async (t) => {
  const f = await fixture(t, { changedOptions: true })
  const result = await f.controller.start(f.parent, runId, 'alice', 'seed')
  assert.equal(result.status, 'failed')
  assert.match(result.issue, /changed.*model settings/)
  assert.equal(f.events.some((event) => ['activate-session', 'native.followup'].includes(event.operation)), false)
  assert.equal(f.natives.size, 0)
})

test('activation failure closes the known unused session only after confirmed native disposal', async (t) => {
  const f = await fixture(t, { activateError: true })
  const result = await f.controller.start(f.parent, runId, 'alice', 'seed')
  assert.equal(result.status, 'failed')
  assert.equal(f.natives.size, 0)
  assert.equal(f.events.some((event) => event.operation === 'native.followup'), false)
  assert.ok(f.events.findIndex((event) => event.operation === 'native.dispose') < f.events.findIndex((event) => event.operation === 'end-session'))
})

test('ending requires the actual idle agent and no unresolved work; expiration does not cancel it', async (t) => {
  const f = await fixture(t, { exhausted: true })
  const result = await f.controller.start(f.parent, runId, 'alice', 'seed')
  const child = f.children[0]
  await assert.rejects(f.controller.end(f.parent, runId, 'alice', 'completed'), /still active/)
  assert.equal(child.agent.status, 'running', 'a Python budget expiry is not a kill timer')
  f.pending.set(result.session_id, ['unfinished-evaluation'])
  f.complete(child)
  await until(async () => (await f.controller.journal.read(runId)).launches[0].phase === 'reconciliation')
  assert.equal(f.events.filter((event) => event.operation === 'end-session').length, 0)
  assert.equal(f.natives.size, 1)
  f.pending.clear()
  f.natives.set(result.native_id, { id: result.native_id })
  await assert.rejects(f.controller.end(f.parent, runId, 'alice', 'completed'), /requires reconciliation/)
  f.natives.set(result.native_id, child.agent)
  assert.equal((await f.controller.end(f.parent, runId, 'alice', 'completed')).status, 'completed')
})

test('each assigned actor advances independently using checkpoint next_iteration_id and configured rounds', async (t) => {
  const f = await fixture(t)
  assert.equal((await f.controller.startRun(f.parent, runId, [{ actor: 'alice', idea: 'seed-a' }, { actor: 'bob', idea: 'seed-b' }])).status, 'running')
  let settled = false
  const waiting = f.controller.waitRun(f.parent, runId).then(result => { settled = true; return result })
  await until(() => f.events.filter((event) => event.operation === 'native.followup').length === 2)
  const bob1 = f.children.find((child) => f.sessions.find((item) => item.native_id === child.agent.id)?.actor === 'bob')
  f.complete(bob1, 'bob-next')
  await until(() => f.events.filter((event) => event.operation === 'native.followup').length === 3)
  assert.equal(f.sessions.filter((item) => item.actor === 'alice').length, 1, 'Bob continues while Alice is still running')
  assert.equal(settled, false, 'waiting includes every actor and all continuation rounds')
  const bob2 = f.children[2]
  assert.notEqual(bob2.agent.id, bob1.agent.id)
  const secondBegin = f.events.filter((event) => event.operation === 'begin-session' && event.payload.actor === 'bob')[1]
  assert.deepEqual(secondBegin.payload, { actor: 'bob', iteration: 'bob-next' })
  f.complete(bob2)
  const alice1 = f.children.find((child) => f.sessions.find((item) => item.native_id === child.agent.id)?.actor === 'alice')
  f.complete(alice1, 'alice-next')
  await until(() => f.events.filter((event) => event.operation === 'native.followup').length === 4)
  f.complete(f.children[3])
  await until(async () => (await f.controller.journal.read(runId)).plan.status === 'completed')
  assert.equal((await waiting).status, 'completed')
  assert.equal(f.events.filter((event) => event.operation === 'begin-session').length, 4)
  assert.equal(f.events.some((event) => ['measure', 'select'].includes(event.operation)), false)
  assert.equal((await f.controller.startRun(f.parent, runId, [{ actor: 'alice', idea: 'seed-a' }, { actor: 'bob', idea: 'seed-b' }])).status, 'completed')
  assert.equal(f.children.length, 4)
})

test('unload disposes owned natives and workspaces while preserving unresolved Python facts', async (t) => {
  const f = await fixture(t)
  const started = await f.controller.start(f.parent, runId, 'alice', 'seed')
  f.pending.set(started.session_id, ['pending-request'])
  await f.controller.dispose()
  assert.equal(f.natives.size, 0)
  assert.equal(f.sessions[0].status, 'active')
  const journal = await f.controller.journal.read(runId)
  assert.equal(journal.launches[0].phase, 'reconciliation')
  assert.equal(journal.launches[0].nativeId, started.native_id)
  assert.equal(f.events.filter((event) => event.operation === 'end-session').length, 0)
  assert.equal(f.events.filter((event) => event.operation === 'sandbox.release').length, 1)
})

test('journal refuses corrupt, symlinked, or locked controller state', async (t) => {
  const f = await fixture(t)
  const journal = new SessionJournal(f.root)
  await journal.transaction(runId, async (_, save) => save())
  const directory = join(f.root, 'runs', runId, 'dsh')
  await writeFile(join(directory, 'controller.lock'), 'unknown prior owner')
  await assert.rejects(journal.transaction(runId, async () => {}), /reconcile its owner/)
  assert.equal((await f.controller.recover(f.parent, runId)).needs_reconciliation, true)
  await rm(join(directory, 'controller.lock'))
  await writeFile(join(directory, 'controller.json'), '{"schemaVersion":99}')
  await assert.rejects(journal.read(runId), /invalid controller journal/)
  await rm(join(directory, 'controller.json'))
  await writeFile(join(f.root, 'elsewhere.json'), '{}')
  await symlink(join(f.root, 'elsewhere.json'), join(directory, 'controller.json'))
  await assert.rejects(journal.read(runId))
  await assert.rejects(journal.read('../outside'), /Invalid run/)
})


test('selection-generated pivots drive 2x2 independently while a peer is still active (deterministic native doubles)', {timeout:15000}, async t=>{
 const settings={coordinatorSelection:true,directions:[]}
 const f=await fixture(t,settings)
 await f.controller.startRun(f.parent,runId,[{actor:'alice',idea:'a'},{actor:'bob',idea:'b'}])
 await until(()=>f.children.length===2 && f.children.at(-1).agent.status==='running' && f.sessions.every(s=>s.activated_at))
 const childFor=actor=>f.children.find(c=>f.sessions.find(s=>s.native_id===c.agent.id)?.actor===actor)
 f.complete(childFor('alice'))
 const waiting=await f.controller.waitRun(f.parent,runId)
 assert.equal(waiting.status,'awaiting_selection');assert.equal(waiting.needs_reconciliation,false)
 assert.equal(f.sessions.find(s=>s.actor==='bob').status,'active')
 settings.directions.push({actor:'alice',generation:2,iteration_id:'independent-pivot'})
 await until(()=>f.children.length===3 && f.children.at(-1).agent.status==='running' && f.sessions.every(s=>s.activated_at))
 assert.equal(f.sessions.find(s=>s.actor==='bob').status,'active','alice continues before bob ends round one')
 assert.ok(f.events.some(e=>e.operation==='begin-session'&&e.payload.actor==='alice'&&e.payload.iteration==='independent-pivot'))
 f.complete(f.children[2]);f.complete(childFor('bob'))
 settings.directions.push({actor:'bob',generation:2,iteration_id:'different-route'})
 await until(()=>f.children.length===4 && f.children.at(-1).agent.status==='running' && f.sessions.every(s=>s.activated_at))
 f.complete(f.children[3])
 await until(async()=>(await f.controller.journal.read(runId)).plan.status==='completed')
 assert.equal((await f.controller.waitRun(f.parent,runId)).status,'completed')
 assert.equal(f.sessions.length,4)
})
