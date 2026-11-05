import * as React from 'react'
import {useEffect,useState} from 'react'
export function subscribeSelection(target:Pick<Window,'addEventListener'|'removeEventListener'>,selected:(value:any)=>void){
 const listener=(event:Event)=>{const value=(event as CustomEvent).detail;if(value?.implementationId==='dynamic-fixture')selected(value)}
 target.addEventListener('research-runtime-selection',listener)
 return()=>target.removeEventListener('research-runtime-selection',listener)
}
export function apply(ctx:any){function Panel(){const [result,setResult]=useState<any>(),[error,setError]=useState('')
 const [selection,setSelection]=useState<any>(()=>{try{return JSON.parse(sessionStorage.getItem('research-runtime-selection')??'null')}catch{return null}})
 useEffect(()=>subscribeSelection(window,setSelection),[])
 useEffect(()=>{
  const abort=new AbortController();setResult(undefined);setError('')
  if(selection?.implementationId!=='dynamic-fixture'){setError('请从研究工作台选择动态评测运行。');return()=>abort.abort()}
  ctx.connection.rpc.call('/api','dynamic-fixture/view',{projectId:selection.projectId,runId:selection.runId},abort.signal).then((reply:any)=>{if(abort.signal.aborted)return;if(reply.ok)setResult(reply.value.progress);else setError(reply.error.message)}).catch((e:Error)=>{if(!abort.signal.aborted)setError(e.message)})
  return()=>abort.abort()
 },[selection?.projectId,selection?.runId])
 return <div style={{padding:24}}><h2>动态评测 · 模拟验收</h2><p>{error||result?.updateRationale}</p>{result&&<p>共同集合 {result.comparisonDataset} 上选中：{result.selectedConfiguration}</p>}{result?.snapshotRefs.map((snapshot:any)=><section key={snapshot.version}><h3>集合 {snapshot.version}</h3><code>{snapshot.sha256}</code><table><thead><tr><th>测试配置</th><th>模拟损失</th></tr></thead><tbody>{result.measurements.filter((r:any)=>r.datasetVersion===snapshot.version).map((r:any)=><tr key={r.configuration}><td>{r.configuration}</td><td>{r.fixtureLoss}</td></tr>)}</tbody></table></section>)}<p>各集合分别展示；这些数值不是真实研究结果。</p></div>}
 ctx.slots.inject('main',()=>ctx.slots.register({name:'main',key:'dynamic-fixture',id:'dynamic-fixture'},Panel))
}
