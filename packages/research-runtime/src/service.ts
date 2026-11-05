import {Context,Service} from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import {homedir} from 'node:os'
import {join,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import {mkdir,readdir,realpath} from 'node:fs/promises'
import {randomUUID} from 'node:crypto'
import {ImplementationRegistry} from './registry.js'
import {ProjectRegistry} from './project-registry.js'
import {artifactIdentity} from './identity.js'
import {atomic,exclusive,read,runDirectory,sha,safeDirectory} from './storage.js'
import type {Invocation,Preflight,ResearchImplementation,RunIdentity,ImplementationIdentity} from './types.js'
export interface ImplementationLock {schemaVersion:1;implementationId:string;runtime:ImplementationIdentity;method:ImplementationIdentity}
export default class ResearchRuntime extends Service {
 static Config=Schema.object({pathBase:Schema.string(),stateRoot:Schema.string()})
 readonly implementations=new ImplementationRegistry()
 readonly projects:ProjectRegistry
 readonly packageRoot=fileURLToPath(new URL('../',import.meta.url))
 constructor(ctx:Context,config:{pathBase?:string;stateRoot?:string}={}){super(ctx,'researchRuntime')
  const expand=(value:string)=>value==='~'?homedir():value.startsWith('~/')?join(homedir(),value.slice(2)):value
  const base=resolve(this.packageRoot,expand(config.pathBase??'.'))
  const stateRoot=resolve(base,expand(config.stateRoot??join(homedir(),'.dsh/research-runtime')))
  this.projects=new ProjectRegistry(join(stateRoot,'projects'),base)
  ctx.inject(['agents'],child=>child.on('agent/created',async({agent})=>{if(agent.session.header.origin!=='subagent'&&this.implementations.forPreset(agent.session.header.agentPreset)&&agent.session.header.cwd)await this.projects.register(agent.session.header.cwd)}))
 }
 register(method:ResearchImplementation){return this.implementations.register(method)}
 method(input:Invocation){const method=this.implementations.forPreset(input.agent.session.header.agentPreset);if(!method||input.agent.session.header.origin==='subagent')throw new Error('Select a registered research main preset');return method}
 async preflight(input:Invocation):Promise<Preflight>{const method=this.method(input),binding=await this.projects.binding(input.agent.id);if(binding&&(binding.root!==await realpath(input.root)||binding.implementationId!==method.id))throw new Error('Bound session implementation or workspace changed');return method.preflight(input)}
 async start(input:Invocation,checked:Preflight){
  const method=this.method(input),project=await this.projects.register(input.root)
  if(!checked.ready)throw new Error('Preflight did not pass')
  const existing=await this.projects.binding(input.agent.id);if(existing)throw new Error(`Session is already bound to ${existing.implementationId??'legacy'}/${existing.runId}; continue its original run`)
  return exclusive(join(project.root,'.anysolvers/runtime/start.lock'),async()=>{
   const claims=join(project.root,'.anysolvers/runtime/launches');await mkdir(claims,{recursive:true})
   for(const file of await readdir(claims)){const value=await read<{state:string;requestId:string}>(join(claims,file));if(value&&value.state!=='running')throw new Error(`Unresolved launch ${value.requestId}; reconcile without replay`)}
   const current=await method.preflight({...input,root:project.root});if(!current.ready||current.revision!==checked.revision)throw new Error('Initialization or configuration changed after preflight')
   const lock:ImplementationLock={schemaVersion:1,implementationId:method.id,runtime:await artifactIdentity(this.packageRoot),method:await artifactIdentity(method.packageRoot)}
   await method.validateEnvironment?.({...input,root:project.root})
   const requestId=randomUUID(),claim=join(claims,requestId+'.json'),intent={schemaVersion:1,requestId,projectId:project.id,implementationId:method.id,sessionId:input.agent.id,state:'starting',createdAt:new Date().toISOString(),lock}
   await atomic(claim,intent)
   const allocate=async(target:string)=>{if(!/^[a-z][a-z0-9-]{0,63}$/.test(target))throw new Error('Invalid target');const base=join(project.root,'runs',target);await safeDirectory(base,true);const max=Math.max(0,...(await readdir(base)).filter(v=>/^\d{3,}$/.test(v)).map(Number));const runId=target+'/'+String(max+1).padStart(3,'0');await mkdir(join(project.root,'runs',runId));return runId}
   const result=await method.start({...input,root:project.root,requestId,checked:current,allocate})
   const directory=runDirectory(project.root,result.runId)
   await safeDirectory(join(project.root,'runs',result.runId))
   const record:RunIdentity={schemaVersion:1,projectId:project.id,root:project.root,runId:result.runId,implementationId:method.id,createdAt:intent.createdAt,requestId,state:'running',methodRef:result.methodRef??null,issue:null}
   if(await read(join(directory,'run.json')))throw new Error('Method returned an existing run')
   await atomic(join(directory,'implementation.lock.json'),lock);await atomic(join(directory,'run.json'),record)
   await this.projects.bind(input.agent.id,{projectId:project.id,root:project.root,runId:result.runId,implementationId:method.id})
   await atomic(claim,{...intent,state:'running',runId:result.runId})
   return {...result,projectId:project.id,implementationId:method.id}
  })
 }
 async verify(root:string,runId:string,implementationId?:string){
  const dir=runDirectory(root,runId),record=await read<RunIdentity>(join(dir,'run.json'));if(!record){if(await read(join(dir,'implementation.lock.json')))throw new Error('Interrupted run registration requires reconciliation');return undefined}
  if(implementationId&&record.implementationId!==implementationId)throw new Error(`Run belongs to ${record.implementationId}, not ${implementationId}`)
  if(record.schemaVersion!==1||record.root!==await realpath(root)||record.projectId!==sha(record.root)||record.runId!==runId)throw new Error('Run identity mismatch')
  const method=this.implementations.get(record.implementationId),lock=await read<ImplementationLock>(join(dir,'implementation.lock.json'))
  if(!lock||lock.implementationId!==method.id)throw new Error('Missing or invalid implementation lock')
  const current={schemaVersion:1,implementationId:method.id,runtime:await artifactIdentity(this.packageRoot),method:await artifactIdentity(method.packageRoot)}
  if(JSON.stringify(current)!==JSON.stringify(lock))throw new Error(`Locked implementation unavailable: ${lock.method.name}@${lock.method.version} ${lock.method.digest}; restore matching installation to resume`)
  return record
 }
 async runs(projectId:string){const project=await this.projects.get(projectId),values:any[]=[];let studies:string[]=[];try{studies=await readdir(join(project.root,'runs'))}catch{return []}
  for(const study of studies){if(!/^[a-z][a-z0-9-]{0,63}$/.test(study))continue;await safeDirectory(join(project.root,'runs',study));for(const number of await readdir(join(project.root,'runs',study))){if(!/^\d{3,}$/.test(number))continue;const record=await read<RunIdentity>(join(runDirectory(project.root,study+'/'+number),'run.json'));if(record){if(record.schemaVersion!==1||record.projectId!==project.id||record.root!==project.root||record.runId!==study+'/'+number)throw new Error('Run identity mismatch');const method=this.implementations.list().find(m=>m.id===record.implementationId);values.push({...record,ui:method?.ui??null,available:!!method,version:await read(join(runDirectory(project.root,record.runId),'implementation.lock.json'))})}}}
  return values.sort((a,b)=>b.createdAt.localeCompare(a.createdAt))
 }
 async status(root:string,runId:string){const record=await this.verify(root,runId);if(!record)throw new Error('Legacy run requires its explicit read-only method adapter');const method=this.implementations.get(record.implementationId);const status=await method.status(root,runId);const jobs=join(runDirectory(root,runId),'jobs');let unresolved=false;try{for(const file of await readdir(jobs)){if(!file.endsWith('.json')||file.endsWith('.result.json'))continue;const job=await read<{state:string}>(join(jobs,file));if(job&&!['completed','failed'].includes(job.state))unresolved=true}}catch(error){if(!(error instanceof Error&&'code'in error&&error.code==='ENOENT'))throw error}
  return {record,complete:status.complete&&!unresolved,unresolved,progress:status.progress}
 }
}
declare module '@deepseek-ai/cordis' {interface Context {researchRuntime:ResearchRuntime}}
