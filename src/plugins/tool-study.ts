import type { Context } from '@deepseek-ai/cordis'
import type { ParameterSchemaSpec } from '@deepseek-ai/dsh-tools'
import { register, text, payload } from '../core/tool.js'
import type { JsonObject, Scope } from '../services.js'
import { prepareLaunch, registerLaunchCommand } from '../core/launch.js'
import type { LaunchAction } from '../core/launch.js'

export const name = 'anysolvers-main-tools'
export const inject = ['tools', 'systemPrompt', 'studies', 'studySessions', 'studySandbox', 'studyPolicy']

/** Coordinator tools are mounted only inside the main preset. */
export function apply(ctx: Context): void {
  if(!process.env.AS_NEXT_MANIFEST)ctx.systemPrompt.section({ name: 'anysolvers:coordinator', order: 100,
    text: `Coordinate AnySolvers through study tools. During initialization clarify the task, data and resource bounds, prepare files with study_write, register datasets/ideas, then seal. Never substitute synthetic data for missing real data. Read study_module for the selected method's full requirements.
Before research confirm user authorization and the effective configuration, create a batch, measure its development baseline, then research_start. Each researcher continues independently from its sealed notebook. Inspect pending_requests separately from audit.ok. Unknown execution requires study_recover; never change IDs to repeat it.
For a short request to start an initialized research study, use study_launch(target=the user's study ID). Without a target, discover sealed studies; if multiple exist ask the user to choose, never select a sample automatically. Use the selected study's task, datasets, ideas and configuration. Do not ask for a long pasted prompt or require a benchmark-specific launch file. After research, use the mounted final and visualization plugins to complete terminal deliverables under the run policy. For explicit continuation use study_launch(action=final,target=the study or run ID). If a native command already supplied a validated plan, follow it without calling study_launch again. Existing runs default to status, never automatic relaunch.
After all researchers and requests settle, final_select freezes the eligible anchor winner. final_measure uses scoped authorization for only baseline and the frozen candidate on sealed final data. Delegate terminal tools and their requirements to the final and visualization plugins mounted in this same main session. Preserve failures and missing results. Final feedback never returns to the completed research. Budget changes require user authorization.`,
  })
  ctx.inject(['commands'], commandCtx => registerLaunchCommand(commandCtx))
  register(ctx, 'study_launch', 'Start a standard initialized and sealed research study. Without a target discover studies and request a choice when ambiguous. Existing runs are inspected, never replayed.',
    { target: text('Study ID or study/001 run ID; no default problem', false), config: text('Explicit repository-relative config; otherwise use the initialization source', false),
      action: { type: 'string', enum: ['start', 'status', 'final'] } }, async (a, e) => {
      await ctx.studyPolicy.authorize(e.agent, 'study_launch')
      const requested = a.action ?? 'start'
      if (!['start', 'status', 'final'].includes(requested)) throw new Error('Invalid launch action')
      return prepareLaunch(ctx, { action: requested as LaunchAction, ...(a.target ? { target: a.target } : {}), ...(a.config ? { config: a.config } : {}) }, e.signal)
    })
  const run = { run_id: text('Study/batch identifier') }
  const study = { study_id: text('Study identifier') }
  function wire(name: string, operation: string, description: string, parameters: ParameterSchemaSpec,
    key: 'run_id' | 'study_id', fixed: JsonObject = {}) {
    register(ctx, name, description, parameters, async (args, exec) => {
      const identifier = args[key]
      if (typeof identifier !== 'string' || !identifier) throw new Error('A study or run identifier is required')
      const scope: Scope = key === 'run_id' ? { runId: identifier } : { studyId: identifier }
      await ctx.studyPolicy.authorize(exec.agent, name, scope)
      const values: Record<string, unknown> = { ...fixed }
      for (const field of Object.keys(parameters)) if (field !== key && args[field] !== undefined) values[field] = args[field]
      return ctx.studies.call(operation, payload(values), scope, exec.signal)
    })
  }
  wire('study_status', 'status', 'Read phase, sessions, pending work and remaining budgets.', run, 'run_id')
  wire('study_config', 'config', 'Read pinned effective configuration.', run, 'run_id')
  register(ctx, 'study_check', 'Validate a study or numbered run; supply exactly one identifier.',
    { run_id: text('Numbered run ID', false), study_id: text('Study ID', false) }, async (a, e) => {
      if (Boolean(a.run_id) === Boolean(a.study_id)) throw new Error('Supply exactly one study_id or run_id')
      const scope = a.run_id ? { runId: a.run_id } : { studyId: a.study_id! }
      await ctx.studyPolicy.authorize(e.agent, 'study_check', scope)
      return ctx.studies.call(a.run_id ? 'check' : 'study-check', {}, scope, e.signal)
    })
  wire('study_plan', 'launch-profile', 'Read the sealed initial idea IDs and exact researcher assignments for this existing batch; does not launch work.', run, 'run_id')
  wire('study_audit', 'audit', 'Audit records; pending_requests must also be checked.', run, 'run_id')
  wire('study_init', 'study-init', 'Create a study from prepared task and config files.',
    { ...study, config: text('Repository-relative config path') }, 'study_id')
  wire('study_write', 'prepare-write', 'Write an unsealed task, dataset, idea or config draft. Registered assets are immutable.',
    { ...study, path: text('Allowed repository-relative draft path'), content: text('Complete UTF-8 content') }, 'study_id')
  wire('study_deliver', 'study-deliver', 'Validate and register a dataset or initial ideas.',
    { ...study, kind: { type: 'string', enum: ['dataset', 'ideas'], required: true }, manifest: text('Initialization-relative manifest') }, 'study_id')
  wire('study_seal', 'study-seal', 'Validate and seal initialization without starting research.', study, 'study_id')
  wire('study_plan_resources', 'study-plan-resources', 'Recommend resources without executing a candidate.',
    { ...study, config: text('Repository-relative config path') }, 'study_id')
  wire('batch_init', 'batch-init', 'Create a numbered batch from a sealed study and authorized configuration.',
    { ...study, config: text('Repository-relative config path'), researchers: { type: 'integer' }, rounds: { type: 'integer' } }, 'study_id')
  register(ctx, 'study_module', 'Read method documents and schemas. Use a run, study plus config, or a category before initialization.',
    { run_id: text('Numbered run ID', false), study_id: text('Study ID', false), config: text('Repository-relative config', false), slot: { type: 'string', enum: ['task','datasets','ideas','research','evaluation','selections','final','visualization'], required: true } }, async (a, e) => {
      if (a.run_id && a.study_id) throw new Error('Do not combine study and run identifiers')
      const scope = a.run_id ? { runId: a.run_id } : a.study_id ? { studyId: a.study_id } : {}
      await ctx.studyPolicy.authorize(e.agent, 'study_module', scope)
      return ctx.studies.call('module', payload({ slot: a.slot, config: a.config }), scope, e.signal)
    })
  wire('study_evidence', 'evidence', 'Read trusted candidate and evaluation summaries for selection and reporting.',
    { ...run, evaluation: text('One evaluation ID', false), offset: { type: 'integer' }, limit: { type: 'integer' } }, 'run_id')
  wire('baseline_measure', 'measure', 'Measure baseline on development data through the evaluation sandbox.',
    { ...run, dataset: text('Development dataset ID'), evaluation: text('Unique evaluation ID') }, 'run_id', { candidate: 'baseline', scope: 'development' })
  registerSessionTools(ctx, run)
}

