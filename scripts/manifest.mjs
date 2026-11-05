import {readFile,readdir,stat,writeFile} from 'node:fs/promises'
import {join,relative} from 'node:path'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
export async function manifest(url,entries){
 const root=fileURLToPath(url),pkg=JSON.parse(await readFile(join(root,'package.json'),'utf8')),files={}
 const hash=data=>createHash('sha256').update(data).digest('hex')
 async function scan(path){const info=await stat(path);if(info.isDirectory()){for(const name of (await readdir(path)).sort())if(name!=='__pycache__')await scan(join(path,name))}else{const name=relative(root,path);if(name==='lib/implementation.json'||name.endsWith('.pyc'))return;files[name]=hash(await readFile(path))}}
 for(const entry of entries.sort())await scan(join(root,entry))
 const value={name:pkg.name,version:pkg.version,files};await writeFile(join(root,'lib/implementation.json'),JSON.stringify({schemaVersion:1,...value,digest:hash(JSON.stringify(value))},null,2)+'\n')
}
