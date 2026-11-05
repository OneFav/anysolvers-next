import { Context, Service } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import { NativeWorkspaces } from '../core/workspace-native.js'
import { DockerWorkspaces } from '../core/workspace-docker.js'
import type { JsonObject, ResearchBinding, StudySandbox } from '../services.js'

interface Config {
  backend: 'dsh-native' | 'docker'; image: string; docker: string; python: string;
  maxFileBytes: number; memoryMb: number; pids: number; cpus: number;
}

/** A file-only researcher execution world, independent of Python evaluation containers. */
export default class StudySandboxService extends Service implements StudySandbox {
  static inject = ['studies']
  static Config = Schema.object({
    backend: Schema.union(['dsh-native','docker']).default('dsh-native'), image: Schema.string().default(''), docker: Schema.string().default('docker'), python: Schema.string().default('python3'),
    maxFileBytes: Schema.number().min(1).max(2 * 1024 * 1024).default(1024 * 1024),
    memoryMb: Schema.number().min(32).max(4096).default(128),
    pids: Schema.number().min(4).max(256).default(32), cpus: Schema.number().min(0.1).max(4).default(1),
  })
  private readonly roots = new Map<string, DockerWorkspaces | NativeWorkspaces>()
  constructor(ctx: Context, private readonly config: Config) {
    super(ctx, 'studySandbox')
    ctx.effect(() => async () => { await Promise.all([...this.roots.values()].map(v=>v.dispose())) })
  }
  forRoot(root:string):DockerWorkspaces | NativeWorkspaces {
    let value=this.roots.get(root)
    if(!value){const options={root,...this.config,python:this.ctx.studies.python,...(this.ctx.studies.workspaceRoot?{workspaceRoot:this.ctx.studies.workspaceRoot}:{})};value=this.config.backend==='docker'?new DockerWorkspaces(options):new NativeWorkspaces(this.ctx,options);this.roots.set(root,value)}
    return value
  }
  private current():DockerWorkspaces | NativeWorkspaces {return this.forRoot(this.ctx.studies.root)}
  prepare(runId: string, sessionId: string): Promise<string> { return this.current().prepare(runId, sessionId) }
  read(binding: ResearchBinding, path: string): Promise<string> { return this.current().read(binding, path) }
  write(binding: ResearchBinding, path: string, content: string): Promise<void> { return this.current().write(binding, path, content) }
  list(binding: ResearchBinding, path?: string): Promise<string[]> { return this.current().list(binding, path) }
  release(runId: string, sessionId: string): Promise<void> { return this.current().release(runId, sessionId) }
  doctor(): Promise<JsonObject> { return this.current().doctor() }
}
