import type {Context} from '@deepseek-ai/cordis'
import {fileURLToPath} from 'node:url'
import type {JsonObject} from '../services.js'
import {researchPlan,formatPreflight} from '../core/research-method.js'
import '@onefav/research-runtime'
export const name='anysolvers-method'
export const inject=['researchRuntime','studies','studySandbox','studyPolicy']
export function apply(ctx:Context){ctx.effect(()=>ctx.researchRuntime.register({id:'anysolvers',version:'0.7.0',apiVersion:1,mainPreset:'anysolvers-main',packageRoot:fileURLToPath(new URL('../../',import.meta.url)),ui:{module:'@onefav/anysolvers',panel:'anysolvers',route:'anysolvers'},
 async preflight({agent,root,target,signal}){
  await ctx.studyPolicy.authorize(agent,'as_research')
  if(target&&!/^[a-z][a-z0-9-]{0,63}$/.test(target))throw new Error('Invalid AnySolvers study ID')
  const result=await ctx.studies.forRoot!(root).call('as-preflight',{},target?{studyId:target}:{},signal)
  return {ready:result.ready===true,revision:String(result.revision??''),target:String(result.study_id??''),payload:result,message:result.ready===true?`初始化检定通过：${root} / ${result.study_id}。未创建 run；将使用当前 UI 已保存参数，包含最终测试与图表。`:formatPreflight(result)}
 },
 async validateEnvironment({root}){
  const environment=await ctx.studySandbox.forRoot!(root).doctor();if(environment.ok!==true)throw new Error('运行环境检定未通过；未创建 run。')
 },
 async start({agent,root,requestId,checked,signal}){
  const project=await ctx.studies.projects!.register(root),result=await ctx.studies.forRoot!(root).call('as-start',{session:agent.id,request_id:requestId,revision:checked.revision},{studyId:checked.target},signal)
  if(result.ready!==true)throw new Error(formatPreflight(result))
  const runId=String(result.run_id);await ctx.studies.bindProject!(agent,{projectId:project.id,root,runId,implementationId:'anysolvers'})
  return {runId,message:`已创建 ${runId}，工作区 ${root}。将完成研究、最终测试、报告和图表。`,prompt:researchPlan(runId,result.profile as JsonObject),methodRef:'state.json'}
 },
 async status(root,runId,signal){const progress=await ctx.studies.forRoot!(root).call('terminal-status',{},{runId},signal);return {complete:progress.complete===true,progress}}
}))}
