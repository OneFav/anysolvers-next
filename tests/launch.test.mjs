import test from 'node:test'
import assert from 'node:assert/strict'
import { launchPlan, parseLaunchInput, prepareLaunch } from '../lib/core/launch.js'

const profile = () => ({ schema_version: 1, study_id: 'route-study', config: 'configs/routes.yaml',
  task_id: 'routing', budgets: { researchers: 3, rounds_per_researcher: 4, evaluation_calls: null },
  assignments: [{ actor: 'r1', idea: 'distance' }], final_permitted: true, existing_runs: [],
  datasets: [{ dataset_id: 'cities-a', purpose: 'anchor' }, { dataset_id: 'cities-b', purpose: 'anchor' },
    { dataset_id: 'test-north', purpose: 'final' }, { dataset_id: 'test-south', purpose: 'final' }] })

test('generic command accepts study, pinned run and explicit configuration as data', () => {
  assert.deepEqual(parseLaunchInput(''), { action: 'start' })
  assert.deepEqual(parseLaunchInput('route-study --config configs/routes.yaml'),
    { action: 'start', target: 'route-study', config: 'configs/routes.yaml' })
  assert.deepEqual(parseLaunchInput('status route-study/002'), { action: 'status', target: 'route-study/002' })
  assert.deepEqual(parseLaunchInput('final route-study'), { action: 'final', target: 'route-study' })
  for (const input of ['../other', 'a b', '--unknown', '--config', '--config a --config b']) {
    assert.throws(() => parseLaunchInput(input))
  }
})

test('discovery never silently picks among studies or substitutes a sample when none exists', async () => {
  for (const studies of [[], [{ study_id: 'routing' }, { study_id: 'scheduling' }]]) {
    const calls = []
    const ctx = { studies: { async call(...args) { calls.push(args); return { schema_version: 1, studies, invalid: [] } } } }
    const result = await prepareLaunch(ctx, { action: 'start' }, new AbortController().signal)
    assert.equal(result.action, 'choose')
    assert.equal(calls.length, 1)
    assert.equal(calls[0][0], 'launch-list')
  }
})

test('one standard study resolves automatically; explicit selection bypasses discovery', async () => {
  const calls = []
  const ctx = { studies: { async call(operation, payload, scope) {
    calls.push({ operation, payload, scope })
    return operation === 'launch-list' ? { studies: [{ study_id: 'route-study' }] } : profile()
  } } }
  assert.equal((await prepareLaunch(ctx, { action: 'start' }, new AbortController().signal)).action, 'start')
  assert.deepEqual(calls.map(value => value.operation), ['launch-list', 'launch-profile'])
  calls.length = 0
  await prepareLaunch(ctx, { action: 'start', target: 'route-study', config: 'configs/alternative.yaml' }, new AbortController().signal)
  assert.deepEqual(calls, [{ operation: 'launch-profile', payload: { config: 'configs/alternative.yaml' }, scope: { studyId: 'route-study' } }])
})

test('plan uses selected task budgets and all datasets, including unlimited calls and multiple final sets', () => {
  const value = profile()
  const start = launchPlan(value, 'start')
  assert.match(start.instructions, /researchers=3,rounds=4/)
  assert.match(start.instructions, /评测次数不限/)
  assert.match(start.instructions, /cities-a/)
  assert.match(start.instructions, /cities-b/)
  assert.doesNotMatch(start.instructions, /knapsack|holdout|背包|180秒|11次/)
  value.budgets.evaluation_calls = 30
  assert.match(launchPlan(value, 'start').instructions, /保留4次/)
  value.existing_runs = ['route-study/001']
  assert.equal(launchPlan(value, 'start').action, 'status')
  const final = launchPlan(value, 'final')
  assert.match(final.instructions, /test-north/)
  assert.match(final.instructions, /test-south/)
  assert.match(final.instructions, /App请求范围审批/)
  assert.match(final.instructions, /visualization_render/)
  assert.match(final.instructions, /visualization_deliver/)
  assert.match(final.instructions, /final_status/)
  value.final_permitted = false
  assert.match(launchPlan(value, 'final').instructions, /不测final/)
})
