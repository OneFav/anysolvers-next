import { realpath } from 'node:fs/promises'
import LocalSandbox from '@deepseek-ai/dsh-sandbox-local'
import { Context } from '@deepseek-ai/cordis'
import { classifyRunnerFailure, type SandboxProvider } from '@deepseek-ai/dsh-sandbox'
import { prepareWorkspace, workspacePath, WORKSPACE_PROGRAM, runCommand } from './workspace-docker.js'
import type { DockerWorkspaceOptions } from './workspace-docker.js'
import type { JsonObject, ResearchBinding } from '../services.js'

/** Trusted file operations under DSH's native workspace-write policy. */
export class NativeWorkspaces {
  private readonly sandbox: SandboxProvider
  constructor(ctx: Context, private readonly options: DockerWorkspaceOptions) {
    this.sandbox = ctx.get('sandbox') ?? new LocalSandbox(ctx, LocalSandbox.Config({}))
  }
  async prepare(runId:string, sessionId:string):Promise<string> {
    const path=await prepareWorkspace(this.options.root,runId,sessionId,this.options.workspaceRoot)
    await this.operation({runId,sessionId,workspace:path,status:'active'} as ResearchBinding,'list','.')
    return path
  }
  private async operation(binding:ResearchBinding,operation:string,path:string,content?:string):Promise<JsonObject> {
    const expected=workspacePath(await realpath(this.options.root),binding.runId,binding.sessionId,this.options.workspaceRoot)
    if(binding.workspace!==expected || !['active','closing'].includes(binding.status))throw Error('Workspace is not bound to an active research session')
    const confined=await this.sandbox.confine([this.options.python,'-I','-B','-c',WORKSPACE_PROGRAM,expected,String(this.options.maxFileBytes)],{mode:'workspace-write',workspaceRoot:expected})
    if(confined.enforcement!=='full')throw Error('Native workspace requires full file-write enforcement on this host')
    const result=await runCommand(confined.argv[0]!,confined.argv.slice(1),JSON.stringify({operation,path,...content===undefined?{}:{content}}))
    if(result.code!==0 && classifyRunnerFailure(result.code,result.stderr,confined.runnerFailureRules))throw Error('DSH native sandbox unavailable: '+result.stderr.slice(0,500))
    let reply:any
    try{reply=JSON.parse(result.stdout)}catch{throw Error('DSH native workspace returned no valid response: '+result.stderr.slice(0,500))}
    if(result.code!==0||reply.ok!==true)throw Error('Workspace operation rejected: '+String(reply.error??'provider failure'))
    return reply.value as JsonObject
  }
  async read(binding:ResearchBinding,path:string):Promise<string>{const v=await this.operation(binding,'read',path);if(typeof v.content!=='string')throw Error('Invalid file content');return v.content}
  async write(binding:ResearchBinding,path:string,content:string):Promise<void>{await this.operation(binding,'write',path,content)}
  async list(binding:ResearchBinding,path='.'):Promise<string[]>{const v=await this.operation(binding,'list',path);if(!Array.isArray(v.paths)||v.paths.some(x=>typeof x!=='string'))throw Error('Invalid file list');return v.paths as string[]}
  async release(_runId:string,_sessionId:string):Promise<void>{}
  async dispose():Promise<void>{}
  async doctor():Promise<JsonObject>{const v=await this.sandbox.confine([this.options.python,'-I','-B','-c','print("ok")'],{mode:'read-only',workspaceRoot:this.options.root});const r=await runCommand(v.argv[0]!,v.argv.slice(1));return {ok:r.code===0&&r.stdout.trim()==='ok',backend:'dsh-native',file_write_enforcement:v.enforcement,read_isolation:false,network_isolation:false}}
}
