import {cp,mkdir,readFile,rename,rm,stat} from 'node:fs/promises'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {randomUUID} from 'node:crypto'
export interface RuntimeDispatch {script:string;store:string;engineId:string}
/** Persist immutable installed versions outside both the plugin checkout and research projects. */
export class RuntimeStore {
  private ready?:Promise<RuntimeDispatch>
  constructor(readonly stateRoot:string){}
  prepare():Promise<RuntimeDispatch>{return this.ready??=this.install()}
  private async install():Promise<RuntimeDispatch>{
    const packaged=fileURLToPath(new URL('../runtime/',import.meta.url))
    const current=JSON.parse(await readFile(join(packaged,'current.json'),'utf8'))
    if(!/^[a-f0-9]{64}$/.test(current.engine_id))throw new Error('Invalid packaged engine identity')
    const store=join(this.stateRoot,'engines'),destination=join(store,current.engine_id)
    await mkdir(store,{recursive:true,mode:0o700})
    try{await stat(destination)}catch(error){
      if(!(error instanceof Error&&'code'in error&&error.code==='ENOENT'))throw error
      const staging=join(store,'.install-'+randomUUID())
      try{
        await cp(join(packaged,'engines',current.engine_id),staging,{recursive:true,errorOnExist:true,force:false})
        try{await rename(staging,destination)}catch(error){if(!(error instanceof Error&&'code'in error&&['EEXIST','ENOTEMPTY'].includes(String(error.code))))throw error}
      }finally{await rm(staging,{recursive:true,force:true})}
    }
    // Python verifies all installed bytes on every invocation; never silently repairs a changed cache.
    return {script:join(packaged,'dispatch.py'),store,engineId:current.engine_id}
  }
}
