import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, readFile, symlink, link, mkdir, rm, realpath } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { DockerWorkspaces, WORKSPACE_PROGRAM, prepareWorkspace, workspacePath, runCommand } from '../lib/core/workspace-docker.js'

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'anysolvers-files-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  return root
}
function fileOperation(root, operation, path, content, max = 128) {
  const result = spawnSync('python3', ['-I', '-c', WORKSPACE_PROGRAM, root, String(max)], {
    input: JSON.stringify({ operation, path, ...content === undefined ? {} : { content } }), encoding: 'utf8',
  })
  assert.equal(result.signal, null)
  return { exit: result.status, ...JSON.parse(result.stdout) }
}

test('the trusted file program edits UTF-8 text and lists only the selected directory', async t => {
  const root = await fixture(t)
  assert.equal(fileOperation(root, 'write', 'code/solver.py', '答案 = 42\n').ok, true)
  assert.equal(fileOperation(root, 'read', 'code/solver.py').value.content, '答案 = 42\n')
  assert.deepEqual(fileOperation(root, 'list', 'code').value.paths, ['solver.py'])
  assert.deepEqual(fileOperation(root, 'list', '.').value.paths, ['code'])
  assert.equal(fileOperation(root, 'execute', 'code/solver.py').ok, false)
})

test('provider output retains UTF-8 characters split across process chunks', async () => {
  const result = await runCommand('python3', ['-I', '-c', 'import os,time; os.write(1,bytes([228])); time.sleep(0.02); os.write(1,bytes([184,173]))'])
  assert.equal(result.code, 0)
  assert.equal(result.stdout, '中')
})

test('absolute paths, parent traversal, symlinks and hardlinks cannot expose other files', async t => {
  const root = await fixture(t)
  const outside = await fixture(t)
  await writeFile(join(outside, 'secret'), 'private')
  await symlink(join(outside, 'secret'), join(root, 'link'))
  await symlink(outside, join(root, 'directory'))
  await link(join(outside, 'secret'), join(root, 'hardlink'))
  for (const path of ['../secret', join(outside, 'secret'), 'link', 'directory/secret', 'hardlink', 'a\\b', 'a\u0000b']) {
    assert.equal(fileOperation(root, 'read', path).ok, false, path)
    assert.equal(fileOperation(root, 'write', path, 'changed').ok, false, path)
  }
  assert.equal(await readFile(join(outside, 'secret'), 'utf8'), 'private')
})

test('file limits count bytes and reject nonregular files', async t => {
  const root = await fixture(t)
  assert.equal(fileOperation(root, 'write', 'large', '中'.repeat(20), 59).ok, false)
  await writeFile(join(root, 'large'), 'a'.repeat(129))
  assert.equal(fileOperation(root, 'read', 'large').ok, false)
  await mkdir(join(root, 'directory'))
  assert.equal(fileOperation(root, 'read', 'directory').ok, false)
})

test('host workspace construction rejects redirected ancestors and malformed identities', async t => {
  const root = await fixture(t)
  const outside = await fixture(t)
  await symlink(outside, join(root, 'runs'))
  await assert.rejects(prepareWorkspace(root, 'study/001', 'r1-r001'), /real directory/)
  await assert.rejects(prepareWorkspace(outside, '../bad', 'r1-r001'), /identifier/)
})

test('relative workspace storage stays under its project without path-based namespace changes', async t => {
  const root=await realpath(await fixture(t))
  const path=await prepareWorkspace(root,'study/001','r1-r001','.workspaces')
  assert.equal(path,join(root,'.workspaces/runs/study/001/dsh/workspaces/r1-r001/work'))
  await assert.rejects(prepareWorkspace(root,'study/001','r1-r001','../escape'),/storage/)
})

test('trusted external workspace roots remain isolated by project and reject links', async t => {
  const base = await realpath(await mkdtemp(join(tmpdir(),'anysolvers-storage-')))
  const first = await fixture(t), second = await fixture(t)
  const previous = process.env.ANYSOLVERS_DSH_WORKSPACES
  process.env.ANYSOLVERS_DSH_WORKSPACES = base
  t.after(async () => {
    if (previous === undefined) delete process.env.ANYSOLVERS_DSH_WORKSPACES
    else process.env.ANYSOLVERS_DSH_WORKSPACES = previous
    await rm(base, { recursive: true, force: true })
  })
  const work = await prepareWorkspace(first, 'study/001', 'r1-r001')
  assert.ok(work.startsWith(base + '/'))
  assert.notEqual(workspacePath(first, 'study/001', 'r1-r001'), workspacePath(second, 'study/001', 'r1-r001'))
  await rm(join(work, '..'), { recursive: true, force: true })
  await symlink(second, join(work, '..'))
  await assert.rejects(prepareWorkspace(first, 'study/001', 'r1-r001'), /real directory/)
})

