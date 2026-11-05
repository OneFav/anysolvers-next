#!/usr/bin/env node
/** Add only the IM panel bundle; preserve every existing profile setting. */
import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {join,resolve} from 'node:path'
import {spawnSync} from 'node:child_process'
if(!process.argv[2])throw Error('Usage: node scripts/install-desktop-im.mjs PROFILE; relative to the invocation directory')
const profile=resolve(process.argv[2])
const source=fileURLToPath(new URL('../packages/anysolvers-im',import.meta.url))
const file=join(profile,'package.json'),before=JSON.parse(await readFile(file,'utf8'))
const backup=join(profile,'.anysolvers-im-backups',new Date().toISOString().replaceAll(':','-'))
await mkdir(backup,{recursive:true})
for(const name of ['package.json','pnpm-lock.yaml','cordis.patch.yml','cordis.yml']){
 try{await copyFile(join(profile,name),join(backup,name))}catch(e){if(e.code!=='ENOENT')throw e}
}
const result=spawnSync('pnpm',['add','--offline','--ignore-scripts','@onefav/anysolvers-im@link:'+source],{cwd:profile,stdio:'inherit'})
if(result.status!==0)throw Error('Local dependency install failed; backup: '+backup)
const pkg=JSON.parse(await readFile(file,'utf8'))
for(const [name,value] of Object.entries(before.dependencies??{}))if(pkg.dependencies[name]!==value)throw Error('Existing dependency changed: '+name)
pkg.dsh??={};pkg.dsh.profile??={};pkg.dsh.profile.bundles=[...new Set([...(pkg.dsh.profile.bundles??[]),'@onefav/anysolvers-im'])]
await writeFile(file,JSON.stringify(pkg,null,2)+'\n')
console.log(JSON.stringify({profile,added:'AnySolvers-IM',source,backup,restartRequired:true,startedResearch:false},null,2))
