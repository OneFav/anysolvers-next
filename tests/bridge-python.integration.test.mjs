import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { PythonBridge } from '../lib/core/bridge.js'

// Explicit opt-in against an installed AnySolvers checkout. These operations
// only read module contracts and Docker metadata; they never create research
// runs, launch a model, import candidate code, or start a solver container.
const root = process.env.ANYSOLVERS_ROOT
const python = process.env.ANYSOLVERS_PYTHON
const enabled = Boolean(root && python)
const options = { skip: !enabled, timeout: 30_000 }

async function backend(t, overrides = {}) {
  const bridge = new PythonBridge({ root, python, maxInputBytes: 2 * 1024 * 1024,
    maxOutputBytes: 4 * 1024 * 1024, ...overrides })
  t.after(() => bridge.close())
  await bridge.validate()
  return bridge
}

test('real Python bridge returns verified task and research materials without a run', options, async t => {
  const bridge = await backend(t)
  for (const [slot, contract] of [['task', 'task'], ['research', 'candidate']]) {
    const result = await bridge.call('module', { slot })
    assert.equal(result.slot, slot)
    assert.equal(result.historical_read_only, false)
    assert.equal(result.sha, createHash('sha256').update(result.text).digest('hex'))
    assert.match(result.text, new RegExp(`\nkind: ${slot}\n`))
    assert.equal(typeof result.schemas[contract], 'object')
    assert.equal(result.schemas[contract].type, 'object')
    assert.equal(typeof result.execution, 'object')
    assert.ok(Buffer.byteLength(JSON.stringify(result)) < bridge.config.maxOutputBytes)
  }
})

test('real Python doctor reports read-only readiness without requiring Docker availability', options, async t => {
  const bridge = await backend(t)
  const result = await bridge.call('doctor')
  assert.equal(result.checks_are_read_only, true)
  assert.match(result.python_version, /^3\./)
  assert.equal(typeof result.docker_available, 'boolean')
  assert.equal(typeof result.daemon_ready, 'boolean')
  assert.equal(result.image_available, null)
})

test('real Python validation errors survive subprocess exit and JSON envelope parsing', options, async t => {
  const bridge = await backend(t)
  await assert.rejects(bridge.call('module', { slot: '../not-a-module' }),
    error => error.code === 'INVALID_REQUEST' && /unknown active module category/.test(error.message))
  await assert.rejects(bridge.call('status', {}, { sessionId: 'r1-r001' }),
    error => error.code === 'INVALID_REQUEST' && /coordinator operation/.test(error.message))
})

test('real generic discovery returns sealed studies without launching or requiring sample metadata', options, async t => {
  const bridge = await backend(t)
  const result = await bridge.call('launch-list')
  assert.equal(result.schema_version, 1)
  assert.ok(Array.isArray(result.studies))
  assert.ok(Array.isArray(result.invalid))
  await assert.rejects(bridge.call('launch-profile', {}, { sessionId: 'r1-r001' }),
    error => error.code === 'INVALID_REQUEST' && /coordinator operation/.test(error.message))
})

test('real Python module responses respect the Node output capacity limit', options, async t => {
  const bridge = await backend(t, { maxOutputBytes: 128 })
  await assert.rejects(bridge.call('module', { slot: 'research' }),
    error => error.code === 'OUTPUT_LIMIT')
})
