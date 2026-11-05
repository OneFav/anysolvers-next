import { Context } from '@deepseek-ai/cordis'
import LocalSandbox from '@deepseek-ai/dsh-sandbox-local'
import { realpath } from 'node:fs/promises'
const chunks:Buffer[]=[]
for await(const chunk of process.stdin){chunks.push(chunk);if(chunks.reduce((n,b)=>n+b.length,0)>65536)throw Error('Confinement request too large')}
try{
 const request=JSON.parse(Buffer.concat(chunks).toString('utf8'))
 if(process.platform!=='darwin')throw Error('Native candidate evaluator currently supports macOS only; no unconfined fallback')
 if(!Array.isArray(request.argv)||!request.argv.length||request.argv.some((x:unknown)=>typeof x!=='string'))throw Error('Invalid exact argv')
 const provider=new LocalSandbox(new Context(),LocalSandbox.Config({}))
 const result=await provider.confine(request.argv,{mode:'read-only',workspaceRoot:request.workspace})
 if(result.enforcement!=='full'||result.argv[1]!=='-p')throw Error('Native candidate evaluator requires DSH Seatbelt full file enforcement')
 // DSH supplies write confinement. Candidate evaluation adds read/network/fork restrictions.
 const roots=[request.workspace,...request.pythonRoots,'/System','/usr','/Library/Apple'].filter((x:unknown)=>typeof x==='string')
 const actual=await Promise.all(roots.map(async(x:string)=>{try{return await realpath(x)}catch{return x}}))
 result.argv[2]+=' (deny network*) (deny process-fork) (deny file-read-data) (allow file-read-data (literal "/")) '+actual.map(x=>'(allow file-read-data (subpath '+JSON.stringify(x)+'))').join(' ')+' (allow file-read-data (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random") (subpath "/private/var/db/timezone") (literal "/private/etc/localtime"))'
 console.log(JSON.stringify({ok:true,...result,backend:'dsh-native-seatbelt',read_isolation:true,network_isolation:true,fork_allowed:false,memory_enforcement:'sampled-rss-watchdog',cpu_enforcement:'rlimit-cpu',temporary_writes:false}))
}catch(error){console.log(JSON.stringify({ok:false,error:String(error)}));process.exitCode=2}
