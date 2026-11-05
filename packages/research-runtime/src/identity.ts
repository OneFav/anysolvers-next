import {readFile} from 'node:fs/promises'
import {join} from 'node:path'
import {sha,containedFile} from './storage.js'
import type {ImplementationIdentity} from './types.js'
export interface ArtifactManifest extends ImplementationIdentity {schemaVersion:1; files:Record<string,string>}
/** Recompute every packaged byte, even when the package version has not changed. */
export async function artifactIdentity(root:string):Promise<ImplementationIdentity>{
 const manifest=JSON.parse(await readFile(join(root,'lib/implementation.json'),'utf8')) as ArtifactManifest
 if(manifest.schemaVersion!==1||sha(JSON.stringify({name:manifest.name,version:manifest.version,files:manifest.files}))!==manifest.digest)throw new Error('Invalid implementation manifest')
 for(const [file,digest] of Object.entries(manifest.files))if(sha(await readFile(await containedFile(root,file)))!==digest)throw new Error(`Installed implementation changed: ${manifest.name}/${file}; rebuild for a new run or restore the locked artifact`)
 return {name:manifest.name,version:manifest.version,digest:manifest.digest}
}
