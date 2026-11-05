/** Separate owner-only DSH UI. No models, presets, credentials or agent tools. */
import {spawn} from 'node:child_process'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {resolve,join} from 'node:path'
export const name='anysolvers-im-ui'
const root=fileURLToPath(new URL('../../',import.meta.url))
const methods=new Set(['projects','catalog','configuration','progress','artifact','save','start','deployment','service-start','service-stop'])
export async function handle(endpoint,payload,signal,peer,execute=bridge){
  try{
    if(peer?.principal?.kind!=='owner')throw Error('Only the host owner can use AnySolvers-IM')
    const method=endpoint.startsWith('anysolvers-im/')?endpoint.slice(14):''
    if(!methods.has(method)||!payload||typeof payload!=='object'||Array.isArray(payload))throw Error('Invalid AnySolvers-IM request')
    return {ok:true,value:await execute({method,payload},signal)}
  }catch(error){return {ok:false,error:{code:'ANYSOLVERS_IM',message:error.message??'AnySolvers-IM request failed',details:{}}}}
}
export async function bridge(request,signal,instance=process.env.ANYSOLVERS_INSTANCE){
  const configPath=instance?join(resolve(instance),'config/settings.json'):join(root,'config/settings.json')
  let config
  try{config=JSON.parse(await readFile(configPath,'utf8'))}catch{throw Error('请让安装 agent 按仓库 INSTALL.md 完成部署，然后设置插件 instance 为实例目录。')}
  const source=config.deployment?.source??root
  const base=resolve(configPath,'..',config.path_base??'..')
  const configured=config.worker_host?.python
  const python=configured?(configured.includes('/')||configured.includes('\\')?resolve(base,configured):configured):join(source,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python')
  return new Promise((accept,reject)=>{
    const input=JSON.stringify(request)
    if(Buffer.byteLength(input)>64*1024)return reject(Error('UI request too large'))
    // Preserve venv/bin/python; resolving its symlink would discard the venv.
    const child=spawn(python,['-B','-m','controlplane.desktop_ui','--config',configPath],{cwd:source,env:{PATH:process.env.PATH??'/usr/bin:/bin',HOME:process.env.HOME??root,LANG:'en_US.UTF-8',PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']})
    let output='',size=0,failure
    const stop=()=>{failure=Error('Request interrupted; refresh before retrying a launch');child.kill('SIGTERM')}
    const timer=setTimeout(()=>{failure=Error('UI request timed out; refresh before retrying a launch');child.kill('SIGTERM')},60000)
    signal?.addEventListener('abort',stop,{once:true});if(signal?.aborted)stop()
    child.stdout.on('data',part=>{size+=part.length;if(size>20*1024*1024){failure=Error('UI response too large');child.kill('SIGTERM')}else output+=part})
    child.stderr.on('data',()=>{})
    child.on('error',error=>{failure=error})
    child.on('close',()=>{clearTimeout(timer);signal?.removeEventListener('abort',stop);if(failure)return reject(failure)
      try{const reply=JSON.parse(output);if(!reply.ok)throw Error(reply.error?.message??'UI bridge failed');accept(reply.value)}catch(e){reject(e)}})
    child.stdin.on('error',()=>{});child.stdin.end(input)
  })
}
export function apply(ctx,config={}){
  ctx.inject(['connection'],child=>{
    const connection=child.get('connection')
    const disposers=[...methods].map(method=>{const endpoint='anysolvers-im/'+method;return connection.fetch.register({path:'/api/'+endpoint,methods:['POST'],requestBody:'buffered',async fetch(request){
      let body;try{body=await request.json()}catch{return new Response('Invalid JSON',{status:400})}
      if(body?.type!=='client-request'||typeof body.rpcId!=='string'||body.method!==endpoint)return new Response('Invalid request envelope',{status:400})
      const result=await handle(endpoint,body.payload,request.signal,connection.browserPeer,(input,signal)=>bridge(input,signal,config.instance))
      return Response.json({type:'server-response',rpcId:body.rpcId,result})
    }})})
    return async()=>{await Promise.all(disposers.map(dispose=>dispose()))}
  })
}
