import type { Context } from '@deepseek-ai/cordis'
import type { ParameterSchemaSpec } from '@deepseek-ai/dsh-tools'
import { register, text, payload } from './tool.js'

/** Shared implementation, not an additional plugin entry. */
export function terminalTool(ctx: Context,name:string,operation:string,description:string,parameters:ParameterSchemaSpec):void {
  register(ctx,name,description,{run_id:text('Study/batch identifier'),...parameters},async(args,exec)=>{
    const runId=args.run_id
    if(typeof runId!=='string'||!runId)throw new Error('A numbered run identifier is required')
    await ctx.studyPolicy.authorize(exec.agent,name,{runId})
    const values:Record<string,unknown>={}
    const input=args as Record<string,unknown>
    for(const key of Object.keys(parameters))if(input[key]!==undefined)values[key]=input[key]
    return ctx.studies.call(operation,payload(values),{runId},exec.signal)
  })
}
