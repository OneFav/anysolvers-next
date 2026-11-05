import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ParameterSchemaSpec, InferArgs, ToolRunContext } from '@deepseek-ai/dsh-tools'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { JsonObject } from '../services.js'

export function text(description: string, required?: true): { type: 'string'; description: string; required: true }
export function text(description: string, required: false): { type: 'string'; description: string }
export function text(description: string, required = true) {
  return { type: 'string' as const, description, ...(required ? { required: true as const } : {}) }
}

/** Canonical JSON results remain usable through the DSH tool execution pipeline. */
export function register<P extends ParameterSchemaSpec>(ctx: Context, name: string, description: string,
  parameters: P, execute: (args: InferArgs<P>, exec: ToolRunContext & { agent: Agent }) => Promise<JsonObject>): void {
  ctx.tools.register(defineTool({ name, description, parameters,
    output: { schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: async (args, exec) => {
      if (!exec.agent) throw new Error('Research tools require a native agent identity')
      const task = () => execute(args, { ...exec, agent: exec.agent! })
      // Router/framework processes do not mount the research service. Optional
      // access must use the service lookup, not Cordis's injected-property guard.
      const studies = ctx.get('studies')
      return studies?.withAgent ? studies.withAgent(exec.agent, task) : task()
    },
  }))
}

/** Optional schema properties are omitted, never serialized as undefined. */
export function payload(values: Record<string, unknown>): JsonObject {
  return JSON.parse(JSON.stringify(values)) as JsonObject
}
