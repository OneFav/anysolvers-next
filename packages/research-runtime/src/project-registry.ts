import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, readdir, realpath, rename, writeFile, stat } from 'node:fs/promises'
import { basename, join, resolve } from 'node:path'
export interface Project { id: string; root: string; name: string }
export interface ProjectBinding { projectId: string; root: string; runId: string; implementationId?: string }
const hash = (value:string) => createHash('sha256').update(value).digest('hex')
const missing = (error:unknown) => error instanceof Error && 'code' in error && error.code === 'ENOENT'
/** Owner metadata only; research data remains in each canonical workspace. */
export class ProjectRegistry {
  readonly directory:string
  readonly workspaceBase:string
  constructor(directory:string, workspaceBase=process.cwd()) {
    this.workspaceBase=resolve(workspaceBase)
    this.directory=resolve(this.workspaceBase,directory)
  }
  private async write(path:string,value:unknown) {
    await mkdir(this.directory,{recursive:true,mode:0o700})
    const temporary=join(this.directory,'.'+randomUUID())
    await writeFile(temporary,JSON.stringify(value),{flag:'wx',mode:0o600})
    await rename(temporary,path)
  }
  async register(root:string):Promise<Project> {
    root=await realpath(resolve(this.workspaceBase,root))
    if(!(await stat(root)).isDirectory())throw new Error('工作区不是目录。')
    const project={id:hash(root),root,name:basename(root)}
    await this.write(join(this.directory,'project-'+project.id+'.json'),project)
    return project
  }
  async get(id:string):Promise<Project> {
    if(!/^[a-f0-9]{64}$/.test(id))throw new Error('Invalid project ID')
    const project=JSON.parse(await readFile(join(this.directory,'project-'+id+'.json'),'utf8')) as Project
    if(project.id!==id||hash(project.root)!==id||await realpath(project.root)!==project.root)throw new Error('Project workspace changed; select it again')
    return project
  }
  async list():Promise<Project[]> {
    let files:string[]
    try{files=await readdir(this.directory)}catch(error){if(missing(error))return [];throw error}
    const values:Project[]=[]
    for(const file of files.filter(f=>/^project-[a-f0-9]{64}\.json$/.test(f))){
      // Keep unavailable projects visible; reads of their contents still fail closed.
      const value=JSON.parse(await readFile(join(this.directory,file),'utf8')) as Project
      if(hash(value.root)!==value.id)throw new Error('Invalid project registry')
      values.push(value)
    }
    return values
  }
  async bind(session:string,binding:ProjectBinding) {
    const existing = await this.binding(session)
    if (existing && JSON.stringify(existing) !== JSON.stringify(binding)) throw new Error('Native session is already bound to a different run or implementation')
    const project = await this.get(binding.projectId)
    if (project.root !== binding.root) throw new Error('Project binding root mismatch')
    await this.write(join(this.directory,'session-'+hash(session)+'.json'),{session,...binding})
  }
  async binding(session:string):Promise<ProjectBinding|undefined> {
    let raw:string
    try{raw=await readFile(join(this.directory,'session-'+hash(session)+'.json'),'utf8')}
    catch(error){if(missing(error))return undefined;throw error}
    const value=JSON.parse(raw)
    if(value.session!==session)throw new Error('Session binding mismatch')
    const project=await this.get(value.projectId)
    if(project.root!==value.root)throw new Error('Session workspace mismatch')
    return {projectId:project.id,root:project.root,runId:value.runId,...(value.implementationId?{implementationId:value.implementationId}:{})}
  }
}
