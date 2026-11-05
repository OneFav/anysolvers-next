import {createHash, randomUUID} from 'node:crypto'
import {mkdir, open, readFile, rename} from 'node:fs/promises'
import {dirname, join} from 'node:path'

export async function read<T = any>(path: string): Promise<T | undefined> {
  try { return JSON.parse(await readFile(path, 'utf8')) as T }
  catch(e) { if ((e as NodeJS.ErrnoException).code === 'ENOENT') return; throw e }
}
export async function atomic(path: string, value: unknown) {
  await mkdir(dirname(path), {recursive:true, mode:0o700})
  const temp=path+'.'+randomUUID()+'.tmp', f=await open(temp,'wx',0o600)
  try { await f.writeFile(JSON.stringify(value)+'\n'); await f.sync() } finally { await f.close() }
  await rename(temp,path)
  if(process.platform!=='win32') { const dir=await open(dirname(path),'r'); try {await dir.sync()} finally {await dir.close()} }
}
export function canonical(value:any):string {
  if(Array.isArray(value)) return '['+value.map(canonical).join(',')+']'
  if(value && typeof value==='object') return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}'
  return JSON.stringify(value)
}
export const hash=(value:unknown)=>createHash('sha256').update(canonical(value)).digest('hex')
export const sleep=(ms:number,signal?:AbortSignal)=>new Promise<void>((resolve,reject)=>{
  signal?.throwIfAborted()
  const stop=()=>{clearTimeout(timer);reject(signal?.reason??new Error('Aborted'))}
  const timer=setTimeout(()=>{signal?.removeEventListener('abort',stop);resolve()},ms)
  signal?.addEventListener('abort',stop,{once:true})
})

export class ControlClient {
  constructor(readonly url:string, readonly token:string, readonly attempt?:string) {
    const u=new URL(url)
    if(u.protocol!=='https:' && !['127.0.0.1','localhost','[::1]'].includes(u.hostname)) throw new Error('Remote Router requires HTTPS')
  }
  async request(path:string,body?:any):Promise<any> {
    const response=await fetch(this.url+path,{method:body===undefined?'GET':'POST',
      headers:{Authorization:'Bearer '+this.token,'Content-Type':'application/json'},
      ...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(15000)})
    const value=await response.json() as any
    if(!response.ok) throw new Error('Router '+response.status+': '+String(value.error))
    return value.value??value
  }
  worker(path:string,body:any={}) {return this.request('/api/worker/'+path,{attempt_id:this.attempt,...body})}
}

/** Side effects are logged before execution and results before remote acknowledgement. */
export class ToolJournal {
  constructor(readonly root:string,readonly client:ControlClient) {}
  path(id:string){return join(this.root,'tools',hash(id)+'.json')}
  async execute(id:string,name:string,args:unknown,next:()=>Promise<any>):Promise<any> {
    const path=this.path(id), fingerprint=hash([name,args]), saved=await read(path)
    if(saved && saved.fingerprint!==fingerprint) throw new Error('Tool identity collision')
    if(saved?.state==='completed') {
      // A recovered attempt may already have reconciled this result centrally.
      const central=await this.client.worker('tool-begin',{operation_id:id,name,arguments:args})
      if(central.state!=='completed') await this.client.worker('tool-finish',{operation_id:id,result:saved.result})
      return saved.result
    }
    const central=await this.client.worker('tool-begin',{operation_id:id,name,arguments:args})
    if(central.state==='completed') {await atomic(path,{id,name,arguments:args,fingerprint,state:'completed',result:central.result});return central.result}
    if(saved) throw new Error('Uncertain prior execution: reconcile the original operation before continuing')
    await atomic(path,{id,name,arguments:args,fingerprint,state:'started',attempt_id:this.client.attempt})
    const result=await next() // If this throws, the operation intentionally remains unresolved.
    await atomic(path,{id,name,arguments:args,fingerprint,state:'completed',attempt_id:this.client.attempt,result})
    await this.client.worker('tool-finish',{operation_id:id,result})
    return result
  }
}
