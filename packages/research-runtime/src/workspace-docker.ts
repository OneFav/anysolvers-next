import { runtimeDirectoryName } from './run-layout.js'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { lstat, mkdir, realpath } from 'node:fs/promises'
import { join, resolve, isAbsolute, parse } from 'node:path'
import type { JsonObject } from './types.js'
export interface WorkspaceBinding {runId:string;sessionId:string;workspace:string;status:string}
type ResearchBinding=WorkspaceBinding

export interface CommandResult { code: number; stdout: string; stderr: string }
export type CommandRunner = (command: string, args: readonly string[], input?: string) => Promise<CommandResult>
export interface DockerWorkspaceOptions {
  root: string; image: string; docker: string; python: string;
  workspaceRoot?: string;
  maxFileBytes: number; memoryMb: number; pids: number; cpus: number;
}

/** Execute only trusted argv, bounding complete output and process lifetime. */
export const runCommand: CommandRunner = (command, args, input) => new Promise((done, fail) => {
  const child = spawn(command, [...args], { stdio: ['pipe', 'pipe', 'pipe'] })
  const stdout: Buffer[] = [], stderr: Buffer[] = []
  let bytes = 0
  let failure: Error | undefined
  const timer = setTimeout(() => { failure = new Error('Workspace provider command timed out'); child.kill('SIGKILL') }, 30_000)
  const collect = (target: 'stdout' | 'stderr', chunk: Buffer) => {
    bytes += chunk.length
    if (bytes > 8 * 1024 * 1024) { failure = new Error('Workspace provider output exceeded limit'); child.kill('SIGKILL'); return }
    if (target === 'stdout') stdout.push(chunk); else stderr.push(chunk)
  }
  child.stdout.on('data', (value: Buffer) => collect('stdout', value))
  child.stderr.on('data', (value: Buffer) => collect('stderr', value))
  child.once('error', error => { clearTimeout(timer); fail(error) })
  child.once('close', code => { clearTimeout(timer); if (failure) fail(failure); else done({ code: code ?? -1, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8') }) })
  child.stdin.on('error', () => { /* A failed provider may close input before exit is observed. */ })
  child.stdin.end(input ?? '')
})

/** Names are identifiers, never path fragments supplied to Docker. */
export function workspacePath(root: string, runId: string, sessionId: string, workspaceRoot?: string): string {
  if (!/^[a-z][a-z0-9-]{0,63}\/[0-9]{3,}$/.test(runId)) throw new Error('Invalid run identifier')
  if (!/^[a-z][a-z0-9-]{0,95}$/.test(sessionId)) throw new Error('Invalid research session identifier')
  return join(workspaceStorage(root, workspaceRoot), 'runs', runId, runtimeDirectoryName(root,runId), 'workspaces', sessionId, 'work')
}

function workspaceStorage(root: string, workspaceRoot?: string): string {
  const base = workspaceRoot
  if (!base) return resolve(root)
  if (!isAbsolute(base)) {
    if (base.split(/[/\\]/).includes('..') || resolve(root,base)===resolve(root) || base.includes(',')) throw new Error('Invalid trusted workspace storage root')
    return resolve(root,base)
  }
  if (resolve(base) === parse(base).root || base.includes(',')) throw new Error('Invalid trusted workspace storage root')
  return join(resolve(base), createHash('sha256').update(resolve(root)).digest('hex'))
}

/** Refuse redirected host ancestors before exposing a bind mount. */
export async function prepareWorkspace(root: string, runId: string, sessionId: string, workspaceRoot?: string): Promise<string> {
  const canonical = await realpath(root)
  const workspace = workspacePath(canonical, runId, sessionId, workspaceRoot)
  const storage = workspaceStorage(canonical, workspaceRoot)
  if (storage !== canonical) {
    let parent = parse(storage).root
    for (const part of storage.slice(parent.length).split('/')) {
      parent = join(parent, part)
      try { await mkdir(parent, { mode: 0o700 }) } catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error }
      const info = await lstat(parent)
      if (info.isSymbolicLink() || !info.isDirectory()) throw new Error('Workspace storage ancestor is redirected')
    }
    const info = await lstat(storage)
    if (process.getuid && info.uid !== process.getuid()) throw new Error('Workspace storage belongs to another user')
  }
  let current = storage
  for (const part of ['runs', ...runId.split('/'), runtimeDirectoryName(canonical,runId), 'workspaces', sessionId, 'work']) {
    current = join(current, part)
    try { await mkdir(current, { mode: 0o700 }) } catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error }
    const stat = await lstat(current)
    if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error('Workspace ancestor is not a real directory')
  }
  return workspace
}

/** Runs inside the container; user input is JSON data, never executable text. */
export const WORKSPACE_PROGRAM = String.raw`
import json, os, stat, sys, uuid
root, maximum = sys.argv[1], int(sys.argv[2])
rootfd = None
try:
    rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    request = json.loads(sys.stdin.buffer.read(maximum * 6 + 4096))
    operation = request.get('operation')
    path = request.get('path', '.')
    if not isinstance(path, str) or not path or chr(92) in path or chr(0) in path or path.startswith('/'):
        raise ValueError('invalid workspace path')
    if path == '.' and operation == 'list':
        parts = []
    else:
        parts = path.split('/')
        if any(part in ('', '.', '..') for part in parts):
            raise ValueError('invalid workspace path')
    directory = os.dup(rootfd)
    try:
        for part in parts[:-1] if parts else []:
            if operation == 'write':
                try: os.mkdir(part, 0o700, dir_fd=directory)
                except FileExistsError: pass
            nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = nextfd
        leaf = parts[-1] if parts else None
        if operation == 'read':
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
                    raise ValueError('workspace read requires a bounded regular file with one link')
                data = stream.read(maximum + 1)
                if len(data) > maximum: raise ValueError('file exceeds workspace limit')
            value = {'content': data.decode('utf-8')}
        elif operation == 'write':
            content = request.get('content')
            if not isinstance(content, str): raise ValueError('content must be text')
            data = content.encode('utf-8')
            if len(data) > maximum: raise ValueError('file exceeds workspace limit')
            try:
                info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('workspace write requires a regular file with one link')
            except FileNotFoundError: pass
            temporary = '.anysolvers-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, leaf, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try: os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError: pass
            value = {'written': len(data)}
        elif operation == 'list':
            if leaf is not None:
                nextfd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = nextfd
            names = sorted(os.listdir(directory))
            if len(names) > 1000: raise ValueError('directory exceeds workspace listing limit')
            value = {'paths': names}
        else:
            raise ValueError('unsupported workspace operation')
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
    finally:
        os.close(directory)
except (ValueError, OSError, UnicodeError, TypeError) as error:
    print(json.dumps({'ok': False, 'error': str(error)}))
    sys.exit(2)
finally:
    if rootfd is not None: os.close(rootfd)
`

const KEEPALIVE = 'import time; time.sleep(2147483647)'
type Inspection = {
  Id: string;
  Config: { Image: string; Labels: Record<string, string>; User: string; Entrypoint: string[]; Cmd: string[] };
  State: { Running: boolean };
  Mounts: { Type: string; Source: string; Destination: string; RW: boolean }[];
  HostConfig: {
    NetworkMode: string; ReadonlyRootfs: boolean; Privileged: boolean; CapDrop: string[]; CapAdd: string[] | null;
    SecurityOpt: string[]; Memory: number; PidsLimit: number; NanoCpus: number;
    Devices: unknown[]; DeviceRequests: unknown[] | null;
  };
}

function inspection(value: string): Inspection {
  const result: unknown = JSON.parse(value)
  if (!Array.isArray(result) || result.length !== 1 || !result[0] || typeof result[0] !== 'object') throw new Error('Invalid Docker inspection')
  const item = result[0] as Partial<Inspection>
  if (typeof item.Id !== 'string' || typeof item.Config?.Image !== 'string' || !item.Config.Labels || typeof item.State?.Running !== 'boolean' || !Array.isArray(item.Mounts) || !item.HostConfig) throw new Error('Invalid Docker inspection')
  return item as Inspection
}

/** Owns only labelled file-service containers; never runs candidate programs. */
export class DockerWorkspaces {
  private readonly owned = new Map<string, string>()
  private readonly pending = new Map<string, Promise<string>>()
  private readonly owner: string
  private readonly user = `${process.getuid?.() ?? 65534}:${process.getgid?.() ?? 65534}`
  constructor(private readonly options: DockerWorkspaceOptions, private readonly execute: CommandRunner = runCommand) {
    if (!/^(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}$/.test(options.image)) throw new Error('Workspace image must be pinned by sha256 digest')
    if (resolve(options.root).includes(',')) throw new Error('Workspace root cannot contain a Docker mount delimiter')
    if (!Number.isInteger(options.maxFileBytes) || options.maxFileBytes < 1 || options.maxFileBytes > 2 * 1024 * 1024) throw new Error('Invalid workspace file byte limit')
    if (!Number.isInteger(options.memoryMb) || options.memoryMb < 32 || options.memoryMb > 4096 || !Number.isInteger(options.pids) || options.pids < 4 || options.pids > 256 || !Number.isFinite(options.cpus) || options.cpus < 0.1 || options.cpus > 4) throw new Error('Invalid workspace resource limits')
    this.owner = createHash('sha256').update(resolve(options.root)).digest('hex')
  }
  private name(runId: string, sessionId: string): string {
    workspacePath(this.options.root, runId, sessionId, this.options.workspaceRoot)
    return 'anysolvers-files-' + createHash('sha256').update(`${this.owner}/${runId}/${sessionId}`).digest('hex').slice(0, 24)
  }
  private async inspect(name: string): Promise<Inspection | undefined> {
    const response = await this.execute(this.options.docker, ['inspect', '--type', 'container', name])
    if (response.code !== 0) {
      if (/No such (object|container)/i.test(response.stderr)) return undefined
      throw new Error('Docker container inspection failed')
    }
    return inspection(response.stdout)
  }
  private check(item: Inspection, runId: string, sessionId: string, workspace: string): void {
    const labels = item.Config.Labels
    const host = item.HostConfig
    if (labels['org.onefav.anysolvers.owner'] !== this.owner || labels['org.onefav.anysolvers.run'] !== runId || labels['org.onefav.anysolvers.session'] !== sessionId || item.Config.Image !== this.options.image || item.Mounts.length !== 1 || item.Mounts[0]?.Type !== 'bind' || item.Mounts[0]?.Source !== workspace || item.Mounts[0]?.Destination !== '/work' || !item.Mounts[0]?.RW) throw new Error('Existing workspace container does not match its recorded owner or configuration')
    if (item.Config.User !== this.user || JSON.stringify(item.Config.Entrypoint) !== JSON.stringify([this.options.python]) || JSON.stringify(item.Config.Cmd) !== JSON.stringify(['-I', '-c', KEEPALIVE]) || host.NetworkMode !== 'none' || host.ReadonlyRootfs !== true || host.Privileged !== false || host.CapDrop?.length !== 1 || host.CapDrop[0]?.toUpperCase() !== 'ALL' || (host.CapAdd?.length ?? 0) !== 0 || !host.SecurityOpt?.some(value => value === 'no-new-privileges' || value === 'no-new-privileges:true') || host.Memory !== this.options.memoryMb * 1024 * 1024 || host.PidsLimit !== this.options.pids || host.NanoCpus !== Math.round(this.options.cpus * 1e9) || (host.Devices?.length ?? 0) !== 0 || (host.DeviceRequests?.length ?? 0) !== 0) throw new Error('Existing workspace container has unsafe or changed isolation settings')
  }
  async prepare(runId: string, sessionId: string): Promise<string> {
    const key = `${runId}/${sessionId}`
    const pending = this.pending.get(key)
    if (pending) return pending
    const work = this.create(runId, sessionId)
    this.pending.set(key, work)
    try { return await work } finally { this.pending.delete(key) }
  }
  private async create(runId: string, sessionId: string): Promise<string> {
    const workspace = await prepareWorkspace(this.options.root, runId, sessionId, this.options.workspaceRoot)
    const name = this.name(runId, sessionId)
    const existing = await this.inspect(name)
    if (existing) {
      this.check(existing, runId, sessionId, workspace)
      if (!existing.State.Running) throw new Error('Stopped workspace container requires reconciliation before reuse')
      this.owned.set(`${runId}/${sessionId}`, name)
      return workspace
    }
    const result = await this.execute(this.options.docker, ['run', '--detach', '--pull=never', '--name', name,
      '--label', `org.onefav.anysolvers.owner=${this.owner}`, '--label', `org.onefav.anysolvers.run=${runId}`,
      '--label', `org.onefav.anysolvers.session=${sessionId}`, '--network=none', '--read-only', '--cap-drop=ALL',
      '--security-opt=no-new-privileges', '--pids-limit', String(this.options.pids), '--memory', `${this.options.memoryMb}m`,
      '--cpus', String(this.options.cpus), '--user', this.user, '--mount', `type=bind,source=${workspace},target=/work`, '--workdir', '/work',
      '--entrypoint', this.options.python, this.options.image, '-I', '-c', KEEPALIVE])
    if (result.code !== 0) throw new Error('Workspace container could not start; verify the pinned image and Python executable')
    const started = await this.inspect(name)
    if (!started) throw new Error('Workspace container disappeared after start')
    this.check(started, runId, sessionId, workspace)
    if (!started.State.Running) throw new Error('Workspace container stopped during startup; reconciliation required')
    this.owned.set(`${runId}/${sessionId}`, name)
    return workspace
  }
  private async operation(binding: ResearchBinding, operation: string, path: string, content?: string): Promise<JsonObject> {
    const expected = workspacePath(await realpath(this.options.root), binding.runId, binding.sessionId, this.options.workspaceRoot)
    if (binding.workspace !== expected || !['active', 'closing'].includes(binding.status)) throw new Error('Workspace is not bound to an active research session')
    const name = this.name(binding.runId, binding.sessionId)
    const item = await this.inspect(name)
    if (!item?.State.Running) throw new Error('Research workspace is unavailable; reconcile the session before continuing')
    this.check(item, binding.runId, binding.sessionId, expected)
    const result = await this.execute(this.options.docker, ['exec', '-i', name, this.options.python, '-I', '-c', WORKSPACE_PROGRAM, '/work', String(this.options.maxFileBytes)], JSON.stringify({ operation, path, ...content === undefined ? {} : { content } }))
    if (!result.stdout.trim()) throw new Error(`Workspace provider returned no JSON (exit ${result.code}); verify Docker permission to its private mount`)
    let response: unknown
    try { response = JSON.parse(result.stdout) } catch { throw new Error('Workspace provider returned an invalid JSON response') }
    if (!response || typeof response !== 'object' || !('ok' in response)) throw new Error('Invalid workspace provider response')
    if (response.ok !== true || result.code !== 0) throw new Error('Workspace operation rejected: ' + ('error' in response && typeof response.error === 'string' ? response.error : 'provider failure'))
    if (!('value' in response) || !response.value || typeof response.value !== 'object' || Array.isArray(response.value)) throw new Error('Invalid workspace provider value')
    return response.value as JsonObject
  }
  async read(binding: ResearchBinding, path: string): Promise<string> {
    const value = await this.operation(binding, 'read', path)
    if (typeof value.content !== 'string') throw new Error('Invalid workspace text response')
    return value.content
  }
  async write(binding: ResearchBinding, path: string, content: string): Promise<void> { await this.operation(binding, 'write', path, content) }
  async list(binding: ResearchBinding, path = '.'): Promise<string[]> {
    const value = await this.operation(binding, 'list', path)
    if (!Array.isArray(value.paths) || value.paths.some(item => typeof item !== 'string')) throw new Error('Invalid workspace list response')
    return value.paths as string[]
  }
  async release(runId: string, sessionId: string): Promise<void> {
    const name = this.name(runId, sessionId)
    const existing = await this.inspect(name)
    if (!existing) { this.owned.delete(`${runId}/${sessionId}`); return }
    this.check(existing, runId, sessionId, workspacePath(await realpath(this.options.root), runId, sessionId, this.options.workspaceRoot))
    const response = await this.execute(this.options.docker, ['rm', '--force', name])
    if (response.code !== 0) throw new Error('Workspace cleanup failed; retain session for reconciliation')
    this.owned.delete(`${runId}/${sessionId}`)
  }
  async dispose(): Promise<void> {
    await Promise.all([...this.pending.values()].map(value => value.catch(() => undefined)))
    const results = await Promise.allSettled([...this.owned.keys()].map(key => {
      const [study, batch, session] = key.split('/')
      return this.release(`${study}/${batch}`, session!)
    }))
    const failure = results.find(item => item.status === 'rejected')
    if (failure?.status === 'rejected') throw failure.reason
  }
  async doctor(): Promise<JsonObject> {
    const server = await this.execute(this.options.docker, ['version', '--format', '{{json .Server.Version}}'])
    if (server.code !== 0) return { ok: false, docker: false, image: false }
    const image = await this.execute(this.options.docker, ['image', 'inspect', this.options.image, '--format', '{{json .Id}}'])
    return { ok: image.code === 0, docker: true, image: image.code === 0, pinned_image: this.options.image }
  }
}
