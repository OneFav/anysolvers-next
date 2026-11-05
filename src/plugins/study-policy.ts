import {readFile} from 'node:fs/promises'
import { Context, Service } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-agent-preset-registry'
import type {} from '@deepseek-ai/dsh-user-approval'
import { MAIN_TOOLS, SOLVER_TOOLS, CONTROL_TOOLS, authorizationScope, isObject, readAuthorization, saveAuthorization, scopeDigest } from '../core/policy-rules.js'
import type { Role, Scope, StudyPolicy } from '../services.js'

interface Config { coordinatorPreset: string; researcherPreset: string }

/** Role registration is scoped; domain authorization also runs inside each tool. */
export default class StudyPolicyService extends Service implements StudyPolicy {
  static inject = ['studies', 'studySessions', 'tools', 'agents', 'agentPresets', 'approval']
  static Config = Schema.object({
    coordinatorPreset: Schema.string().default('anysolvers-main'),
    researcherPreset: Schema.string().default('anysolvers-researcher'),
  })
  private readonly installed = new Map<string, () => void>()
  private readonly approvals = new Map<string, Promise<void>>()
  constructor(ctx: Context, private readonly config: Config) {
    super(ctx, 'studyPolicy')
    ctx.on('agent/created', ({ agent }) => { this.install(agent); return undefined })
    ctx.on('agent/disposed', ({ agent }) => { this.installed.get(agent.id)?.(); this.installed.delete(agent.id) })
    for (const agent of ctx.agents.list()) this.install(agent)
    ctx.effect(() => () => { for (const release of this.installed.values()) release(); this.installed.clear() })
  }
  private role(agent: Agent): Role | undefined {
    const preset = this.ctx.agentPresets.composedPreset(agent.ctx)
    if (preset === this.config.researcherPreset) return 'researcher'
    if (preset === this.config.coordinatorPreset && agent.session.header.origin !== 'subagent') return 'coordinator'
    return undefined
  }
  private install(agent: Agent): void {
    const role = this.role(agent)
    if (!role || this.installed.has(agent.id)) return
    const allowed: readonly string[] = role === 'researcher' ? [...SOLVER_TOOLS,...(process.env.AS_NEXT_MANIFEST?['next_progress','next_framework_request']:[])] : [...MAIN_TOOLS,...(process.env.AS_NEXT_MANIFEST?CONTROL_TOOLS:[])]
    // Query by the public Agent key; scope helpers from a second SDK copy have a distinct store.
    const inherited = agent.ctx.tools.schemas(agent).map(tool => tool.name).filter(name => allowed.includes(name))
    const disposers = [agent.ctx.tools.presentAs('native'), agent.ctx.tools.restrict({ allow: inherited }), agent.ctx.tools.guard(exec => {
      if (!exec.agent) return 'AnySolvers tools require a calling agent'
      const current = this.role(exec.agent)
      if (current !== role) return 'AnySolvers role changed; create a new configured session'
      if (!allowed.includes(exec.name)) return 'This operation is outside the AnySolvers role'
      if (role === 'researcher') {
        const binding = this.ctx.studySessions.binding(exec.agent)
        if (!binding || binding.nativeId !== exec.agent.id || !['active', 'closing'].includes(binding.status)) return 'The researcher has no active trusted session binding'
      }
      return undefined
    })]
    this.installed.set(agent.id, () => { for (const dispose of disposers.reverse()) dispose() })
  }
  async authorize(agent: Agent, operation: string, scope: Scope = {}): Promise<void> {
    const role = this.role(agent)
    if (!role) throw new Error('Use an AnySolvers role preset for this operation')
    const allowed: readonly string[] = role === 'researcher' ? [...SOLVER_TOOLS,...(process.env.AS_NEXT_MANIFEST?['next_progress','next_framework_request']:[])] : [...MAIN_TOOLS,...(process.env.AS_NEXT_MANIFEST?CONTROL_TOOLS:[])]
    if (!allowed.includes(operation) && !(operation==='as_research' && role==='coordinator')) throw new Error('Operation is not available to this role')
    const project = await this.ctx.studies.projectBinding?.(agent)
    if(project && role==='coordinator') {
      if(['study_launch','batch_init','study_init','study_write','study_deliver','study_seal'].includes(operation))
        throw new Error('本会话已绑定 /as-research 运行；旧 /anysolvers 请在独立主会话使用。')
      if(scope.runId && scope.runId!==project.runId || scope.studyId && scope.studyId!==project.runId.split('/')[0])
        throw new Error('本会话仅可访问绑定项目的当前运行。')
    }
    if (role === 'researcher') {
      const binding = this.ctx.studySessions.binding(agent)
      if (!binding || binding.nativeId !== agent.id || !['active', 'closing'].includes(binding.status)) throw new Error('Researcher is not bound to an active session')
      if ((scope.runId !== undefined && scope.runId !== binding.runId) || (scope.sessionId !== undefined && scope.sessionId !== binding.sessionId) || (scope.studyId !== undefined && scope.studyId !== binding.runId.split('/')[0])) throw new Error('Researcher cannot address another run or session')
      if (operation === 'solver_evaluate' || operation === 'solver_write') {
        if (binding.status === 'closing') throw new Error('Research time is exhausted; only results and handover operations remain available')
        const context = await this.ctx.studies.call('research', { operation: 'context' }, { runId: binding.runId, sessionId: binding.sessionId })
        if (!isObject(context.research_time) || typeof context.research_time.exhausted !== 'boolean') throw new Error('Trusted research timing is unavailable')
        if (context.research_time.exhausted) throw new Error('Research time is exhausted; only results and handover operations remain available')
      }
      return
    }
    if (scope.runId !== undefined && ['session_begin', 'research_start', 'baseline_measure', 'final_select', 'final_measure', 'final_write', 'final_deliver', 'final_evidence', 'visualization_evidence', 'visualization_render', 'visualization_deliver'].includes(operation)) {
      const status = await this.ctx.studies.status(scope.runId)
      if (['session_begin', 'research_start', 'baseline_measure'].includes(operation) && isObject(status.selection)) throw new Error('The candidate is frozen; research cannot restart in this run')
      if (operation === 'final_measure' && !isObject(status.selection)) throw new Error('Freeze a candidate before final measurement')
      if (['final_select', 'final_measure', 'final_write', 'final_deliver', 'final_evidence', 'visualization_evidence', 'visualization_render', 'visualization_deliver'].includes(operation)) {
        if (!Array.isArray(status.sessions) || status.sessions.some(session => !isObject(session) || !['completed', 'failed'].includes(String(session.status)))) throw new Error('End all research sessions before final operations')
        if (!Array.isArray(status.pending_requests) || status.pending_requests.length > 0) throw new Error('Reconcile pending requests before final operations')
      }
    }
  }
  async authorizeFinal(agent: Agent, runId: string, signal: AbortSignal): Promise<void> {
    await this.authorize(agent, 'final_measure', { runId })
    const key = this.ctx.studies.root + ':' + runId
    const pending = this.approvals.get(key)
    if (pending) { await pending; signal.throwIfAborted(); return this.authorizeFinal(agent, runId, signal) }
    const grant = this.finalGrant(agent, runId, signal)
    this.approvals.set(key, grant)
    try { await grant } finally { this.approvals.delete(key) }
  }
  private async finalGrant(agent: Agent, runId: string, signal: AbortSignal): Promise<void> {
    signal.throwIfAborted()
    const status = await this.ctx.studies.status(runId, signal)
    const config = await this.ctx.studies.call('config', {}, { runId }, signal)
    const scope = authorizationScope(runId, status, config)
    const digest = scopeDigest(scope)
    if (await readAuthorization(this.ctx.studies.root, runId, digest)) return
    if(process.env.AS_NEXT_MANIFEST){
      const launch=JSON.parse(await readFile(process.env.AS_NEXT_MANIFEST,'utf8'))
      if(launch.job?.stage==='terminal' && launch.native_id===agent.id && launch.job.handoff?.run_id===runId){
        // The admitted terminal task grants this fixed final scope; config must still permit it.
        await saveAuthorization(this.ctx.studies.root,runId,scope,agent.id);return
      }
    }
    const project=await this.ctx.studies.projectBinding?.(agent)
    if(project?.runId===runId) {
      const consent=await this.ctx.studies.call('as-consent',{session:agent.id},{runId},signal)
      if(consent.authorized===true){await saveAuthorization(this.ctx.studies.root,runId,scope,agent.id);return}
    }
    const datasets = (scope.datasets as import('../services.js').Json[]).filter(isObject).map(dataset => `${dataset.dataset_id} (${String(dataset.manifest_sha).slice(0, 12)})`).join(', ')
    const result = await this.ctx.approval.request({
      agent, toolName: 'final_measure', signal,
      reason: `Authorize final evaluation for ${runId}. Frozen selection: ${JSON.stringify(scope.selection)}. Sealed final datasets: ${datasets}. Scope SHA-256: ${digest}. This permits the frozen candidate and baseline within the run's fixed budget and evaluation rules, and is reused only while that scope remains unchanged.`,
    })
    if (result !== 'allowed-once') throw new Error('Final evaluation has not been authorized')
    signal.throwIfAborted()
    const currentStatus = await this.ctx.studies.status(runId, signal)
    const currentConfig = await this.ctx.studies.call('config', {}, { runId }, signal)
    if (scopeDigest(authorizationScope(runId, currentStatus, currentConfig)) !== digest) throw new Error('Final evaluation scope changed while awaiting authorization')
    await saveAuthorization(this.ctx.studies.root, runId, scope, agent.id)
  }
}
