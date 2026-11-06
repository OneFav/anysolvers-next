import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { register, text, payload } from '../core/tool.js'
import type { ResearchBinding } from '../services.js'

export const name = 'anysolvers-researcher-tools'
export const inject = ['tools', 'systemPrompt', 'studies', 'studySessions', 'studySandbox', 'studyPolicy']

/** Researcher tools never accept a caller-controlled run/session identity. */
export function apply(ctx: Context): void {
  ctx.systemPrompt.section({ name: 'anysolvers:researcher', order: 100,
    text: `You are an AnySolvers researcher. Work only on your assigned task and idea/iteration.
Use solver_read/solver_write/solver_list to edit your private files. All candidate execution goes through solver_evaluate; there is no general shell. For a sealed SSH compute configuration, solver_evaluate automatically uses the remote endpoint; local DSH does not sandbox the remote process. Do not invent direct SSH commands outside evaluation accounting. Every new source version needs a new candidate ID. Read solver_context before experiments and use solver_result for details.
The backend owns budgets and excludes trusted evaluation waiting from active research time. When exhausted, read results and save iteration/checkpoint; do not request new experiments. Finish outstanding requests before ending. Unknown execution must be reported, never retried with a new ID.
Use solver_findings/share for public evidence and solver_reuse before using another researcher's published source. Keep observations separate from hypotheses. Preserve a checkpoint of this round, including important route changes and their evidence. When selection_generates_next_idea is true, next_iteration_id must be null: selection generates the next research direction. Checkpoint record requires exactly id, session_id, candidate_id (nullable), evidence_ids, learned, failed_or_uncertain, next_iteration_id (nullable); omit schema_version. Workspace paths are relative to your private directory, not research/ or the project root. Do not request final data, other private notes, administrative tools or additional agents.`,
  })
  async function binding(agent: Agent, operation: string): Promise<ResearchBinding> {
    const value = ctx.studySessions.binding(agent)
    if (!value) throw new Error('This native session is not bound to an active researcher')
    await ctx.studyPolicy.authorize(agent, operation, { runId: value.runId, sessionId: value.sessionId })
    return value
  }
  register(ctx, 'solver_context', 'Read your history and budget, or list/read sealed development cases by dataset and case ID.',
    { dataset: text('Development dataset ID', false), case: text('Case ID; omit to list cases', false),
      offset: { type: 'integer' }, limit: { type: 'integer' } }, async (a, e) => {
    const b = await binding(e.agent, 'solver_context')
    if (!a.dataset && (a.case !== undefined || a.offset !== undefined || a.limit !== undefined)) throw new Error('A dataset is required for case access')
    if (a.dataset) return ctx.studies.call('research-development', payload({ dataset: a.dataset, case: a.case, offset: a.offset, limit: a.limit }),
      { runId: b.runId, sessionId: b.sessionId }, e.signal)
    return ctx.studies.call('research', { operation: 'context' }, { runId: b.runId, sessionId: b.sessionId }, e.signal)
  })
  register(ctx, 'solver_evaluate', 'Snapshot a source version and submit one idempotent development evaluation.',
    { candidate_id: text('Unique candidate ID'), source: text('Workspace-relative solver source'), rationale: text('Algorithm change being tested'),
      parent_id: text('Actual parent candidate, if any', false), dataset: text('Authorized development dataset ID'), id: text('Unique request ID') }, async (a, e) => {
      const b = await binding(e.agent, 'solver_evaluate')
      const scope = { runId: b.runId, sessionId: b.sessionId }
      const imported = await ctx.studies.call('candidate-import', payload({ candidate_id: a.candidate_id, source: a.source,
        rationale: a.rationale, parent_id: a.parent_id }), scope, e.signal)
      if (typeof imported.manifest !== 'string') throw new Error('Candidate import did not return a manifest')
      return ctx.studies.call('research', { operation: 'evaluate', manifest: imported.manifest, id: a.id, dataset: a.dataset }, scope, e.signal)
    })
  register(ctx, 'solver_result', 'Read an existing authorized development result without repeating execution.',
    { id: text('Request ID', false), evaluation: text('Evaluation ID', false), details: { type: 'boolean' }, case: text('Specific case ID', false) }, async (a, e) => {
      const b = await binding(e.agent, 'solver_result')
      if (Boolean(a.id) === Boolean(a.evaluation)) throw new Error('Supply exactly one request id or evaluation id')
      return ctx.studies.call('research', payload({ operation: 'result', id: a.id, evaluation: a.evaluation, details: a.details, case: a.case }), { runId: b.runId, sessionId: b.sessionId }, e.signal)
    })
  register(ctx, 'solver_findings', 'Read the published findings directory or one public finding.',
    { id: text('Finding ID; omit for the directory', false) }, async (a, e) => {
      const b = await binding(e.agent, 'solver_findings')
      return ctx.studies.call('research', payload({ operation: 'findings', id: a.id }), { runId: b.runId, sessionId: b.sessionId }, e.signal)
    })
  for (const operation of ['share', 'reuse', 'checkpoint', 'iteration'] as const) {
    const wire = `solver_${operation}`
    register(ctx, wire, operation === 'reuse'
      ? 'Register reuse of a public finding and copy its fixed source into your workspace.'
      : `Validate and save a ${operation} record using the supplied research schema.`,
    { record: { type: 'object', additionalProperties: true, required: true } }, async (a, e) => {
      const b = await binding(e.agent, wire)
      return ctx.studies.call(operation === 'reuse' ? 'reuse-export' : 'research-record',
        payload({ ...(operation === 'reuse' ? {} : { operation }), record: a.record }), { runId: b.runId, sessionId: b.sessionId }, e.signal)
    })
  }
  register(ctx, 'solver_read', 'Read a UTF-8 file inside your private work directory.', { path: text('Workspace-relative path') }, async (a, e) => {
    const b = await binding(e.agent, 'solver_read')
    return { path: a.path, content: await ctx.studySandbox.read(b, a.path) }
  })
  register(ctx, 'solver_write', 'Write a UTF-8 file inside your private work directory; this never executes it.',
    { path: text('Workspace-relative path'), content: text('Complete file content') }, async (a, e) => {
      const b = await binding(e.agent, 'solver_write')
      await ctx.studySandbox.write(b, a.path, a.content)
      return { path: a.path, written: true }
    })
  register(ctx, 'solver_list', 'List files inside your private work directory.', { path: text('Workspace-relative directory', false) }, async (a, e) => {
    const b = await binding(e.agent, 'solver_list')
    return { files: await ctx.studySandbox.list(b, a.path) }
  })
}
