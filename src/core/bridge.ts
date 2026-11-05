import {setTimeout as delay} from 'node:timers/promises'
import {fileURLToPath} from 'node:url'
import {dirname,join} from 'node:path'
import {BoundedProcess,ProcessError} from '@onefav/research-runtime'
import { resolve } from 'node:path'
import { existsSync } from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import { directoryPath, executablePath, implementationRoot } from './config-paths.js'
import type { JsonObject, Scope } from '../services.js'

export const OPERATIONS = new Set([
  'runtime-wait', 'stage-validate',
  'as-preflight', 'as-start', 'as-configuration', 'as-save', 'as-consent',
  'status', 'config', 'check', 'module', 'study-init', 'study-check', 'study-deliver',
  'study-seal', 'study-plan-resources', 'batch-init', 'begin-session', 'activate-session',
  'end-session', 'audit', 'select', 'measure', 'research', 'prepare-write',
  'candidate-import', 'research-record', 'reuse-export', 'artifact-write', 'artifact-deliver',
  'research-materials', 'research-development', 'evidence', 'launch-list', 'launch-profile', 'doctor',
  'terminal-status', 'terminal-evidence', 'terminal-report-write', 'terminal-report-deliver', 'terminal-artifact',
  'visualization-render', 'visualization-deliver',
  'ui-configuration', 'ui-save', 'ui-progress', 'ui-launch-profile', 'ui-claim',
])

export {ProcessError as BridgeError} from '@onefav/research-runtime'
const BridgeError=ProcessError
// A sealed monorepo carries its own Python runtime. Installed split packages
// use dependency resolution; neither mode follows the caller's cwd.
const bundledPython = join(implementationRoot,'packages/research-runtime/python')
const pythonRuntime = existsSync(join(bundledPython,'research_runtime/__init__.py')) ? bundledPython
  : join(dirname(fileURLToPath(import.meta.resolve('@onefav/research-runtime/package.json'))),'python')

export interface BridgeConfig {
  pathBase?: string
  runtime?: import('./runtime-store.js').RuntimeDispatch
  adapterRoot?: string
  root: string
  python: string
  workspaceRoot?: string
  maxOutputBytes: number
  maxInputBytes: number
}

/** Fixed executable and operation list; no model-supplied argv or shell command. */
export class PythonBridge {
  private readonly process = new BoundedProcess()
  constructor(readonly config: BridgeConfig) {
    const base = directoryPath(config.pathBase ?? '.', implementationRoot)
    config.root = directoryPath(config.root, base)
    config.python = executablePath(config.python, base)
  }

  async validate(): Promise<void> {
    if (this.config.workspaceRoot && (directoryPath(this.config.workspaceRoot,this.config.root) === '/' || this.config.workspaceRoot.includes(','))) {
      throw new Error('Workspace storage root must be a private directory')
    }
    await realpath(this.config.root)
    const script = await stat(this.config.runtime?.script ?? resolve(this.config.root, 'tools/dsh_bridge.py'))
    if (!script.isFile()) throw new Error('AnySolvers tools/dsh_bridge.py is missing')
    await stat(this.config.python)
  }

  async close():Promise<void>{await this.process.close()}

  async call(operation: string, payload: JsonObject = {}, scope: Scope = {}, signal?: AbortSignal): Promise<JsonObject> {
    if (!OPERATIONS.has(operation)) throw new BridgeError('OPERATION', 'Unsupported research operation')
    // These operations acquire the metadata lock before changing any backend state.
    // Only an explicit lock rejection may be retried; evaluations/unknown outcomes never are.
    for(let retry=0;;retry++){
      try{return await this.callOnce(operation,payload,scope,signal)}catch(error){
        if(!(error instanceof BridgeError) || error.message!=='another evaluation or file operation is active'
          || !['begin-session','activate-session','end-session','config','check'].includes(operation) || retry>=20)throw error
        await delay(50,undefined,signal?{signal}:undefined)
      }
    }
  }

  private async callOnce(operation:string,payload:JsonObject,scope:Scope,signal?:AbortSignal):Promise<JsonObject>{
    signal?.throwIfAborted()
    const input = JSON.stringify({ schema_version: 1, operation, payload,
      ...(scope.runId ? { run_id: scope.runId } : {}),
      ...(scope.studyId ? { study_id: scope.studyId } : {}),
      ...(scope.sessionId ? { session_id: scope.sessionId } : {}),
    })
    if (Buffer.byteLength(input) > this.config.maxInputBytes) throw new BridgeError('INPUT_LIMIT', 'Research request is too large')
      const entry = operation.startsWith('terminal-') || operation.startsWith('visualization-') ? 'dsh/terminal_bridge.py' : operation.startsWith('ui-') ? 'dsh/ui_bridge.py' : operation === 'launch-list' || operation === 'launch-profile' ? 'tools/dsh_launch.py' : 'tools/dsh_bridge.py'
      const args = this.config.runtime ? ['-B', this.config.runtime.script, this.config.root, this.config.runtime.store, this.config.runtime.engineId] : this.config.adapterRoot ? ['-B', resolve(this.config.adapterRoot, 'dsh/project_bridge.py'), this.config.root] : ['-B', resolve(this.config.root, entry)]
      const {code,stdout} = await this.process.execute({executable:this.config.python,args,cwd:this.config.root,env:{ PATH: process.env.PATH ?? '/usr/bin:/bin', HOME: process.env.HOME ?? this.config.root,
          LANG: 'en_US.UTF-8', PYTHONDONTWRITEBYTECODE: '1',
          ANYSOLVERS_NATIVE_CONFINE:fileURLToPath(new URL('./native-confine.js',import.meta.url)), ANYSOLVERS_NODE:process.execPath,
          PYTHONPATH:pythonRuntime,
          ...((this.config.workspaceRoot ?? process.env.ANYSOLVERS_DSH_WORKSPACES) ? {
            ANYSOLVERS_DSH_WORKSPACES: (this.config.workspaceRoot ?? process.env.ANYSOLVERS_DSH_WORKSPACES)!,
          } : {}) },input,maxInputBytes:this.config.maxInputBytes,maxOutputBytes:this.config.maxOutputBytes,...(signal?{signal}:{})})
      try {
          const reply: unknown = JSON.parse(stdout)
          if (!isObject(reply) || reply.schema_version !== 1 || typeof reply.ok !== 'boolean') {
            throw new BridgeError('PROTOCOL', 'Unsupported research bridge response')
          }
          if (!reply.ok) {
            const error = isObject(reply.error) ? reply.error : {}
            throw new BridgeError(typeof error.code === 'string' ? error.code : 'RESEARCH_ERROR',
              typeof error.message === 'string' ? error.message : 'Research operation failed')
          }
          if (code !== 0 || !isObject(reply.value)) throw new BridgeError('PROTOCOL', 'Research bridge did not return a successful object')
          return reply.value as JsonObject
        } catch (error) {
          throw error instanceof BridgeError ? error : new BridgeError('PROTOCOL', 'Research response was incomplete; reconcile before retrying')
        }

  }
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
