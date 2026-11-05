import assert from 'node:assert/strict'
import {createScope} from '@deepseek-ai/dsh-scope'
export function loopAdapter() {
  const handles = new Set()
  const queued = []
  let capture = false
  let loopContext
  return {
    handles, queued, captureFollowups() { capture = true },
    attach(ctx) { loopContext = ctx; ctx.agents.setFactory(this) },
    async createAgent(owner, options) {
      const session = owner.sessions.prepare(options.sessionId, { meta: options.meta, seed: options.seed })
      const agent = { id: session.id, session, options: options.agentOptions ?? {}, status: 'idle',
        inbox: { nextTurn: [], nextStep: [] },
        whenIdle: async () => {},
        followup: message => {
          if (!capture) throw new Error('This composition smoke must never drive a model')
          queued.push({ agent, message })
        },
      }
      const scoped = createScope(loopContext, agent)
      agent.ctx = scoped.ctx
      let detachSession = () => {}
      let detachAgent = () => {}
      let disposed = false
      const handle = { agent, async dispose() {
        if (disposed) return
        disposed = true
        detachAgent()
        detachSession()
        await scoped.dispose()
        handles.delete(handle)
      } }
      try {
        await options.setup?.(scoped.ctx, agent)
        options.signal?.throwIfAborted()
        // The real registry must not publish a partially composed identity.
        assert.equal(owner.agents.get(agent.id), undefined)
        assert.equal(owner.sessions.get(session.id), undefined)
        detachSession = scoped.ctx.sessions.enter(session)
        detachAgent = scoped.ctx.agents.enter(agent, options.parentAgent)
        scoped.ctx.sessions.announce(session)
        await scoped.ctx.agents.announce(agent, 'create', options.signal)
        handles.add(handle)
        return handle
      } catch (error) {
        await handle.dispose()
        throw error
      }
    },
    async resume() { throw new Error('The smoke adapter never restores or replays model sessions') },
  }
}
