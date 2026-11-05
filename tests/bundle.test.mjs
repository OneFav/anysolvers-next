import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { parse } from 'yaml'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'

test('shipped complete bundle remains unbound without a legacy root and mounts both usable presets', { timeout: 15000 }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'anysolvers-bundle-'))
  await mkdir(join(root, 'tools'))
  await writeFile(join(root, 'tools/dsh_bridge.py'), '# only existence is checked; no process starts\n')
  const values = { 'process.env.ANYSOLVERS_ROOT || undefined': undefined, 'process.env.ANYSOLVERS_PYTHON': process.execPath,
    'process.env.ANYSOLVERS_WORKSPACE_IMAGE': 'sha256:' + 'a'.repeat(64), 'process.env.ANYSOLVERS_PROVIDER': 'test-only' }
  const patch = parse(await readFile(new URL('../cordis.patch.yml', import.meta.url), 'utf8'), { customTags: [{
    tag: 'tag:yaml.org,2002:js', resolve: expression => {
      assert.ok(Object.hasOwn(values, expression), 'Unknown configuration expression')
      return values[expression]
    },
  }] })
  const ctx = new Context().extend({ baseUrl: new URL('../', import.meta.url).href })
  ctx.provide('approval', { request: () => { throw new Error('No approvals during bundle test') } })
  ctx.provide('llm', { resolveCallConfig: () => { throw new Error('No model route during bundle test') } })
  const loader = ctx.plugin(Loader, { baseUrl: new URL('../', import.meta.url).href })
  await loader
  t.after(async () => { await loader.dispose(); await rm(root, { recursive: true, force: true }) })
  await ctx.loader.root.update([
    { id: 'sessions', name: '@deepseek-ai/dsh-session' },
    { id: 'agents', name: '@deepseek-ai/dsh-agent' },
    { id: 'projection', name: '@deepseek-ai/dsh-session-projection' },
    { id: 'prompt', name: '@deepseek-ai/dsh-system-prompt' },
    { id: 'tools', name: '@deepseek-ai/dsh-tools', config: { mode: 'native' } },
    { id: 'presets', name: '@deepseek-ai/dsh-agent-preset-registry', config: { default: 'anysolvers-main' } },
    ...patch.flatMap(layer => layer.insert).map(entry=>entry.id==='anysolvers-runtime'?{...entry,config:{...entry.config,stateRoot:join(root,'cache')}}:entry),
  ])
  await ctx.loader.await()
  for (const name of ['studies', 'studySessions', 'studySandbox', 'studyPolicy']) assert.ok(ctx[name], name)
  assert.deepEqual((await ctx.agentPresets.list()).map(p => ({ id: p.id, broken: p.broken })), [
    { id: 'anysolvers-main', broken: undefined }, { id: 'anysolvers-researcher', broken: undefined },
  ])
  assert.throws(()=>ctx.studies.root,/工作区/)
  assert.equal(ctx.studies.forRoot(root).root,root)
  assert.ok(ctx.studySandbox.forRoot(root))
  assert.equal(ctx.tools.schemas().length, 0, 'Role tools must not be registered globally')
})
