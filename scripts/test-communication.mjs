// Compile and test in a temporary tree; never overwrite a deployed lib/ or run.
import {mkdtemp,cp,mkdir,symlink,writeFile,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {spawnSync} from 'node:child_process'

const root=fileURLToPath(new URL('../',import.meta.url))
const temporary=await mkdtemp(join(tmpdir(),'anysolvers-communication-test-'))
const tests=['communication.test.mjs','next-durable.test.mjs','next-recovery.test.mjs','policy.test.mjs']
function run(args,cwd){
 const result=spawnSync(process.execPath,args,{cwd,stdio:'inherit'})
 if(result.error)throw result.error
 if(result.status!==0)throw new Error(`Test command failed (${result.status??result.signal})`)
}
try {
 await writeFile(join(temporary,'package.json'),JSON.stringify({type:'module'}))
 await symlink(join(root,'node_modules'),join(temporary,'node_modules'),process.platform==='win32'?'junction':'dir')
 run([join(root,'node_modules/typescript/bin/tsc'),'-p',join(root,'tsconfig.json'),'--outDir',join(temporary,'lib')],root)
 await mkdir(join(temporary,'tests'))
 for(const test of tests)await cp(join(root,'tests',test),join(temporary,'tests',test))
 await cp(join(root,'tests/fixtures/communication'),join(temporary,'tests/fixtures/communication'),{recursive:true})
 run(['--test',...tests.map(test=>join(temporary,'tests',test))],temporary)
} finally {
 await rm(temporary,{recursive:true,force:true})
}