test('Docker workspaces use a fixed file-only command and reject unknown or stopped containers', async t => {
  const root = await fixture(t)
  const calls = []
  let record
  const image = 'sha256:' + 'a'.repeat(64)
  const run = async (_command, args) => {
    calls.push([...args])
    if (args[0] === 'inspect') return record ? { code: 0, stdout: JSON.stringify([record]), stderr: '' } : { code: 1, stdout: '', stderr: 'Error: No such object' }
    if (args[0] === 'run') {
      const labels = Object.fromEntries(args.flatMap((arg, i) => arg === '--label' ? [[args[i + 1].split('=')[0], args[i + 1].split('=').slice(1).join('=')]] : []))
      const mount = args[args.indexOf('--mount') + 1]
      record = { Id: 'container-1', Config: { Image: image, Labels: labels, User: args[args.indexOf('--user') + 1], Entrypoint: ['python3'], Cmd: ['-I', '-c', 'import time; time.sleep(2147483647)'] }, State: { Running: true }, Mounts: [{ Type: 'bind', Source: mount.match(/source=(.*),target=/)[1], Destination: '/work', RW: true }], HostConfig: { NetworkMode: 'none', ReadonlyRootfs: true, Privileged: false, CapDrop: ['ALL'], CapAdd: null, SecurityOpt: ['no-new-privileges'], Memory: 128 * 1024 * 1024, PidsLimit: 32, NanoCpus: 1e9, Devices: [], DeviceRequests: null } }
      return { code: 0, stdout: 'container-1', stderr: '' }
    }
    if (args[0] === 'rm') { record = undefined; return { code: 0, stdout: '', stderr: '' } }
    throw new Error('unexpected command')
  }
  const backend = new DockerWorkspaces({ root, image, docker: 'docker', python: 'python3', maxFileBytes: 128, memoryMb: 128, pids: 32, cpus: 1 }, run)
  const work = await backend.prepare('study/001', 'r1-r001')
  await writeFile(join(work, 'solver.py'), 'def solve(case): return {}')
  const creation = calls.find(args => args[0] === 'run')
  for (const flag of ['--pull=never', '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges']) assert.ok(creation.includes(flag), flag)
  assert.equal(creation.filter(arg => arg === '--mount').length, 1)
  assert.ok(!creation.some(arg => arg.includes('docker.sock')))
  assert.equal(await backend.prepare('study/001', 'r1-r001'), work)
  assert.equal(calls.filter(args => args[0] === 'run').length, 1)
  record.HostConfig.NetworkMode = 'host'
  await assert.rejects(backend.prepare('study/001', 'r1-r001'), /isolation/)
  record.HostConfig.NetworkMode = 'none'
  record.State.Running = false
  await assert.rejects(backend.prepare('study/001', 'r1-r001'), /reconciliation/)
  record.Config.Labels['org.onefav.anysolvers.owner'] = 'another-owner'
  await assert.rejects(backend.release('study/001', 'r1-r001'), /owner/)
  assert.equal(calls.filter(args => args[0] === 'rm').length, 0)
  record = undefined
  await backend.release('study/001', 'r1-r001')
  assert.equal(await readFile(join(work, 'solver.py'), 'utf8'), 'def solve(case): return {}')
})

test('explicit desktop workspace configuration works without a shell export', async t => {
  const base = await realpath(await mkdtemp(join(tmpdir(),'anysolvers-desktop-storage-')))
  const root = await fixture(t)
  t.after(() => rm(base, { recursive: true, force: true }))
  const work = await prepareWorkspace(root, 'study/001', 'r1-r001', base)
  assert.ok(work.startsWith(base + '/'))
  assert.equal(work, workspacePath(await (await import('node:fs/promises')).realpath(root), 'study/001', 'r1-r001', base))
})