function registerSessionTools(ctx: Context, run: { run_id: ReturnType<typeof text> }): void {
  register(ctx, 'session_begin', 'Start and activate one fresh restricted researcher generation.',
    { ...run, actor: text('Actor ID'), idea: text('Initial idea ID', false), iteration: text('Sealed continuation iteration', false) }, async (a, e) => {
      const runId = String(a.run_id)
      await ctx.studyPolicy.authorize(e.agent, 'session_begin', { runId })
      return ctx.studySessions.start(e.agent, runId, a.actor, a.idea, a.iteration)
    })
  register(ctx, 'research_start', 'Start researcher assignments and continue notebook generations. Waits for settled completion by default; wait=false returns early.',
    { ...run, wait: { type: 'boolean' }, assignments: { type: 'array', required: true, items: { type: 'object', additionalProperties: false,
      properties: { actor: text('Actor ID'), idea: text('Initial idea ID') } } } }, async (a, e) => {
      const runId = String(a.run_id)
      await ctx.studyPolicy.authorize(e.agent, 'research_start', { runId })
      const started = await ctx.studySessions.startRun(e.agent, runId, a.assignments)
      return a.wait === false ? started : ctx.studySessions.waitRun(e.agent, runId)
    })
  register(ctx, 'session_end', 'Close a settled researcher after native lifecycle and pending-work checks.',
    { ...run, actor: text('Actor ID'), status: { type: 'string', enum: ['completed', 'failed'], required: true } }, async (a, e) => {
      const runId = String(a.run_id)
      await ctx.studyPolicy.authorize(e.agent, 'session_end', { runId })
      if (a.status !== 'completed' && a.status !== 'failed') throw new Error('Invalid completion status')
      return ctx.studySessions.end(e.agent, runId, a.actor, a.status)
    })
  register(ctx, 'study_recover', 'Reconcile native IDs and outstanding work without repeating uncertain execution.', run, async (a, e) => {
    const runId = String(a.run_id)
    await ctx.studyPolicy.authorize(e.agent, 'study_recover', { runId })
    return ctx.studySessions.recover(e.agent, runId)
  })
  register(ctx, 'study_doctor', 'Check configured evaluator and researcher file sandbox without running a solver. Supply run_id after batch initialization to inspect its actual remote backend.', {run_id:text('Optional initialized run ID',false)}, async (a, e) => {
    const scope=a.run_id?{runId:a.run_id}:{}
    await ctx.studyPolicy.authorize(e.agent, 'study_doctor',scope)
    return { backend: await ctx.studies.call('doctor', {}, scope, e.signal), workspace: await ctx.studySandbox.doctor() }
  })
}
