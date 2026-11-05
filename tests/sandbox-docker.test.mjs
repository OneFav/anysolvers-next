import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, symlink, link, writeFile, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { DockerWorkspaces } from '../lib/core/workspace-docker.js'

// Explicit opt-in: this test starts only the fixed file service, never a candidate or model.
test('real Docker workspace confines file tools and preserves work after release', {
  skip: !process.env.ANYSOLVERS_DSH_DOCKER_IMAGE,
}, async t => {
  const root = await mkdtemp('/private/tmp/anysolvers-dsh-files-smoke-')
  const backend = new DockerWorkspaces({ root, image: process.env.ANYSOLVERS_DSH_DOCKER_IMAGE, docker: 'docker', python: 'python3', maxFileBytes: 128, memoryMb: 128, pids: 32, cpus: 1 })
  t.after(async () => { await backend.dispose(); await rm(root, { recursive: true, force: true }) })
  const work = await backend.prepare('smoke/001', 'r1-r001')
  const binding = { runId: 'smoke/001', sessionId: 'r1-r001', workspace: work, status: 'active', actor: 'r1', nativeId: 'smoke-only', generation: 1 }
  await backend.write(binding, 'draft/solver.py', '# file-provider smoke only\n')
  assert.equal(await backend.read(binding, 'draft/solver.py'), '# file-provider smoke only\n')
  assert.deepEqual(await backend.list(binding, 'draft'), ['solver.py'])
  await writeFile(join(root, 'outside'), 'outside-workspace')
  await symlink(join(root, 'outside'), join(work, 'symlink'))
  await link(join(root, 'outside'), join(work, 'hardlink'))
  for (const path of ['/etc/passwd', '../authorization.json', 'symlink', 'hardlink']) {
    await assert.rejects(backend.read(binding, path))
    await assert.rejects(backend.write(binding, path, 'modified'))
  }
  await assert.rejects(backend.write(binding, 'large', 'a'.repeat(129)))
  assert.equal(await readFile(join(root, 'outside'), 'utf8'), 'outside-workspace')
  await backend.release('smoke/001', 'r1-r001')
  assert.equal(await readFile(join(work, 'draft/solver.py'), 'utf8'), '# file-provider smoke only\n')
  await assert.rejects(backend.read(binding, 'draft/solver.py'), /unavailable/)
})
