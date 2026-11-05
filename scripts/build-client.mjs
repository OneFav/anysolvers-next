import ts from 'typescript'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
// DSH browser modules register factories; React is supplied by the native client baseline.
const source = await readFile(new URL('../src/ui-study/client/index.tsx',import.meta.url),'utf8')
const output = ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React},fileName:'index.tsx'})
await writeFile(new URL('../lib/client.js',import.meta.url),`window.__ModuleLoader__.load({id:"@onefav/anysolvers",factory:(require)=>{const module={exports:{}};const exports=module.exports;\n${output.outputText}\nreturn module.exports;}});\n`)

await mkdir(new URL('../packages/anysolvers-im/lib/',import.meta.url),{recursive:true})
await writeFile(new URL('../packages/anysolvers-im/lib/client.js',import.meta.url),`window.__ModuleLoader__.load({id:"@onefav/anysolvers-im",factory:(require)=>{const module={exports:{}};const exports=module.exports;\n${output.outputText}\nreturn {...module.exports,inject:['slots','connection','layout'],apply:ctx=>module.exports.applyPanel(ctx,{id:'anysolvers-im',label:'AnySolvers-IM',im:true})};}});\n`)
