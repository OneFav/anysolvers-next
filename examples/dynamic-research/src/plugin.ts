import {validateDelivery} from './contracts.js'
import type {Context} from '@deepseek-ai/cordis'
import type {HostConnectionHandle} from '@deepseek-ai/dsh-client-connection'
import Schema from '@deepseek-ai/schemastery'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {join,dirname} from 'node:path'
import {BoundedProcess,atomic,read,sha,containedFile,runDirectory} from '@onefav/research-runtime'
import type {JsonObject,ResearchImplementation} from '@onefav/research-runtime'
export const name='dynamic-research-fixture'
export const inject=['researchRuntime']
export const Config=Schema.object({python:Schema.string().required()})
export function createMethod(python:string):ResearchImplementation{
 const packageRoot=fileURLToPath(new URL('../',import.meta.url))
 const method:ResearchImplementation={id:'dynamic-fixture',version:'0.1.0',apiVersion:1,mainPreset:'dynamic-fixture-main',packageRoot,ui:{module:'@onefav/dynamic-research-fixture',panel:'dynamic-fixture',route:'dynamic-fixture'},
 async preflight({root,target}){
  const config=await read<any>(join(root,'configs/dynamic-fixture.json'))
  if(!config||config.format!=='dynamic-fixture-v1'||config.testOnly!==true||!Array.isArray(config.values)||config.values.length<2||config.values.some((v:unknown)=>typeof v!=='number'||!Number.isFinite(v)))return {ready:false,target:'',revision:'',message:'需要 configs/dynamic-fixture.json：format=dynamic-fixture-v1、testOnly=true、至少两个有限数值。仅模拟验收。',payload:{}}
  const selected=target??config.studyId??'dynamic-demo';if(!/^[a-z][a-z0-9-]{0,63}$/.test(selected))throw new Error('Invalid fixture target')
  return {ready:true,target:selected,revision:sha(JSON.stringify(config)),message:'动态测试初始化通过；仅模拟执行，未创建运行。',payload:config as JsonObject}
 },
 async start({root,checked,allocate,signal}){
  const runId=await allocate(checked.target),run=join(root,'runs',runId),values=checked.payload.values as number[]
  const initial={version:'v1',values},updated={version:'v2',values:[...values,Math.max(...values)+1]}
  const snapshots=[]
  for(const value of [initial]){const path='dynamic/datasets/'+value.version+'.json';await atomic(join(run,path),value);snapshots.push({version:value.version,path,sha256:sha(await readFile(join(run,path)))})}
  const source={path:'configs/dynamic-fixture.json',sha256:sha(await readFile(join(root,'configs/dynamic-fixture.json')))}
  const state={schemaVersion:1,testOnly:true,source,snapshotRefs:snapshots,configurations:[{id:'zero',bias:0},{id:'one',bias:1}],updateRationale:'测试规则追加一个高于现有最大值的实例；两配置在共同 v2 集合重新评测。'}
  await atomic(join(run,'dynamic/state.json'),state)
  const measure=async()=>{const process=new BoundedProcess();let response
  try{response=await process.execute({executable:python,args:['-B',join(packageRoot,'python/fixture.py')],cwd:root,env:{PATH:globalThis.process.env.PATH??'/usr/bin:/bin',PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:join(dirname(fileURLToPath(import.meta.resolve('@onefav/research-runtime/package.json'))),'python')},input:JSON.stringify({root,runId}),maxInputBytes:65536,maxOutputBytes:1024*1024,timeoutMs:30000,signal})}finally{await process.close()}
  const reply=JSON.parse(response.stdout);if(response.code!==0||reply.ok!==true)throw new Error('Fixture execution failed; inspect the recorded jobs')
  return reply.value.measurements as any[]
  }
  const first=await measure()
  const updatedPath='dynamic/datasets/v2.json';await atomic(join(run,updatedPath),updated)
  snapshots.push({version:'v2',path:updatedPath,sha256:sha(await readFile(join(run,updatedPath)))})
  state.updateRationale=`v1 测试测量最大模拟损失为 ${Math.max(...first.map(v=>v.fixtureLoss))}；按方法规则追加高于现有最大值的实例，并在共同 v2 集合重新评测。`
  await atomic(join(run,'dynamic/state.json'),state)
  const measurements=await measure(),ranking=measurements.filter((v:any)=>v.datasetVersion==='v2').sort((a:any,b:any)=>a.fixtureLoss-b.fixtureLoss)
  const delivery={schemaVersion:1,testOnly:true,snapshotRefs:snapshots,updateRationale:state.updateRationale,comparisonDataset:'v2',selectedConfiguration:ranking[0].configuration,measurements}
  await validateDelivery(packageRoot,delivery)
  await atomic(join(run,'dynamic/delivery.json'),delivery)
  return {runId,methodRef:'dynamic/delivery.json',message:`完成模拟动态评测 ${runId}；结果分集合展示，仅在共同 v2 集合选优，无真实科研分数。`}
 },
 async status(root,runId){const delivery=await read<JsonObject>(join(root,'runs',runId,'dynamic/delivery.json'));if(delivery){await validateDelivery(packageRoot,delivery);for(const snapshot of delivery.snapshotRefs as any[]){if(sha(await readFile(await containedFile(join(root,'runs',runId),snapshot.path)))!==snapshot.sha256)throw new Error('Sealed dataset snapshot changed')}}return {complete:!!delivery,progress:delivery??{testOnly:true}}}
 };return method
}
export function apply(ctx:Context,config:{python:string}){
 ctx.effect(()=>ctx.researchRuntime.register(createMethod(config.python)))
 ctx.inject(['connection'],child=>{
  const connection=child.get('connection') as HostConnectionHandle
  return connection.fetch.register({path:'/api/dynamic-fixture/view',methods:['POST'],requestBody:'buffered',async fetch(request){
   if(connection.browserPeer.principal.kind!=='owner')return new Response('Owner only',{status:403})
   const body=await request.json() as any;if(body?.type!=='client-request'||body.method!=='dynamic-fixture/view'||typeof body.rpcId!=='string')return new Response('Invalid request',{status:400})
   let result
   try{const {projectId,runId}=body.payload;const project=await ctx.researchRuntime.projects.get(projectId),record=await read<any>(join(runDirectory(project.root,runId),'run.json'));if(record?.implementationId!=='dynamic-fixture'||record.projectId!==project.id||record.root!==project.root||record.runId!==runId)throw new Error('Run does not belong to this method');const value=await createMethod(config.python).status(project.root,runId);result={ok:true,value}}catch(e){result={ok:false,error:{message:String(e)}}}
   return Response.json({type:'server-response',rpcId:body.rpcId,result})
  }})
 })
}
