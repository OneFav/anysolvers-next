import { Context, Service } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import { SessionId } from '@deepseek-ai/dsh-session'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-agent-preset-registry'
import type {} from '@deepseek-ai/dsh-llm'
import { SessionController } from '../core/session-controller.js'
import type { SessionControllerConfig } from '../core/session-controller.js'
import type { JsonObject, ResearchBinding } from '../services.js'

/** Real fresh-session orchestration; Python retains the only research budget clock. */
export default class StudySessionsService extends Service {
  static inject = ['studies', 'studySandbox', 'agents', 'agentPresets', 'llm']
  static Config = Schema.object({
    provider: Schema.string().required().description('Exact configured DSH provider route for research models'),
    researcherPreset: Schema.string().default('anysolvers-researcher'),
    coordinatorPreset: Schema.string().default('anysolvers-main'),
  })
  private readonly controllers = new Map<string, SessionController>()
  get controller():SessionController {return this.forRoot(this.ctx.studies.root)}

  constructor(ctx: Context, private readonly config: SessionControllerConfig) {
    super(ctx, 'studySessions')
    if (!config.provider.trim()) throw new Error('A research provider route is required')
    ctx.on('agent/error', ({agent}) => {for(const c of this.controllers.values())c.failed(agent.id)})
    ctx.on('agent/disposed', ({ agent }) => { for(const c of this.controllers.values())c.parentDisposed(agent.id) })
    ctx.effect(() => async () => { await Promise.all([...this.controllers.values()].map(c=>c.dispose())) })
  }
  private forRoot(root:string):SessionController {
    const cached=this.controllers.get(root)
    if(cached)return cached
    const ctx=this.ctx,config=this.config
    const controller = new SessionController({
      studies: ctx.studies.forRoot?.(root) ?? ctx.studies, sandbox: ctx.studySandbox.forRoot?.(root) ?? ctx.studySandbox,
      create: async (options) => {
        // Bind before the researcher receives its first model turn; cwd stays its private sandbox.
        if(options.parentAgent && options.sessionId){
          const binding=await ctx.studies.projectBinding?.(options.parentAgent)
          if(!binding)throw new Error('Research supervisor has no project binding')
          await ctx.studies.projects!.bind(options.sessionId,binding)
        }
        return ctx.agents.create(options)
      },
      ...(process.env.AS_NEXT_MANIFEST ? {resume: async (options: import('@deepseek-ai/dsh-agent').ResumeAgentOptions) => {
        if(options.parentAgent){
          const binding=await ctx.studies.projectBinding?.(options.parentAgent)
          if(!binding)throw new Error('Research supervisor has no project binding')
          await ctx.studies.projects!.bind(options.resumeSessionId,binding)
        }
        return ctx.agents.resume(options)
      }} : {}),
      get: (id) => ctx.agents.get(SessionId(id)),
      mountPreset: async (agentCtx) => { await ctx.agentPresets.mount(agentCtx, config.researcherPreset) },
      validateRoute: async (options, signal) => {
        if (!options.provider || !options.model || !options.reasoningEffort) throw new Error('Incomplete explicit model route')
        const models=await ctx.llm.listModels(options.provider)
        if(models.length && !models.some(m=>m.id===options.model))throw new Error('Requested research model is not offered by the configured provider: '+options.model)
        const resolved = await ctx.llm.resolveCallConfig({ provider: options.provider, model: options.model,
          reasoningEffort: options.reasoningEffort }, signal)
        if (resolved.provider !== options.provider || resolved.model !== options.model
          || resolved.reasoningEffort !== options.reasoningEffort) throw new Error('Resolved route changed the research model settings')
      },
      isCoordinator: (agent) => agent.session.header.origin !== 'subagent'
        && ctx.agentPresets.composedPreset(agent.ctx) === config.coordinatorPreset,
    }, config)
    this.controllers.set(root,controller)
    return controller
  }

  private async owner(parent:Agent,runId:string):Promise<SessionController>{
    const root=this.ctx.studies.root
    const existing=await this.ctx.studies.projectBinding?.(parent)
    if(existing){
      if(existing.root!==root || existing.runId!==runId)throw new Error('Supervisor is already bound to another project or run')
    }else{
      const project=await this.ctx.studies.projects!.register(root)
      await this.ctx.studies.bindProject!(parent,{projectId:project.id,root,runId,implementationId:'anysolvers'})
    }
    return this.forRoot(root)
  }

  binding(agent: Agent): ResearchBinding | undefined { return [...this.controllers.values()].map(c=>c.binding(agent)).find(Boolean) }
  async start(parent: Agent, runId: string, actor: string, idea?: string, iteration?: string): Promise<JsonObject> {
    return (await this.owner(parent,runId)).start(parent, runId, actor, idea, iteration)
  }
  end(parent: Agent, runId: string, actor: string, status: 'completed' | 'failed'): Promise<JsonObject> {
    return this.controller.end(parent, runId, actor, status)
  }
  async recover(parent: Agent, runId: string): Promise<JsonObject> { return (await this.owner(parent,runId)).recover(parent, runId) }
  async startRun(parent: Agent, runId: string, assignments: { actor: string; idea: string }[]): Promise<JsonObject> {
    return (await this.owner(parent,runId)).startRun(parent, runId, assignments)
  }
  waitRun(parent: Agent, runId: string): Promise<JsonObject> { return this.controller.waitRun(parent, runId) }
}
