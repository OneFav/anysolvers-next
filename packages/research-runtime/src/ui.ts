import type {Context} from '@deepseek-ai/cordis'
import type {HostConnectionHandle} from '@deepseek-ai/dsh-client-connection'
import './service.js'
export const name='research-runtime-ui'
export const inject=['researchRuntime']
export function apply(ctx:Context){ctx.inject(['connection'],child=>{
 const connection=child.get('connection') as HostConnectionHandle
 return connection.fetch.register({path:'/api/research-runtime/navigation',methods:['POST'],requestBody:'buffered',async fetch(request){
  if(connection.browserPeer.principal.kind!=='owner')return new Response('Owner only',{status:403})
  let body:any;try{body=await request.json()}catch{return new Response('Invalid JSON',{status:400})}
  if(body?.type!=='client-request'||typeof body.rpcId!=='string'||body.method!=='research-runtime/navigation')return new Response('Invalid envelope',{status:400})
  let result:unknown
  try{const data=body.payload;if(!data||typeof data!=='object'||Object.keys(data).some(k=>k!=='projectId'))throw new Error('Invalid navigation request')
   const projects=await ctx.researchRuntime.projects.list(),runs=[]
   for(const p of projects){if(data.projectId&&data.projectId!==p.id)continue;runs.push(...await ctx.researchRuntime.runs(p.id))}
   runs.sort((a,b)=>b.createdAt.localeCompare(a.createdAt))
   result={ok:true,value:{projects,runs,defaultProjectId:runs[0]?.projectId??projects[0]?.id??null,implementations:ctx.researchRuntime.implementations.list().map(m=>({id:m.id,version:m.version,mainPreset:m.mainPreset,ui:m.ui}))}}
  }catch(e){result={ok:false,error:{message:e instanceof Error?e.message:String(e)}}}
  return Response.json({type:'server-response',rpcId:body.rpcId,result})
 }})
})}
