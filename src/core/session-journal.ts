import {atomic,read,readExecutionJournal,saveExecutionJournal} from '@onefav/research-runtime'
import { runtimeDirectoryName } from './run-layout.js'
import { constants } from 'node:fs'
import { lstat, mkdir, open, rename, unlink } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'

export type LaunchPhase = 'beginning' | 'preparing' | 'creating' | 'activating' | 'dispatching'
  | 'active' | 'closing' | 'completed' | 'failed' | 'reconciliation'

/** Controller facts only; Python owns research, budget and evaluation state. */
export interface LaunchRecord {
  attemptId: string
  runId: string
  actor: string
  parentNativeId: string
  nativeId: string
  sessionId: string | null
  workspace: string | null
  generation: number | null
  idea: string | null
  iteration: string | null
  nextIteration: string | null
  phase: LaunchPhase
  createdAt: string
  updatedAt: string
  issue: string | null
}

export interface RunPlan {
  parentNativeId: string
  assignments: { actor: string; idea: string }[]
  status: 'running' | 'completed' | 'reconciliation'
}

export interface ControllerJournal {
  schemaVersion: 1
  runId: string
  revision: number
  launches: LaunchRecord[]
  plan: RunPlan | null
}

const phases = new Set<LaunchPhase>(['beginning', 'preparing', 'creating', 'activating', 'dispatching',
  'active', 'closing', 'completed', 'failed', 'reconciliation'])

