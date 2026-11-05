import type {Context} from '@deepseek-ai/cordis'
import type {Agent} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-commands'
import {createUserMessage} from '@deepseek-ai/dsh-llm'
import './service.js'
export const name='research-runtime-command'
export const inject=['researchRuntime','systemPrompt']
export function apply(ctx:Context){
 const pending=new WeakSet<Agent>()
 ctx.inject(['commands'],child=>child.commands.register({name:'as-research',description:'Check or start the research implementation bound to this main session',input:{hint:'[TARGET] [--check]'},handler:async({agent,rawInput,signal})=>{
  if(pending.has(agent)||agent.status!=='idle'||agent.inbox.nextTurn.length||agent.inbox.nextStep.length)return {kind:'error',text:'主会话正在运行或排队，请等待当前操作结束。'}
  const words=rawInput.trim().split(/\s+/).filter(Boolean),checkOnly=words.includes('--check'),targets=words.filter(w=>w!=='--check')
  if(targets.length>1||targets[0]?.startsWith('--'))return {kind:'error',text:'用法：/as-research [TARGET] [--check]'}
  pending.add(agent);let created:string|undefined
  try{
   const root=agent.session.header.cwd;if(!root)throw new Error('Select a workspace')
   const input={agent,root,signal,...(targets[0]?{target:targets[0]}:{})},checked=await ctx.researchRuntime.preflight(input)
   if(!checked.ready)return {kind:'error',text:checked.message}
   if(checkOnly)return {kind:'success',text:checked.message}
   const result=await ctx.researchRuntime.start(input,checked);created=result.runId
   signal.throwIfAborted();if(agent.status!=='idle'||agent.inbox.nextTurn.length||agent.inbox.nextStep.length)throw new Error('Main session changed during launch')
   if(result.prompt)agent.followup(createUserMessage({content:[{type:'text',text:result.prompt}],source:{kind:'research-runtime-command',projectId:result.projectId,runId:result.runId,implementationId:result.implementationId}}))
   return {kind:'success',text:result.message}
  }catch(e){return {kind:'error',text:(e instanceof Error?e.message:String(e))+(created?`\n已创建 ${created}；请核对原请求，不重复启动。`:'')}}finally{pending.delete(agent)}
 }}))
}
declare module '@deepseek-ai/dsh-llm' {interface MessageSourceMap {'research-runtime-command':{kind:'research-runtime-command';projectId:string;runId:string;implementationId:string}}}
