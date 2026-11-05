import ts from 'typescript'
import {readFile,writeFile,readdir,stat} from 'node:fs/promises'
import {join,relative} from 'node:path'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
const root=fileURLToPath(new URL('../',import.meta.url)),source=await readFile(join(root,'src/client.tsx'),'utf8')
const output=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React},fileName:'client.tsx'})
await writeFile(join(root,'lib/client.js'),`window.__ModuleLoader__.load({id:"@onefav/research-runtime",factory:(require)=>{const module={exports:{}};const exports=module.exports;${output.outputText};return module.exports;}});\n`)
const hash=b=>createHash('sha256').update(b).digest('hex'),files={},pkg=JSON.parse(await readFile(join(root,'package.json'),'utf8'))
async function scan(path){if((await stat(path)).isDirectory()){for(const f of (await readdir(path)).sort())if(f!=='__pycache__')await scan(join(path,f))}else{const name=relative(root,path);if(name!=='lib/implementation.json'&&!name.endsWith('.pyc'))files[name]=hash(await readFile(path))}}
for(const entry of ['lib','package.json','python'])await scan(join(root,entry))
const value={name:pkg.name,version:pkg.version,files};await writeFile(join(root,'lib/implementation.json'),JSON.stringify({schemaVersion:1,...value,digest:hash(JSON.stringify(value))},null,2)+'\n')