export function assertRunId(value: string): void {
  if (!/^[a-z][a-z0-9-]{0,63}\/(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$/.test(value)) throw new Error('Invalid run identifier')
}

export function assertActor(value: string): void {
  if (!/^[a-z][a-z0-9-]{0,63}$/.test(value)) throw new Error('Invalid actor or idea identifier')
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

/** Reject corrupt or future durable records instead of guessing a recovery action. */
export function decodeJournal(raw: unknown, runId: string): ControllerJournal {
  if (!object(raw) || raw.schemaVersion !== 1 || raw.runId !== runId
    || !Number.isSafeInteger(raw.revision) || Number(raw.revision) < 0 || !Array.isArray(raw.launches)) {
    throw new Error('Unsupported or invalid controller journal')
  }
  const nativeIds = new Set<string>()
  const launches = raw.launches.map((entry): LaunchRecord => {
    if (!object(entry) || entry.runId !== runId || typeof entry.attemptId !== 'string'
      || typeof entry.actor !== 'string' || typeof entry.nativeId !== 'string'
      || typeof entry.parentNativeId !== 'string' || typeof entry.createdAt !== 'string'
      || typeof entry.updatedAt !== 'string' || typeof entry.phase !== 'string'
      || !phases.has(entry.phase as LaunchPhase) || !nullableString(entry.sessionId)
      || !nullableString(entry.workspace) || !nullableString(entry.idea)
      || !nullableString(entry.iteration) || !nullableString(entry.nextIteration)
      || !nullableString(entry.issue) || (entry.generation !== null
        && (!Number.isSafeInteger(entry.generation) || Number(entry.generation) < 1))) {
      throw new Error('Invalid launch record')
    }
    assertActor(entry.actor)
    if (nativeIds.has(entry.nativeId)) throw new Error('Duplicate native identity in journal')
    nativeIds.add(entry.nativeId)
    return {
      attemptId: entry.attemptId, runId, actor: entry.actor, nativeId: entry.nativeId,
      parentNativeId: entry.parentNativeId, sessionId: entry.sessionId, workspace: entry.workspace,
      generation: entry.generation === null ? null : Number(entry.generation),
      idea: entry.idea, iteration: entry.iteration, nextIteration: entry.nextIteration,
      phase: entry.phase as LaunchPhase, createdAt: entry.createdAt, updatedAt: entry.updatedAt,
      issue: entry.issue,
    }
  })
  let plan: RunPlan | null = null
  if (raw.plan !== null) {
    const candidate = raw.plan
    if (!object(candidate) || typeof candidate.parentNativeId !== 'string'
      || !['running', 'completed', 'reconciliation'].includes(String(candidate.status))
      || !Array.isArray(candidate.assignments)) throw new Error('Invalid run plan')
    const assignments = candidate.assignments.map((assignment) => {
      if (!object(assignment) || typeof assignment.actor !== 'string' || typeof assignment.idea !== 'string') {
        throw new Error('Invalid assignment')
      }
      assertActor(assignment.actor)
      assertActor(assignment.idea)
      return { actor: assignment.actor, idea: assignment.idea }
    })
    plan = { parentNativeId: candidate.parentNativeId, assignments,
      status: candidate.status as RunPlan['status'] }
  }
  return { schemaVersion: 1, runId, revision: Number(raw.revision), launches, plan }
}

/** Short exclusive writes; stale locks intentionally require reconciliation. */
export class SessionJournal {
  private readonly queues = new Map<string, Promise<void>>()

  constructor(private readonly root: string) {}

  private async directory(runId: string): Promise<string> {
    assertRunId(runId)
    let directory = resolve(this.root)
    const runtime=runtimeDirectoryName(this.root,runId)
    for (const component of ['runs', ...runId.split('/'), runtime]) {
      directory = join(directory, component)
      if (component === runtime) await mkdir(directory, { recursive: true, mode: 0o700 })
      const stat = await lstat(directory)
      if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Controller path is not a private directory')
    }
    return directory
  }

  async read(runId: string): Promise<ControllerJournal> {
    const directory = await this.directory(runId)
    const split = await readExecutionJournal(directory)
    if (split?.schemaVersion === 2) {
      if(split.runId!==runId||split.methodRef!=='methods/anysolvers/sessions.json')throw new Error('Execution journal identity mismatch')
      const method=await read<any>(join(directory,split.methodRef))
      if(!method||method.revision!==split.revision)throw new Error('Interrupted journal transaction requires reconciliation')
      const launches=split.launches.map(record=>{
        const state=method.launches.find((item:any)=>item.attemptId===record.attemptId)
        if(!state)throw new Error('Missing method session state')
        return {...record,...state}
      })
      return decodeJournal({schemaVersion:1,runId,revision:split.revision,launches,plan:method.plan},runId)
    }
    let handle
    try {
      handle = await open(join(directory, 'controller.json'), constants.O_RDONLY | constants.O_NOFOLLOW)
    } catch (error) {
      if (error instanceof Error && 'code' in error && error.code === 'ENOENT') {
        return { schemaVersion: 1, runId, revision: 0, launches: [], plan: null }
      }
      throw error
    }
    try {
      const stat = await handle.stat()
      // A concurrent atomic replacement can unlink this already-open snapshot.
      if (!stat.isFile() || stat.nlink > 1 || stat.size > 8 * 1024 * 1024) throw new Error('Invalid controller journal file')
      return decodeJournal(JSON.parse(await handle.readFile('utf8')), runId)
    } finally {
      await handle.close()
    }
  }

  async transaction<T>(runId: string, action: (journal: ControllerJournal, save: () => Promise<void>) => Promise<T>): Promise<T> {
    const previous = this.queues.get(runId) ?? Promise.resolve()
    let release!: () => void
    const current = new Promise<void>((done) => { release = done })
    this.queues.set(runId, current)
    await previous
    try {
      const directory = await this.directory(runId)
      const lockPath = join(directory, 'controller.lock')
      const lock = await open(lockPath, 'wx', 0o600).catch((error: unknown) => {
        if (error instanceof Error && 'code' in error && error.code === 'EEXIST') {
          throw new Error('Controller is locked; reconcile its owner before retrying')
        }
        throw error
      })
      try {
        await lock.writeFile(JSON.stringify({ pid: process.pid, owner: randomUUID() }))
        const journal = await this.read(runId)
        const save = async (): Promise<void> => {
          journal.revision += 1
          if(await read(join(directory,'implementation.lock.json'))){
            const methodRef='methods/anysolvers/sessions.json'
            // Method first, execution commit second; a crash between files is rejected on read.
            await atomic(join(directory,methodRef),{schemaVersion:1,revision:journal.revision,plan:journal.plan,
              launches:journal.launches.map(({attemptId,actor,generation,idea,iteration,nextIteration})=>({attemptId,actor,generation,idea,iteration,nextIteration}))})
            await saveExecutionJournal(directory,{schemaVersion:2,runId,revision:journal.revision,methodRef,
              launches:journal.launches.map(({actor,generation,idea,iteration,nextIteration,...record})=>record)})
            return
          }
          const temporary = join(directory, `.controller-${randomUUID()}.json`)
          const file = await open(temporary, 'wx', 0o600)
          try {
            await file.writeFile(JSON.stringify(journal, null, 2) + '\n')
            await file.sync()
          } finally {
            await file.close()
          }
          await rename(temporary, join(directory, 'controller.json'))
          const dir = await open(directory, 'r')
          try { await dir.sync() } finally { await dir.close() }
        }
        return await action(journal, save)
      } finally {
        await lock.close()
        await unlink(lockPath)
      }
    } finally {
      release()
      if (this.queues.get(runId) === current) this.queues.delete(runId)
    }
  }
}
