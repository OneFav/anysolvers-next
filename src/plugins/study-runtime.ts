import {read as readRuntime,runDirectory} from '@onefav/research-runtime'
import { Context, Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { readFile, readdir, stat, realpath } from 'node:fs/promises'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { RuntimeStore } from '../core/runtime-store.js'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { PythonBridge } from '../core/bridge.js'
import type { BridgeConfig } from '../core/bridge.js'
import { ProjectRegistry } from '../core/project-registry.js'
import type { ProjectBinding } from '../core/project-registry.js'
import { projectScope } from '../core/project-scope.js'
import { directoryPath, executablePath, implementationRoot } from '../core/config-paths.js'
import type { JsonObject, Scope, Studies } from '../services.js'

type RuntimeConfig = Omit<BridgeConfig,'root'> & { root?:string; stateRoot?:string }

export default class StudyRuntime extends Service implements Studies {
  static Config = z.object({
    pathBase:z.string().description('Base for relative framework paths; defaults to this installed package'),
    root:z.string().description('Optional legacy /anysolvers repository; new projects use the bundled engine'),
    stateRoot:z.string().description('Host cache and project index; defaults to ~/.dsh/anysolvers'),
    python:z.string().required(), workspaceRoot:z.string(),
    maxOutputBytes:z.number().min(1024).default(4*1024*1024),
    maxInputBytes:z.number().min(1024).default(2*1024*1024),
  })
  readonly baseRoot?:string
  readonly python:string
  readonly workspaceRoot?:string
  readonly projects:ProjectRegistry
  private readonly legacyProjects:ProjectRegistry
  private readonly bridge?:PythonBridge
  private readonly runtime:RuntimeStore
  private readonly roots=new Map<string,{studies:Studies;bridge:PythonBridge}>()
  constructor(ctx:Context,private readonly config:RuntimeConfig){
    super(ctx,'studies')
    const base=directoryPath(config.pathBase??'.',implementationRoot)
    config.pathBase=base
    if(config.root)this.baseRoot=config.root=directoryPath(config.root,base)
    this.python=config.python=executablePath(config.python,base)
    const storage=config.workspaceRoot??process.env.ANYSOLVERS_DSH_WORKSPACES
    if(storage)this.workspaceRoot=storage
    if(config.root)this.bridge=new PythonBridge({...config,root:config.root})
    const stateRoot=directoryPath(config.stateRoot??join(homedir(),'.dsh/anysolvers'),base)
    this.runtime=new RuntimeStore(stateRoot)
    this.legacyProjects=new ProjectRegistry(join(stateRoot,'projects'))
    this.projects=(ctx.get('researchRuntime') as any)?.projects??this.legacyProjects
    ctx.inject(['agents'],child=>child.on('agent/created',async({agent})=>{
      const header=agent.session.header
      if(header.origin!=='subagent'&&(this.ctx.get('researchRuntime')?.implementations.forPreset(header.agentPreset)?.id==='anysolvers'||header.agentPreset==='anysolvers-main')&&header.cwd){
        try{await this.projects.register(header.cwd)}catch{/* The command reports unavailable workspaces explicitly. */}
      }
    }))
    ctx.effect(()=>async()=>{await Promise.all([this.bridge?.close(),...[...this.roots.values()].map(v=>v.bridge.close())])})
  }
  get root():string{const root=projectScope.getStore()??this.baseRoot;if(!root)throw new Error('请选择研究工作区；旧入口需单独配置 legacy root。');return root}
  async [Service.init](){await stat(this.python);await this.runtime.prepare();if(this.projects!==this.legacyProjects){for(const project of await this.legacyProjects.list()){try{await this.projects.register(project.root)}catch{/* Unavailable legacy projects remain visible through the read adapter. */}}}}
  forRoot(root:string):Studies{
    let value=this.roots.get(root)
    if(!value){
      const bridge=new PythonBridge({...this.config,root})
      const ready=this.runtime.prepare().then(runtime=>{bridge.config.runtime=runtime})
      const studies:Studies={root,python:this.python,...(this.workspaceRoot?{workspaceRoot:this.workspaceRoot}:{}),
        call:async(operation,payload={},scope={},signal)=>{await ready;await this.verifyRun(root,operation,scope);return bridge.call(operation,payload,scope,signal)},
        status:async(runId,signal)=>{await ready;await this.verifyRun(root,'status',{runId});return bridge.call('status',{},{runId},signal)}}
      value={studies,bridge};this.roots.set(root,value)
    }
    return value.studies
  }
  private async verifyRun(root:string,operation:string,scope:Scope){
    if(!scope.runId)return
    const record=await readRuntime<{implementationId:string}>(join(runDirectory(root,scope.runId),'run.json'))
    if(record&&record.implementationId!=='anysolvers')throw new Error('This run belongs to '+record.implementationId+'; open its method view')
    const runtime=this.ctx.get('researchRuntime')
    if(record&&!runtime)throw new Error('The locked execution plugin is not loaded')
    // Explicit read-only AnySolvers adapter: Python still verifies original engine and pins.
    const readonly=new Set(['status','config','check','module','audit','evidence','terminal-status','terminal-evidence','terminal-artifact','ui-progress'])
    if(!readonly.has(operation)&&runtime)await runtime.verify(root,scope.runId,'anysolvers')
  }
  call(operation:string,payload:JsonObject={},scope:Scope={},signal?:AbortSignal):Promise<JsonObject>{
    const root=projectScope.getStore()
    if(root)return this.forRoot(root).call(operation,payload,scope,signal)
    if(!this.bridge)throw new Error('未绑定研究工作区。')
    return this.verifyRun(this.root,operation,scope).then(()=>this.bridge!.call(operation,payload,scope,signal))
  }
  status(runId:string,signal?:AbortSignal){return this.call('status',{},{runId},signal)}
  async projectBinding(agent:Agent){
    const current=await this.projects.binding(agent.id)
    const binding=current??await this.legacyProjects.binding(agent.id)
    if(binding&&!current){await this.projects.register(binding.root);await this.projects.bind(agent.id,binding)}
    if(binding?.implementationId&&binding.implementationId!=='anysolvers')throw new Error('Session is bound to '+binding.implementationId)
    if(binding&&agent.session.header.origin!=='subagent'&&await realpath(agent.session.header.cwd??'')!==binding.root)
      throw new Error('主会话工作区已改变，请创建新的 AnySolvers 主会话。')
    return binding
  }
  async bindProject(agent:Agent,binding:ProjectBinding){await this.projects.bind(agent.id,binding)}
  async withAgent<T>(agent:Agent,task:()=>Promise<T>):Promise<T>{
    const binding=await this.projectBinding(agent)
    const root=binding?.root??(!this.baseRoot&&agent.session.header.origin!=='subagent'?agent.session.header.cwd:undefined)
    return projectScope.run(root as string,task)
  }
  async projectCatalog(signal?:AbortSignal):Promise<JsonObject>{
    if(this.baseRoot){try{await this.projects.register(this.baseRoot)}catch{/* A missing legacy repo does not disable independent projects. */}}
    // Include all live main workspaces; the registry retains them after session closure.
    const agents=this.ctx.get('agents') as {list():Agent[]}|undefined
    for(const agent of agents?.list()??[]){
      const header=agent.session.header
      if(header.origin==='subagent'||header.agentPreset!=='anysolvers-main'||!header.cwd)continue
      try{await this.projects.register(header.cwd)}catch{/* An unavailable workspace is not a fallback to another project. */}
    }
    const projects:JsonObject[]=[]
    for(const project of new Map([...(await this.legacyProjects.list()),...(await this.projects.list())].map(project=>[project.id,project])).values()){
      signal?.throwIfAborted()
      let latestRun:string|null=null,latestAt:string|null=null,error:string|null=null
      try{
        await this.projects.get(project.id)
        const runs=join(project.root,'runs')
        for(const sid of (await readdir(runs)).sort()){
          if(!/^[a-z][a-z0-9-]{0,63}$/.test(sid)||!(await stat(join(runs,sid))).isDirectory())continue
          for(const number of (await readdir(join(runs,sid))).sort((a,b)=>Number(a)-Number(b))){
            if(!/^\d{3,}$/.test(number))continue
            const meta=await readRuntime<{implementationId:string}>(join(runs,sid,number,'.runtime/run.json'))
            if(meta&&meta.implementationId!=='anysolvers')continue
            const file=join(runs,sid,number,'state.json')
            if(!(await stat(file)).isFile())continue
            const state=JSON.parse(await readFile(file,'utf8'))
            if(typeof state.created_at==='string'&&(!latestAt||Date.parse(state.created_at)>=Date.parse(latestAt))){latestAt=state.created_at;latestRun=sid+'/'+number}
          }
        }
      }catch(e){error=e instanceof Error?e.message:String(e)}
      projects.push({...project,latestRun,latestAt,error})
    }
    projects.sort((a,b)=>(Date.parse(String(b.latestAt??''))||0)-(Date.parse(String(a.latestAt??''))||0)||String(a.name).localeCompare(String(b.name)))
    return {projects,defaultProjectId:projects[0]?.id??null}
  }
}
