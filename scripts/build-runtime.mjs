import {mkdir,readFile,readdir,copyFile,writeFile,rm} from 'node:fs/promises'
import {join,relative,dirname} from 'node:path'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
const root=fileURLToPath(new URL('../',import.meta.url)),source=join(root,'runtime')
const version=JSON.parse(await readFile(join(root,'package.json'),'utf8')).version
async function scan(directory){const names=[];for(const item of await readdir(directory,{withFileTypes:true})){if(item.name==='__pycache__'||item.name.startsWith('.'))continue;const path=join(directory,item.name);if(item.isSymbolicLink())throw Error('Runtime cannot contain symlinks');if(item.isDirectory())names.push(...await scan(path));else if(item.isFile()&&!path.endsWith('.pyc'))names.push(path)}return names}
const files={};for(const path of (await scan(source)).sort())files[relative(source,path)]=createHash('sha256').update(await readFile(path)).digest('hex')
const engine_id=createHash('sha256').update(JSON.stringify({files,version})).digest('hex')
const output=join(root,'lib/runtime/engines',engine_id)
await mkdir(output,{recursive:true})
for(const path of Object.keys(files)){await mkdir(dirname(join(output,path)),{recursive:true});await copyFile(join(source,path),join(output,path))}
await writeFile(join(output,'manifest.json'),JSON.stringify({schema_version:1,engine_id,files,version})+'\n')
await writeFile(join(root,'lib/runtime/current.json'),JSON.stringify({engine_id,version})+'\n')
await copyFile(join(root,'scripts/runtime-dispatch.py'),join(root,'lib/runtime/dispatch.py'))
console.log(`Bundled AnySolvers engine ${engine_id.slice(0,12)} (${Object.keys(files).length} files)`)

// Pinned running versions live in independent snapshots; discard stale development build output.
for(const old of await readdir(join(root,'lib/runtime/engines')))if(old!==engine_id)await rm(join(root,'lib/runtime/engines',old),{recursive:true,force:true})
