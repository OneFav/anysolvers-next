import type { Context } from '@deepseek-ai/cordis'
import type { HostConnectionHandle, ConnectionRpcHandler } from '@deepseek-ai/dsh-client-connection'
import type { JsonObject } from '../services.js'

export const name = 'anysolvers-ui'
export const inject = ['studies']
const endpoints = new Map([
  ['anysolvers/projects', 'projects'],
  ['anysolvers/catalog', 'launch-list'], ['anysolvers/configuration', 'ui-configuration'],
  ['anysolvers/artifact', 'terminal-artifact'], ['anysolvers/save', 'ui-save'], ['anysolvers/progress', 'ui-progress'],
])

/** Authenticated owner UI; exposes no arbitrary bridge operation, filesystem or model tool. */
export function uiHandler(ctx: Context): ConnectionRpcHandler {
  return async (endpoint, value, signal, peer) => {
    try {
      if (peer.principal.kind !== 'owner') throw new Error('Only the host owner can use the research panel')
      const operation = endpoints.get(endpoint)
      if (!operation || !value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid research UI request')
      const data = value as Record<string, unknown>
      if(operation==='projects'){
        if(Object.keys(data).length)throw new Error('Unknown research UI field')
        if(!ctx.studies.projectCatalog)throw new Error('请重启 DSH 加载多项目工作台。')
        return {ok:true,value:await ctx.studies.projectCatalog(signal)}
      }
      let backend=ctx.studies
      const projectId=data.projectId
      if(projectId!==undefined){
        if(typeof projectId!=='string'||!ctx.studies.projects||!ctx.studies.forRoot)throw new Error('Invalid project selection')
        const project=await ctx.studies.projects.get(projectId)
        backend=ctx.studies.forRoot(project.root)
      }
      const allowed = operation === 'launch-list' ? [] : operation === 'ui-progress' ? ['runId', 'offset', 'final_offset'] : operation === 'terminal-artifact' ? ['runId', 'kind', 'id', 'asset'] : operation === 'ui-save' ? ['studyId', 'revision', 'values'] : ['studyId']
      if (Object.keys(data).some(key => key!=='projectId' && !allowed.includes(key))) throw new Error('Unknown research UI field')
      if (operation === 'launch-list') return { ok: true, value: { ...await backend.call(operation, {}, {}, signal), root: backend.root } }
      const field = operation === 'ui-progress' || operation === 'terminal-artifact' ? 'runId' : 'studyId'
      const id = data[field]
      if (typeof id !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9_.-]*(?:\/[0-9]{3,})?$/.test(id) || (field === 'studyId' && id.includes('/'))) throw new Error('Invalid study or run identifier')
      const scope = field === 'runId' ? { runId: id } : { studyId: id }
      const payload = Object.fromEntries(Object.entries(data).filter(([key]) => key !== field && key !== 'projectId')) as JsonObject
      return { ok: true, value: await backend.call(projectId && operation==='ui-configuration'?'as-configuration':projectId && operation==='ui-save'?'as-save':operation, payload, scope, signal) }
    } catch (error) {
      return { ok: false, error: { code: 'ANYSOLVERS_UI', message: error instanceof Error ? error.message : 'Research UI request failed', details: {} } }
    }
  }
}

/** Exact owner-only Fetch routes share DSH's authenticated /api carrier, including Desktop. */
export function apply(ctx: Context): void {
  ctx.inject(['connection'], child => {
    const connection = child.get('connection') as HostConnectionHandle
    const handler = uiHandler(child)
    const disposers = [...endpoints.keys()].map(endpoint => connection.fetch.register({
      path: '/api/' + endpoint, methods: ['POST'], requestBody: 'buffered',
      async fetch(request) {
        let body: { type?: unknown; rpcId?: unknown; method?: unknown; payload?: unknown }
        try { body = await request.json() as typeof body } catch { return new Response('Invalid JSON', { status: 400 }) }
        if (!body || body.type !== 'client-request' || typeof body.rpcId !== 'string' || body.method !== endpoint) return new Response('Invalid request envelope', { status: 400 })
        const result = await handler(endpoint, body.payload, request.signal, connection.browserPeer)
        return Response.json({ type: 'server-response', rpcId: body.rpcId, result })
      },
    }))
    return async () => { await Promise.all(disposers.map(dispose => dispose())) }
  })
}
