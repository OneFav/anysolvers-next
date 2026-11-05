import * as React from 'react'
import {useEffect,useState} from 'react'
export function apply(ctx:any){
 function Panel(){const [data,setData]=useState<any>(),[project,setProject]=useState(''),[error,setError]=useState('')
  useEffect(()=>{const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;const refresh=async()=>{try{const result=await ctx.connection.rpc.call('/api','research-runtime/navigation',{},abort.signal);if(!result.ok)throw new Error(result.error.message);if(!abort.signal.aborted){setData(result.value);setProject(id=>id||result.value.defaultProjectId||'')}}catch(e){if(!abort.signal.aborted)setError(String(e))}finally{if(!abort.signal.aborted)timer=setTimeout(refresh,5000)}};void refresh();return()=>{abort.abort();clearTimeout(timer)}},[])
  const open=(run:any)=>{const selection={implementationId:run.implementationId,projectId:run.projectId,root:run.root,runId:run.runId};sessionStorage.setItem('research-runtime-selection',JSON.stringify(selection));window.dispatchEvent(new CustomEvent('research-runtime-selection',{detail:selection}));ctx.layout.selectPanel(run.ui.panel)}
  return <div style={{padding:24,maxWidth:1000,margin:'auto'}}><h2>研究工作台</h2><p>按项目与运行选择研究实现。历史运行使用其冻结配置。</p><select aria-label="研究项目" value={project} onChange={e=>setProject(e.target.value)}>{data?.projects.map((p:any)=><option key={p.id} value={p.id}>{p.name} · {p.root}</option>)}</select>{error&&<p role="alert">{error}</p>}<div>{data?.runs.filter((r:any)=>r.projectId===project).map((run:any)=><article key={run.requestId} style={{borderBottom:'1px solid #ddd',padding:'16px 0'}}><strong>{run.runId}</strong><p>{run.implementationId} · {run.version?.method?.version} · {run.createdAt}</p><code>{run.version?.method?.digest?.slice(0,16)}</code><p>{run.available?<button onClick={()=>open(run)}>打开研究视图</button>:<span>该研究插件尚未安装。恢复 {run.version?.method?.name}@{run.version?.method?.version} 及对应内容哈希后继续。</span>}</p></article>)}</div></div>
 }
 const Icon=()=>React.createElement('span',null,'◈')
 ctx.slots.inject('main',()=>ctx.slots.register({name:'main',key:'research-runtime',id:'research-runtime'},Panel))
 ctx.slots.inject('sidebar.panellist',()=>ctx.slots.register({name:'sidebar.panellist',id:'research-runtime',order:34,label:'研究工作台'},Icon))
}
