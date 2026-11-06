import { runtimeDirectoryName } from './run-layout.js'
import { createHash, randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import { lstat, mkdir, open, realpath, rename, unlink } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import type { Json, JsonObject } from '../services.js'

export const FINAL_TOOLS = ['final_status','final_evidence','final_select','final_measure','final_write','final_deliver'] as const
export const VISUALIZATION_TOOLS = ['visualization_status','visualization_evidence','visualization_render','visualization_deliver'] as const
export const CONTROL_TOOLS = ['next_context','next_progress','next_ask','next_complete','next_report_issue','next_subscribe','next_send_figure'] as const
export const MAIN_TOOLS = [
  'study_launch',
  'study_status', 'study_config', 'study_plan', 'study_check', 'study_init', 'study_deliver', 'study_plan_resources',
  'study_seal', 'study_audit', 'batch_init', 'session_begin', 'session_end', 'study_recover',
  'research_start', 'baseline_measure', 'study_write', 'study_module',
  'study_doctor', 'study_evidence', 'study_selection_report_context','study_selection_report_submit','study_selection_context', 'study_selection_submit', ...FINAL_TOOLS, ...VISUALIZATION_TOOLS,
] as const
export const SOLVER_TOOLS = [
  'solver_context', 'solver_evaluate', 'solver_result', 'solver_findings', 'solver_share', 'solver_reuse',
  'solver_checkpoint', 'solver_iteration', 'solver_read', 'solver_write', 'solver_list',
] as const

export function isObject(value: Json | undefined): value is JsonObject {
  return value !== null && value !== undefined && typeof value === 'object' && !Array.isArray(value)
}

/** Key order cannot alter the scope of a persisted authorization. */
export function canonical(value: Json): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (isObject(value)) return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key]!)).join(',') + '}'
  return JSON.stringify(value)
}

export function authorizationScope(runId: string, status: JsonObject, config: JsonObject): JsonObject {
  if (!isObject(status.selection)) throw new Error('Freeze the selected candidate before final authorization')
  if (!isObject(config.final) || config.final.authorized !== true) throw new Error('This run configuration does not permit final evaluation')
  if (!Array.isArray(status.final_datasets) || status.final_datasets.length === 0 || status.final_datasets.some(dataset => !isObject(dataset) || typeof dataset.dataset_id !== 'string' || typeof dataset.manifest_sha !== 'string' || !isObject(dataset.files))) throw new Error('Final authorization requires verified sealed final datasets')
  return {
    action: 'final-evaluation', run_id: runId, selection: status.selection,
    config_sha: createHash('sha256').update(canonical(config)).digest('hex'),
    datasets: status.final_datasets,
  }
}

export function scopeDigest(scope: JsonObject): string { return createHash('sha256').update(canonical(scope)).digest('hex') }

async function authorizationPath(root: string, runId: string): Promise<string> {
  if (!/^[a-z][a-z0-9-]{0,63}\/[0-9]{3,}$/.test(runId)) throw new Error('Invalid run identifier')
  let current = await realpath(root)
  for (const part of ['runs', ...runId.split('/'), runtimeDirectoryName(root,runId)]) {
    current = join(current, part)
    try { await mkdir(current, { mode: 0o700 }) } catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error }
    const info = await lstat(current)
    if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('Authorization directory is redirected')
  }
  return join(current, 'authorization.json')
}

export async function readAuthorization(root: string, runId: string, digest: string): Promise<boolean> {
  const path = await authorizationPath(root, runId)
  let file
  try { file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK) }
  catch (error) { if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return false; throw error }
  try {
    const info = await file.stat()
    if (!info.isFile() || info.nlink !== 1 || info.size > 1024 * 1024) throw new Error('Invalid authorization record')
    const value: unknown = JSON.parse(await file.readFile('utf8'))
    if (!value || typeof value !== 'object' || !('schema_version' in value) || value.schema_version !== 1 || !('digest' in value) || typeof value.digest !== 'string' || !('approved_by' in value) || typeof value.approved_by !== 'string' || !('scope' in value)) throw new Error('Invalid authorization record')
    const scope = value.scope as Json
    if (!isObject(scope) || scopeDigest(scope) !== value.digest) throw new Error('Authorization scope checksum differs')
    return value.digest === digest
  } finally { await file.close() }
}

export async function saveAuthorization(root: string, runId: string, scope: JsonObject, agentId: string): Promise<void> {
  const path = await authorizationPath(root, runId)
  try {
    const info = await lstat(path)
    if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1) throw new Error('Invalid authorization destination')
  } catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error }
  const temporary = join(dirname(path), '.authorization-' + randomUUID())
  const file = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600)
  try {
    await file.writeFile(JSON.stringify({ schema_version: 1, digest: scopeDigest(scope), scope, approved_by: agentId, approved_at: new Date().toISOString() }) + '\n')
    await file.sync()
  } finally { await file.close() }
  try {
    await rename(temporary, path)
    const directory = await open(dirname(path), constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW)
    try { await directory.sync() } finally { await directory.close() }
  }
  finally { try { await unlink(temporary) } catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error } }
}
