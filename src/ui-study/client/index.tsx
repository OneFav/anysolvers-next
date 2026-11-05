import * as React from 'react'
import { useEffect, useRef, useState } from 'react'

type Values = Record<string, number | null>
type Result<T> = { ok: true; value: T } | { ok: false; error: { message: string } }
interface ClientContext {
  slots: { inject(name: string, fn: () => () => void): void; register(options: object, component: React.ComponentType<any>): () => void }
  connection: { rpc: { call(channel: string, endpoint: string, payload: unknown, signal?: AbortSignal): Promise<Result<unknown>> } }
  sessions: { create(options: { cwd: string; agentPreset: string }): Promise<string>; refresh(): Promise<void> }
  remote: { commands: { execute(id: string, line: string, attachments: never[]): Promise<Result<{ result: { kind: string; text?: string } } | undefined>> } }
  layout: { selectPanel(id: string): void }
}
interface Project {id:string;name:string;root:string;latestRun:string|null;latestAt:string|null;error:string|null}
interface Projects {projects:Project[];defaultProjectId:string|null}
interface Catalog { root: string; studies: { study_id: string; task_id: string; existing_runs: string[] }[]; invalid: { study_id: string; reason: string }[] }
interface Configuration { saved?: Saved | null; study_id: string; revision: string; source: string; values: Values; existing_runs: string[]; model: { model: string; reasoning_effort: string }; image: string; executor?: string }
interface Saved { config: string; command: string; values: Values }
interface ChartEntry {
  evaluation_id: string; candidate_id: string; dataset_id: string; actor: string|null; session_id: string|null;
  generation: number|null; submitted_at: string|null; status: string; score: number|null; eligible: boolean; probe: boolean;
}
interface ChartHistory { metric: string; direction: 'minimize'|'maximize'; datasets: {id:string;purpose:string}[]; evaluations: ChartEntry[] }
interface Evaluation extends ChartEntry { dataset_purpose:string; time_source:string; all_valid:boolean|null; completed_cases:number|null; total_cases:number|null; valid_cases:number|null }
interface ArtifactItem { id:string; manifest_path:string; evaluation_ids:string[] }
interface ArtifactContent { filename:string; mime:string; encoding:'utf8'|'base64'; content:string }
interface Terminal { phase:string; complete:boolean; final_authorized:boolean; reports:ArtifactItem[]; figures:ArtifactItem[] }
interface Progress {
  terminal?: Terminal;
  workers?: {id:string;stage:string;state:string;detail:string}[];
  run_id: string; phase: string; planned_sessions: number; completed_sessions: number; failed_sessions: number; missing_sessions: number;
  remaining_evaluations: number | null; evaluation_limit: number | null; remaining_run_s: number; pending_requests: string[];
  selection: { candidate_id: string; score: number } | null;
  sessions: { session_id: string; actor: string; generation: number; status: string }[];
  research_time: Record<string, { remaining_s: number; active_research_s: number; evaluation_wait_s: number }>;
  final_coverage: { covered_pairs: number; expected_pairs: number; complete: boolean };
  chart: ChartHistory; evaluations: Evaluation[]; final_evaluations: Evaluation[];
  total_development_evaluations:number; total_final_evaluations:number; final_offset:number; final_next_offset:number|null;
  values: Values; model: Configuration['model']; total_evaluations: number; offset: number; next_offset: number | null;
  reports: { id: string; manifest_path: string }[]; updated_at: string;
}
// Desktop can serve a rebuilt browser bundle while retaining an older host plugin in memory.
// Zero is the host default; omit the new optional cursor until final pagination is requested.
export async function readProgress(
  api: (method: string, payload: object, signal?: AbortSignal) => Promise<unknown>,
  runId: string, offset = 0, finalOffset = 0, signal?: AbortSignal,
): Promise<Progress> {
  const payload = { runId, offset, ...(finalOffset > 0 ? { final_offset: finalOffset } : {}) }
  try { return await api('progress', payload, signal) as Progress }
  catch (error) {
    if (finalOffset > 0 && error instanceof Error && error.message === 'Unknown research UI field') {
      throw new Error('当前 DSH App 仍加载旧版 AnySolvers 后端，暂不支持最终评测翻页。请重启 DSH App 后重试。')
    }
    throw error
  }
}
const fields: [string, string, string, number?][] = [
  ['researchers','研究员数量','最多同时研究的独立研究员'], ['rounds_per_researcher','每位研究员会话数','各自续接笔记，不等待其他研究员'],
  ['session_wall_s','每次会话研究时间 · 秒','有效研究时间，不含评测等待与执行'], ['run_wall_s','整批准入窗口 · 秒','到期停止接纳新工作，允许收尾'],
  ['evaluation_calls','评测次数上限','包含基线与最终测试；留空表示不限'], ['max_parallel_evaluations','并发评测数','本地 Docker 当前支持 1–3 槽',3],
  ['evaluation_wall_s','整次评测超时 · 秒','取得执行槽后计时，不包含排队'], ['wall_s','单例墙钟上限 · 秒','每个实例的实际运行时限'],
  ['cpu_s','单例 CPU 上限 · 秒','单核容器累计 CPU 时间'], ['memory_mb','单例内存 · MiB','容器内存上限'],
  ['pids','进程数上限','容器内允许的进程数量'], ['tmp_mb','临时空间 · MiB','容器临时目录大小'], ['output_bytes','输出上限 · 字节','限制单例程序输出'],
]
export function panelFields(im:boolean):typeof fields {
  if(!im)return fields
  const hints:Record<string,string>={max_parallel_evaluations:'本批次评测并发数；与 10 个 Worker 任务槽位分别计数',cpu_s:'单例累计 CPU 时间',memory_mb:'DSH 原生环境使用 RSS 监测终止',pids:'执行后端的进程限制',tmp_mb:'执行后端的临时空间预算'}
  return fields.map(([key,label,hint,max])=>[key,label,hints[key]??hint,...(max===undefined?[]:[max])] as typeof fields[number])
}
const terminalNames:Record<string,string> = {'research':'研究尚未结束','final-evaluation':'最终测试待完成','report':'报告待交付','visualization':'图表待交付','complete':'终局已完成','development-only':'仅开发结果'}
const phaseNames: Record<string,string> = { orchestrate:'开发研究', finalize:'候选已冻结', reported:'报告已交付' }
const states: Record<string,string> = { active:'研究中', completed:'已完成', failed:'失败', starting:'启动中', closing:'收尾中', reconciliation:'需要核对', running:'执行中', queued:'排队中', waiting_input:'等待回复', waiting_provider:'等待模型恢复', host_lost:'执行节点失联', recovering:'自动恢复核对中', stopped:'已停止' }
const duration = (s: number | undefined) => {
  if (s == null) return '—'
  const n = Math.max(0, Math.round(s))
  if (n < 60) return `${n} 秒`
  if (n >= 3600) return `${Math.floor(n/3600)} 小时 ${Math.floor(n%3600/60)} 分`
  return `${Math.floor(n/60)} 分 ${n%60} 秒`
}
const score = (v: number | null | undefined) => typeof v !== 'number' || !Number.isFinite(v) ? '—' : v!==0 && (Math.abs(v)<.0001 || Math.abs(v)>=1e7) ? v.toExponential(3) : v.toLocaleString('zh-CN',{maximumFractionDigits:4})
const submittedTime = (value:string|null) => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '时间未记录'
// A generation belongs to one actor. There is no shared round or synchronization barrier.
export function researcherRows(progress: Progress) {
  const actors = new Map<string, Progress['sessions']>()
  for (const session of progress.sessions) {
    const history = actors.get(session.actor) ?? []
    history.push(session)
    actors.set(session.actor, history)
  }
  return [...actors].sort(([a],[b])=>a.localeCompare(b, 'zh-CN', {numeric:true})).map(([actor, history])=>{
    history.sort((a,b)=>a.generation-b.generation)
    const latest = history[history.length-1]!
    const completed = history.filter(s=>s.status==='completed').length
    const planned = progress.values.rounds_per_researcher ?? 0
    const live = ['active','starting','closing'].includes(latest.status)
    const issue = ['failed','reconciliation'].includes(latest.status)
    const status = latest.status==='completed' && completed<planned
      ? progress.phase==='orchestrate' ? '待续接' : '已结束'
      : states[latest.status] ?? latest.status
    return {actor, history, latest, completed, planned, live, issue, status}
  })
}
function Chevron(){return <svg className="as-chevron" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>}
const actorColor = (index:number) => ['#729be8','#d78c69','#5daa93','#b597dd','#cd8dad','#59a7bb'][index] ?? `hsl(${(index*137.5)%360} 48% 60%)`
type PositionedEntry = ChartEntry & {x:number; ordinal:number; historical:boolean}
export function buildChart(history:ChartHistory, sessions:Progress['sessions'], dataset:string) {
  const entries=history.evaluations.filter(e=>e.dataset_id===dataset)
  const timed=(e:ChartEntry)=>e.submitted_at?Date.parse(e.submitted_at):Infinity
  // Stable sort preserves the backend's microsecond submission order within a JS millisecond.
  const ordered=(a:ChartEntry,b:ChartEntry)=>(timed(a)-timed(b)||0)
  const valid=(e:ChartEntry)=>e.eligible && typeof e.score==='number' && Number.isFinite(e.score)
  const baseline=entries.filter(e=>e.candidate_id==='baseline'&&valid(e)).sort(ordered)[0]??null
  const actors=[...new Set(sessions.map(s=>s.actor))].sort((a,b)=>a.localeCompare(b,'zh-CN',{numeric:true}))
  const series=actors.map((actor,index)=>{
    const own=sessions.filter(s=>s.actor===actor).sort((a,b)=>a.generation-b.generation)
    const latest=own.at(-1)!
    const points:PositionedEntry[]=[]
    for (const session of own) {
      const submissions=entries.filter(e=>e.actor===actor&&e.session_id===session.session_id&&e.generation===session.generation).sort(ordered)
      submissions.forEach((entry,i)=>points.push({...entry,x:session.generation-1+(i+1)/(submissions.length+1),ordinal:i+1,historical:session.generation<latest.generation}))
    }
    let best:number|null=null
    const frontier:{x:number;score:number}[]=[]
    for(const point of points){
      if(!valid(point))continue
      const number=point.score!
      if(best===null || (history.direction==='minimize'?number<best:number>best))best=number
      frontier.push({x:point.x,score:best})
    }
    const endX=['completed','failed'].includes(latest.status)?latest.generation:Math.max(latest.generation-1,points.at(-1)?.x??0)
    if(best!==null && endX>(frontier.at(-1)?.x??0))frontier.push({x:endX,score:best})
    return {actor,color:actorColor(index),dash:index%3===1?'7 3':index%3===2?'2 3':undefined,latest,points,frontier,best}
  })
  const values=[...series.flatMap(s=>s.points).filter(valid).map(e=>e.score!),...(baseline?[baseline.score!]:[])]
  const low=values.length?Math.min(...values):0, high=values.length?Math.max(...values):1
  const padding=(high-low)*.14 || Math.abs(high)*.1 || 1
  return {series,baseline,rounds:Math.max(1,...sessions.map(s=>s.generation)),yMin:low-padding,yMax:high+padding,
    unscored:series.flatMap(s=>s.points).filter(p=>!valid(p)).length,
    unassigned:entries.filter(e=>e.candidate_id!=='baseline'&&!e.actor).length}
}
export function stepPath(points:{x:number;score:number}[], x:(v:number)=>number, y:(v:number)=>number) {
  return points.map((point,i)=>i===0?`M ${x(point.x)} ${y(point.score)}`:`H ${x(point.x)} V ${y(point.score)}`).join(' ')
}
function ResearchChart({progress}:{progress:Progress}) {
  const [dataset,setDataset]=useState(''), [focus,setFocus]=useState<string|null>(null), [hover,setHover]=useState<string|null>(null)
  const [width,setWidth]=useState(800); const canvas=useRef<HTMLDivElement>(null)
  useEffect(()=>{const node=canvas.current;if(!node)return;const observer=new ResizeObserver(([entry])=>{if(entry)setWidth(Math.max(300,entry.contentRect.width))});observer.observe(node);return()=>observer.disconnect()},[])
  const history=progress.chart
  const selected=history.datasets.find(d=>d.id===dataset)?.id??history.datasets.find(d=>d.purpose==='anchor')?.id??history.datasets[0]?.id??''
  const data=buildChart(history,progress.sessions,selected)
  const points=data.series.flatMap(s=>s.points.map(p=>({...p,color:s.color})))
  const point=points.find(p=>p.evaluation_id===hover)
  const left=64,right=20,top=26,bottom=270,height=data.unscored?368:330
  const x=(v:number)=>left+v/data.rounds*(width-left-right)
  const y=(v:number)=>bottom-(v-data.yMin)/(data.yMax-data.yMin)*(bottom-top)
  const py=(p:PositionedEntry)=>p.eligible&&p.score!==null?y(p.score):bottom+29
  const tickEvery=Math.max(1,Math.ceil(data.rounds/Math.max(1,Math.floor((width-left-right)/95))))
  const tooltipText=(p:PositionedEntry)=>`${p.actor} · 第 ${p.generation} 次会话 · 第 ${p.ordinal} 次提交；${p.eligible?'成绩 '+score(p.score):p.probe?'探测评测，不计入曲线':p.status==='completed'?'无有效成绩':states[p.status]??p.status}；${p.candidate_id}；${submittedTime(p.submitted_at)}`
  return <section className="as-card as-chart" aria-label="研究进展图">
    <div className="as-title-row"><h2>研究进展</h2>{history.datasets.length>1?<select aria-label="图表数据集" value={selected} onChange={e=>{setDataset(e.target.value);setHover(null)}}>{history.datasets.map(d=><option key={d.id} value={d.id}>{d.id}</option>)}</select>:<span className="as-muted">{selected||'开发评测'}</span>}</div>
    <div className="as-chart-meta"><span>{history.metric} · {history.direction==='minimize'?'越低越好':'越高越好'}</span><span>个人轮次，独立推进</span></div>
    <div ref={canvas} className="as-chart-canvas">
      <svg viewBox={`0 0 ${width} ${height}`} style={{height}} role="group" aria-label="各研究员最佳成绩曲线与每次评测散点">
        <title>每种颜色代表一位研究员；实线或虚线为其历史最佳成绩，点为评测结果。每次会话内按提交顺序等距排布，非实际等时刻。</title>
        {Array.from({length:5},(_,i)=>{const value=data.yMin+(data.yMax-data.yMin)*i/4;return <g key={i}><line className="as-chart-grid" x1={left} x2={width-right} y1={y(value)} y2={y(value)}/><text className="as-chart-tick" x={left-10} y={y(value)+4} textAnchor="end">{score(Number(value.toPrecision(4)))}</text></g>})}
        {Array.from({length:data.rounds+1},(_,i)=><line key={i} className="as-chart-grid as-chart-boundary" x1={x(i)} x2={x(i)} y1={top} y2={bottom}/>)}
        {data.baseline&&<g className="as-chart-baseline"><line x1={left} x2={width-right} y1={y(data.baseline.score!)} y2={y(data.baseline.score!)} stroke="currentColor" strokeDasharray="5 5"/><rect x={left-3.5} y={y(data.baseline.score!)-3.5} width={7} height={7} fill="currentColor"/><title>{`基线 ${data.baseline.candidate_id}：${score(data.baseline.score)}`}</title></g>}
        {data.series.map(s=><path key={s.actor} d={stepPath(s.frontier,x,y)} fill="none" stroke={s.color} strokeWidth={2.5} strokeDasharray={s.dash} opacity={focus&&focus!==s.actor?0.12:1} strokeLinejoin="round"/>) }
        {data.unscored>0&&<g><line className="as-chart-grid" x1={left} x2={width-right} y1={bottom+29} y2={bottom+29}/><text className="as-chart-tick" x={left-10} y={bottom+33} textAnchor="end">未计分</text></g>}
        {points.map(p=><g key={p.evaluation_id} opacity={focus&&focus!==p.actor?0.12:1}>
          <circle cx={x(p.x)} cy={py(p)} r={hover===p.evaluation_id?6:4.5} fill={p.eligible?p.color:'none'} stroke={p.color} strokeWidth={1.5} opacity={hover===p.evaluation_id?1:p.historical?0.25:0.82}/>
          <circle className="as-chart-point" cx={x(p.x)} cy={py(p)} r={11} fill="transparent" tabIndex={0} role="button" aria-label={tooltipText(p)} onMouseEnter={()=>setHover(p.evaluation_id)} onMouseLeave={()=>setHover(null)} onFocus={()=>setHover(p.evaluation_id)} onBlur={()=>setHover(null)} onClick={()=>setHover(p.evaluation_id)} onKeyDown={e=>{if(e.key==='Escape')setHover(null);if(e.key==='Enter'||e.key===' '){e.preventDefault();setHover(p.evaluation_id)}}}><title>{tooltipText(p)}</title></circle>
        </g>)}
        {Array.from({length:data.rounds},(_,i)=>i%tickEvery===0?<text key={i} className="as-chart-tick" x={x(i+.5)} y={data.unscored?bottom+62:bottom+29} textAnchor="middle">第 {i+1} 次会话</text>:null)}
      </svg>
      {point&&<div className="as-chart-tooltip" role="status" style={{left:Math.min(width-250,Math.max(0,x(point.x)-125)),top:Math.max(0,py(point)-88)}}><strong style={{color:point.color}}>{point.actor} · 第 {point.generation} 次会话</strong><span>{point.eligible?'成绩 '+score(point.score):point.probe?'探测评测，不计入曲线':point.status==='completed'?'无有效成绩':states[point.status]??point.status} · 第 {point.ordinal} 次提交</span><span className="as-code">{point.candidate_id}</span><small>{submittedTime(point.submitted_at)}</small></div>}
      {!points.length&&!data.baseline&&<div className="as-chart-empty">{progress.sessions.length?'等待开发评测结果':'等待研究员开始研究'}</div>}
    </div>
    <div className="as-chart-legend" aria-label="图例，点击研究员突出显示其曲线">
      {data.series.map(s=><button key={s.actor} className="as-legend-item" aria-pressed={focus===s.actor} onClick={()=>setFocus(focus===s.actor?null:s.actor)}><span className="as-legend-stroke" style={{borderColor:s.color,borderTopStyle:s.dash?'dashed':'solid'}}/><strong>{s.actor}</strong><span>{score(s.best)}</span></button>)}
      {data.baseline&&<span className="as-legend-item as-legend-baseline"><span className="as-legend-stroke"/>基线 <span>{score(data.baseline.score)}</span></span>}
    </div>
    <div className="as-chart-caption">线表示历史最佳，点表示每次评测；同轮内按提交顺序等距排列，历史会话点淡化。{data.unassigned>0?` ${data.unassigned} 次评测未关联研究员，可在记录中查看。`:''}{!data.baseline?' 本数据集暂无有效基线成绩。':''}</div>
  </section>
}
function EvaluationTable({rows}:{rows:Evaluation[]}) {
  return !rows.length?<p className="as-muted">暂无评测记录。</p>:<div className="as-table-wrap"><table><thead><tr><th>候选 / 研究员</th><th>数据集</th><th>提交时间</th><th>状态</th><th>合法 / 总例数</th><th>成绩</th></tr></thead><tbody>{rows.map(v=><tr key={v.evaluation_id}><td title={v.evaluation_id}>{v.candidate_id}<div className="as-muted">{v.actor?`${v.actor} · 第 ${v.generation} 次会话`:v.candidate_id==='baseline'?'基线':'主会话评测'}</div></td><td>{v.dataset_id}</td><td title={v.time_source==='started'?'无研究员提交记录，显示评测开始时间':undefined}>{submittedTime(v.submitted_at)}{v.time_source==='started'?' *':''}</td><td>{states[v.status]??v.status}{v.probe?' · 探测':''}</td><td>{v.valid_cases??'—'} / {v.total_cases??'—'}</td><td>{v.eligible?score(v.score):'—'}</td></tr>)}</tbody></table>{rows.some(v=>v.time_source==='started')&&<div className="as-muted">* 此记录显示评测开始时间。</div>}</div>
}
const css = `
.as-project-panel{height:100%;overflow:auto}.as-project-panel > .as-panel{height:auto;overflow:visible;padding-top:24px}.as-project-switch{box-sizing:border-box;width:calc(100% - clamp(36px,8vw,96px));max-width:960px;margin:auto;padding:calc(var(--dsh-frame-top-clearance,24px) + 16px) 0 0;color:inherit}.as-project-switch select{background:var(--background,#181b22);color:inherit;padding:7px 10px;border:1px solid #454a54;border-radius:7px;margin-left:8px;max-width:80%}.as-project-path{margin-top:8px;overflow-wrap:anywhere;font-size:12px}.as-panel{height:100%;overflow:auto;padding:calc(var(--dsh-frame-top-clearance,24px) + 16px) clamp(18px,4vw,48px) 48px;color:inherit;font-family:inherit;background:transparent;box-sizing:border-box;font-size:14px}
.as-panel *{box-sizing:border-box}.as-wrap{max-width:960px;margin:auto}.as-head{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:24px}
.as-panel h1{font-size:24px;margin:0;font-weight:600;letter-spacing:-.025em}.as-panel h2{font-size:15px;margin:0;font-weight:600}.as-muted{opacity:.64;font-size:12px;line-height:1.7}.as-small{font-size:12px}.as-panel p{line-height:1.7}
.as-toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:18px 0}.as-navigation{justify-content:space-between;margin-bottom:20px}.as-runbar{justify-content:space-between;margin:0 0 16px}.as-runbar select{max-width:100%;font-size:12px;padding:7px 10px;background:transparent;border-color:transparent}.as-runbar select:hover{border-color:color-mix(in srgb,currentColor 18%,transparent)}
.as-panel select,.as-panel input{font:inherit;color:inherit;background:color-mix(in srgb,currentColor 3%,transparent);border:1px solid color-mix(in srgb,currentColor 16%,transparent);border-radius:8px;padding:9px 12px;min-width:0}.as-panel select option{background:Canvas;color:CanvasText}.as-select{width:270px;max-width:100%}
.as-btn{font:inherit;font-size:12px;border-radius:8px;padding:9px 13px;border:1px solid color-mix(in srgb,currentColor 16%,transparent);background:transparent;color:inherit;cursor:pointer}.as-btn:hover:not(:disabled){background:color-mix(in srgb,currentColor 6%,transparent)}.as-btn:disabled{opacity:.4;cursor:not-allowed}.as-primary{background:#4b66dc;color:white;border-color:#4b66dc}.as-primary:hover:not(:disabled){background:#3d56c0}
.as-tabs{display:flex;gap:3px;padding:3px;border-radius:9px;background:color-mix(in srgb,currentColor 4%,transparent)}.as-tabs button{border:0;padding:8px 13px;white-space:nowrap}.as-tabs button[aria-selected=true]{background:color-mix(in srgb,currentColor 10%,transparent)}
.as-card{border:1px solid color-mix(in srgb,currentColor 11%,transparent);border-radius:12px;padding:24px;margin:16px 0;background:color-mix(in srgb,currentColor 1.5%,transparent)}
.as-overview{padding:26px 24px 18px}.as-overview-main{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:24px;align-items:start}.as-stage{font-size:24px;font-weight:550;letter-spacing:-.025em;margin:8px 0 6px}.as-result{padding-left:24px;border-left:1px solid color-mix(in srgb,currentColor 10%,transparent);min-width:0}.as-result strong{display:block;font-size:29px;font-weight:550;letter-spacing:-.035em;font-variant-numeric:tabular-nums;margin:5px 0}.as-result .as-code{opacity:.65}.as-result-empty{font-size:20px!important;opacity:.65}
.as-summary-line{border-top:1px solid color-mix(in srgb,currentColor 9%,transparent);margin-top:22px;padding-top:14px;display:flex;gap:8px 22px;flex-wrap:wrap;font-size:12px;opacity:.75}.as-summary-line b{font-weight:600;font-variant-numeric:tabular-nums}.as-inline-note{font-size:12px;line-height:1.7;margin-top:10px;opacity:.7}.as-inline-note strong{font-weight:550}
.as-title-row{display:flex;align-items:center;justify-content:space-between;gap:8px 16px;flex-wrap:wrap}.as-researchers>.as-title-row{margin-bottom:12px}.as-actor{border-top:1px solid color-mix(in srgb,currentColor 9%,transparent)}.as-actor:first-of-type{border-top:0}.as-actor>summary{display:flex;gap:12px;align-items:center;padding:16px 0;cursor:pointer;list-style:none}.as-actor>summary::-webkit-details-marker,.as-disclosure>summary::-webkit-details-marker{display:none}
.as-dot{width:7px;height:7px;flex:none;border-radius:50%;background:color-mix(in srgb,currentColor 32%,transparent)}.as-dot[data-live=true]{background:#7897e4}.as-dot[data-issue=true]{background:#d99b55}.as-actor-name{min-width:0;flex:1}.as-actor-name strong{display:block;font-size:14px;font-weight:550;overflow-wrap:anywhere}.as-actor-name .as-muted{display:block;margin-top:3px}.as-actor-status{font-size:12px;white-space:nowrap;opacity:.8}.as-actor-status[data-live=true]{color:#7f9eea}.as-actor-status[data-issue=true]{color:#d99b55}.as-chevron{flex:none;opacity:.5;transition:transform .15s}.as-actor[open]>summary>.as-chevron,.as-disclosure[open]>summary>.as-chevron{transform:rotate(90deg)}.as-actor .as-table-wrap{padding-bottom:16px}
.as-disclosures{border:1px solid color-mix(in srgb,currentColor 11%,transparent);border-radius:12px;overflow:hidden;margin-top:18px}.as-disclosure+.as-disclosure{border-top:1px solid color-mix(in srgb,currentColor 9%,transparent)}.as-disclosure>summary{display:flex;gap:12px;align-items:center;padding:18px 24px;list-style:none;cursor:pointer;font-size:13px}.as-disclosure>summary:hover,.as-actor>summary:hover{background:color-mix(in srgb,currentColor 2%,transparent)}.as-disclosure>summary .as-muted{margin-left:auto;text-align:right}.as-disclosure-body{padding:0 24px 22px}.as-disclosure-body>p:first-child{margin-top:0}.as-disclosure .as-actions{margin-top:14px}
.as-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 28px}.as-field{display:flex;flex-direction:column;gap:8px;font-size:13px}.as-field>span:first-child{font-weight:550}.as-field small{opacity:.6;line-height:1.5}.as-field input{width:100%}.as-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:24px}.as-parameters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 26px;margin-top:20px}.as-parameter{font-size:12px;display:flex;justify-content:space-between;gap:16px}.as-parameter span:first-child{opacity:.6}.as-parameter span:last-child{font-variant-numeric:tabular-nums;white-space:nowrap}
.as-notice{padding:12px 16px;border-radius:8px;background:color-mix(in srgb,#c9963b 9%,transparent);border:1px solid color-mix(in srgb,#c9963b 25%,transparent);font-size:12px;line-height:1.7;margin:16px 0;overflow-wrap:anywhere}.as-error{background:color-mix(in srgb,#d45555 10%,transparent);border-color:color-mix(in srgb,#d45555 30%,transparent)}.as-success{background:color-mix(in srgb,#3e9b80 9%,transparent);border-color:color-mix(in srgb,#3e9b80 30%,transparent)}
.as-table-wrap{overflow:auto}.as-panel table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:12px}.as-panel th{text-align:left;font-weight:500;opacity:.6;padding:10px}.as-panel td{padding:12px 10px;border-top:1px solid color-mix(in srgb,currentColor 8%,transparent)}.as-code{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere;line-height:1.7}.as-empty{padding:60px 24px;text-align:center;border:1px dashed color-mix(in srgb,currentColor 16%,transparent);border-radius:12px;margin-top:24px}.as-empty h2{margin-bottom:12px}.as-details{font-size:12px;line-height:1.8}.as-details summary{cursor:pointer;margin:14px 0}
.as-refresh{display:flex;align-items:center;gap:7px;font-size:12px;opacity:.65;white-space:nowrap}.as-refresh input{width:13px;height:13px;accent-color:#7897e4;margin:0}.as-panel button:focus-visible,.as-panel input:focus-visible,.as-panel select:focus-visible,.as-panel summary:focus-visible{outline:2px solid #7691f1;outline-offset:2px}
.as-progress-heading{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin:24px 0 12px}.as-progress-heading strong{font-size:16px;font-weight:550}.as-progress-heading .as-muted{font-variant-numeric:tabular-nums}.as-progress-note{font-size:12px;line-height:1.7;opacity:.68;margin:8px 0 16px}
.as-chart{padding:24px 24px 18px}.as-chart .as-title-row select{font-size:12px;padding:6px 10px;max-width:55%}.as-chart-meta{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:11px;opacity:.6;margin-top:12px}.as-chart-canvas{position:relative;min-width:0;margin:8px 0 0}.as-chart-canvas>svg{display:block;width:100%;overflow:visible}.as-chart-grid{stroke:currentColor;stroke-opacity:.1;stroke-width:1}.as-chart-boundary{stroke-dasharray:3 6;stroke-opacity:.08}.as-chart-tick{fill:currentColor;opacity:.58;font-size:11px;font-variant-numeric:tabular-nums}.as-chart-baseline{opacity:.55}.as-chart-point{cursor:pointer;outline:none}.as-chart-point:focus-visible{stroke:currentColor;stroke-width:1;stroke-dasharray:2 2}.as-chart-tooltip{position:absolute;z-index:2;pointer-events:none;max-width:250px;width:max-content;box-shadow:0 5px 18px #0002;background:Canvas;color:CanvasText;border:1px solid color-mix(in srgb,currentColor 18%,transparent);border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.6}.as-chart-tooltip>*{display:block}.as-chart-tooltip strong{font-weight:600}.as-chart-tooltip small{opacity:.6}.as-chart-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:13px;opacity:.65;pointer-events:none}
.as-artifact-image{width:100%;height:auto;background:white;border-radius:8px;margin-top:12px}.as-report-preview{white-space:pre-wrap;overflow-wrap:anywhere;max-height:440px;overflow:auto;font:inherit;line-height:1.8;padding:16px;background:color-mix(in srgb,currentColor 4%,transparent);border-radius:8px}.as-artifact{margin-top:14px}.as-artifact a{text-decoration:none;display:inline-block}
.as-chart-legend{display:flex;gap:6px 12px;flex-wrap:wrap;align-items:center;margin-top:-8px}.as-legend-item{display:inline-flex;align-items:center;gap:8px;appearance:none;font:inherit;font-size:12px;background:transparent;color:inherit;border:1px solid transparent;border-radius:7px;padding:7px 8px;cursor:pointer}.as-legend-item strong{font-weight:550}.as-legend-item>span:last-child{opacity:.65;font-variant-numeric:tabular-nums}.as-legend-item[aria-pressed=true]{border-color:color-mix(in srgb,currentColor 22%,transparent);background:color-mix(in srgb,currentColor 5%,transparent)}.as-legend-item:hover{background:color-mix(in srgb,currentColor 5%,transparent)}.as-legend-stroke{display:inline-block;width:22px;border-top:2px solid;flex:none}.as-legend-baseline{opacity:.65;cursor:default}.as-legend-baseline .as-legend-stroke{border-top:1px dashed}.as-chart-caption{font-size:11px;line-height:1.7;opacity:.6;margin-top:10px}
@media(max-width:680px){.as-grid,.as-parameters{grid-template-columns:1fr}.as-card{padding:20px}.as-disclosure>summary{padding:17px 20px}.as-disclosure-body{padding:0 20px 20px}.as-panel{padding-left:18px;padding-right:18px}.as-head{margin-bottom:20px}.as-overview-main{gap:18px}.as-result{padding-left:18px}.as-stage{font-size:21px}.as-result strong{font-size:26px}.as-select{width:100%}}
@media(max-width:420px){.as-overview-main{grid-template-columns:1fr}.as-result{padding-left:0;padding-top:16px;border-left:0;border-top:1px solid color-mix(in srgb,currentColor 9%,transparent)}.as-summary-line{gap:8px 14px}.as-runbar{gap:10px}.as-disclosure>summary{gap:8px}.as-disclosure>summary .as-muted{font-size:11px}}
`

type ArtifactApi = <T>(method:string,payload:object,signal?:AbortSignal)=>Promise<T>
export function ArtifactPreview({runId,item,kind,api}:{runId:string;item:ArtifactItem;kind:'report'|'figure';api:ArtifactApi}) {
  const [open,setOpen]=useState(false),[content,setContent]=useState<ArtifactContent>(),[data,setData]=useState<ArtifactContent>(),[error,setError]=useState('')
  useEffect(()=>{
    if(!open)return
    const abort=new AbortController();setError('')
    const request=(asset:string)=>api<ArtifactContent>('artifact',{runId,kind,id:item.id,asset},abort.signal)
    Promise.all([request(kind==='report'?'report':'image'),kind==='figure'?request('data'):Promise.resolve(undefined)])
      .then(([a,b])=>{if(!abort.signal.aborted){setContent(a);setData(b)}})
      .catch(e=>{if(!abort.signal.aborted)setError(e.message)})
    return()=>abort.abort()
  },[open,runId,item.id,kind,api])
  const url=(v:ArtifactContent)=>v.encoding==='base64'?`data:${v.mime};base64,${v.content}`:`data:${v.mime};charset=utf-8,${encodeURIComponent(v.content)}`
  return <details className="as-details as-artifact" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>{kind==='report'?'报告':'图表'} · {item.id}</summary>
    {error?<p role="alert">{error}</p>:!content?<p className="as-muted">展开后读取已登记文件</p>:<>
      {kind==='figure'?<img className="as-artifact-image" src={url(content)} alt={item.id+' · 已登记评测图表'}/>:<pre className="as-report-preview">{content.content}</pre>}
      <div className="as-actions"><a className="as-btn" download={content.filename} href={url(content)}>下载{kind==='figure'?'图像':'报告'}</a>{data&&<a className="as-btn" download={data.filename} href={url(data)}>下载绘图数据</a>}</div>
      <p className="as-muted">引用 {item.evaluation_ids.length} 项评测 · {item.manifest_path}</p>
    </>}
  </details>
}

export const inject = ['slots','connection','layout','sessions','remote']
export function apply(ctx: ClientContext): void { applyPanel(ctx) }
export function applyPanel(ctx: ClientContext, options={id:'anysolvers',label:'AnySolvers',im:false}): void {
  const fields = panelFields(options.im)
  const selectionEvent = options.im ? 'anysolvers-im-selection' : 'research-runtime-selection'
  async function hostApi<T>(method: string, payload: object, signal?: AbortSignal): Promise<T> {
    const result = await ctx.connection.rpc.call('/api', options.id+'/'+method, payload, signal)
    if (!result.ok) throw new Error(result.error.message)
    return result.value as T
  }
  function Panel() {
    const [navigation,setNavigation]=useState<any>(()=>{try{return JSON.parse(sessionStorage.getItem(selectionEvent)??'null')}catch{return null}})
    useEffect(()=>{const listener=(event:Event)=>{const value=(event as CustomEvent).detail;if(value?.implementationId===options.id){setNavigation(value);setSelected(value.projectId)}};window.addEventListener(selectionEvent,listener);return()=>window.removeEventListener(selectionEvent,listener)},[])
    const [projects,setProjects]=useState<Projects>(),[selected,setSelected]=useState<string>(navigation?.implementationId===options.id?navigation.projectId:''),[error,setError]=useState('')
    useEffect(()=>{
      const abort=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined
      const load=async()=>{try{
        const result=await hostApi<Projects>('projects',{},abort.signal)
        if(!abort.signal.aborted){setProjects(result);setSelected(id=>id||result.defaultProjectId||'');setError('')}
      }catch(e){if(!abort.signal.aborted)setError(e instanceof Error?e.message:String(e))}
      finally{if(!abort.signal.aborted)timer=setTimeout(load,10000)}}
      void load();return()=>{abort.abort();clearTimeout(timer)}
    },[])
    const [deployment,setDeployment]=useState<any>()
    const [serviceBusy,setServiceBusy]=useState(false)
    useEffect(()=>{if(!options.im)return;const abort=new AbortController();let timer:ReturnType<typeof setTimeout>
      const read=async()=>{try{const d=await hostApi('deployment',{},abort.signal);if(!abort.signal.aborted)setDeployment(d)}catch{}finally{if(!abort.signal.aborted)timer=setTimeout(read,5000)}}
      void read();return()=>{abort.abort();clearTimeout(timer)}},[])
    async function manage(action:string){setServiceBusy(true);try{await hostApi(action,{});setDeployment(await hostApi('deployment',{}));setError('')}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setServiceBusy(false)}}
    const project=projects?.projects.find(p=>p.id===selected)
    return <div className="as-project-panel"><style>{css}</style>{options.im&&<section className="as-card"><h2>实例服务</h2><p>{deployment?`${deployment.running?'运行中':'已停止'} · ${deployment.mode} · ${deployment.version}`:'正在连接实例…'}</p>{deployment&&<><p className="as-muted">{deployment.instance}</p><p>飞书群：{deployment.chats.map((c:any)=>c.label||c.chat_id).join('、')||'尚未绑定，请让安装 agent 运行飞书接入向导'}</p><button className="as-btn" disabled={serviceBusy} onClick={()=>manage(deployment.running?'service-stop':'service-start')}>{deployment.running?'停止接入服务':'启动服务'}</button><p className="as-muted">升级、机器人认证和群绑定由安装 agent 按 INSTALL.md 操作。研究使用各自封存版本。</p>{deployment.source_health.map((h:any)=><p key={h.id}>{h.component}：{h.state} · {h.detail}</p>)}</>}</section>}<div className="as-project-switch"><label>项目 <select aria-label="选择项目" value={selected} onChange={e=>setSelected(e.target.value)}>{projects?.projects.map(p=><option key={p.id} value={p.id}>{p.name}{p.id===projects.defaultProjectId?' · 最近执行':''}</option>)}</select></label><div className="as-muted as-project-path">{project?.root}</div>{error&&<div role="alert" className="as-notice as-error">{error}</div>}</div>{project?<WorkspacePanel key={project.id+(navigation?.runId??'')} project={project} selectedRun={navigation?.implementationId===options.id&&navigation?.projectId===project.id?navigation.runId:undefined}/>:<div className="as-empty">{projects?'暂无研究项目；在已绑定飞书群中派工后显示。':'正在读取项目…'}</div>}</div>
  }
  function WorkspacePanel({project,selectedRun}:{project:Project;selectedRun?:string}) {
    const api=<T,>(method:string,payload:object,signal?:AbortSignal)=>hostApi<T>(method,{...payload,projectId:project.id},signal)

    const [catalog,setCatalog]=useState<Catalog>(); const [study,setStudy]=useState((selectedRun??project.latestRun)?.split('/')[0]??''); const [tab,setTab]=useState<'progress'|'config'>('progress')
    const [config,setConfig]=useState<Configuration>(); const [inputs,setInputs]=useState<Record<string,string>>({}); const [saved,setSaved]=useState<Saved>()
    const [run,setRun]=useState(selectedRun??''); const [progress,setProgress]=useState<Progress>(); const [offset,setOffset]=useState(0); const [finalOffset,setFinalOffset]=useState(0)
    const [error,setError]=useState(''); const [notice,setNotice]=useState(''); const [busy,setBusy]=useState(false); const [auto,setAuto]=useState(true)
    const [configTick,setConfigTick]=useState(0); const [loading,setLoading]=useState(false); const [tick,setTick]=useState(0); const [submitted,setSubmitted]=useState(false)
    const generation=useRef(0); const actionLock=useRef(false)
    const refresh=()=>setTick(n=>n+1)
    useEffect(()=>{const abort=new AbortController(); api<Catalog>('catalog',{},abort.signal).then(value=>{if(!abort.signal.aborted){setCatalog(value);setStudy(id=>id|| (value.studies.length===1?value.studies[0]!.study_id:''))}}).catch(e=>{if(!abort.signal.aborted)setError(String(e.message))});return()=>abort.abort()},[tick])
    useEffect(()=>{
      generation.current++; setConfig(undefined);setSaved(undefined);setProgress(undefined);setRun(selectedRun??'');setOffset(0);setFinalOffset(0);setError('');setNotice('');setSubmitted(false)
      if(!study)return
      const abort=new AbortController();setLoading(true)
      api<Configuration>('configuration',{studyId:study},abort.signal).then(value=>{
        if(abort.signal.aborted)return
        setConfig(value);setSaved(value.saved??undefined);setInputs(Object.fromEntries(Object.entries(value.values).map(([k,v])=>[k,v==null?'':String(v)])))
        setRun(selectedRun??'');if(!value.existing_runs.length)setTab('config')
      }).catch(e=>{if(!abort.signal.aborted)setError(e.message)}).finally(()=>{if(!abort.signal.aborted)setLoading(false)})
      return()=>abort.abort()
    },[study,configTick])
    // Poll only while the progress page is visible; sequential reads cannot pile up.
    useEffect(()=>{
      if(tab!=='progress'||!study)return
      const abort=new AbortController(); let timer: ReturnType<typeof setTimeout>|undefined
      const read=async()=>{
        try{
          const next=await api<Catalog>('catalog',{},abort.signal)
          if(abort.signal.aborted)return
          setCatalog(next)
          const chosen=run || next.studies.find(s=>s.study_id===study)?.existing_runs.at(-1)
          if(chosen){const p=await readProgress(api,chosen,offset,finalOffset,abort.signal);if(!abort.signal.aborted){setProgress(p);setError('')}}
        }catch(e){if(!abort.signal.aborted)setError(e instanceof Error?e.message:String(e))}
        finally{if(auto&&!abort.signal.aborted)timer=setTimeout(read,5000)}
      };void read();return()=>{abort.abort();clearTimeout(timer)}
    },[study,run,tab,auto,tick,offset,finalOffset])
    async function saveConfig(){
      if(!config||actionLock.current)return
      const values:Values={}
      for(const [key]of fields){const raw=inputs[key]?.trim()??'';if(!raw&&key==='evaluation_calls'){values[key]=null;continue}const n=Number(raw);if(!raw||!Number.isSafeInteger(n)||n<1){setError('请填写有效的正整数：'+fields.find(f=>f[0]===key)![1]);return}values[key]=n}
      const gen=generation.current; actionLock.current=true;setBusy(true);setError('');setNotice('')
      try{const value=await api<Saved>('save',{studyId:study,revision:config.revision,values});if(gen!==generation.current)return;setSaved(value);const latest=await api<Configuration>('configuration',{studyId:study});if(gen!==generation.current)return;setConfig(latest);setSubmitted(false);setNotice(options.im?'参数已保存为下一批次草稿。启动后由 Next 调度研究及终局 Worker。':'参数已保存到当前项目。/as-research 将使用这些参数创建新 run，完成研究、final 与图表。')}
      catch(e){if(gen===generation.current)setError(e instanceof Error?e.message:String(e))}
      finally{actionLock.current=false;setBusy(false)}
    }
    async function start(){
      if(!saved||!catalog||actionLock.current||submitted)return
      actionLock.current=true;setBusy(true);setError('');setSubmitted(true)
      let sessionId=''
      try{
        if(options.im){
          const reply=await api<{job_id:string}>('start',{studyId:study,config:saved.config})
          setNotice(`已提交 Next 研究任务 ${reply.job_id}；本页自动跟踪新批次及终局交付。`)
          setRun('');setOffset(0);setFinalOffset(0);setProgress(undefined);setTab('progress');refresh();return
        }
        sessionId=await ctx.sessions.create({cwd:catalog.root,agentPreset:'anysolvers-main'})
        const reply=await ctx.remote.commands.execute(sessionId,saved.command,[])
        if(!reply.ok)throw new Error(reply.error.message)
        if(!reply.value||reply.value.result.kind!=='success')throw new Error(reply.value?.result.text??'原生命令未接受请求')
        setNotice(`研究已提交。主会话 ${sessionId} 可在左侧会话列表打开；本页将跟踪新批次。`)
        setRun(selectedRun??'');setOffset(0);setFinalOffset(0);setProgress(undefined);setTab('progress');refresh();await ctx.sessions.refresh()
      }catch(e){setError(`${e instanceof Error?e.message:String(e)}${sessionId?' 主会话：'+sessionId+'。请先核对会话，避免重复启动。':''}`)}
      finally{actionLock.current=false;setBusy(false)}
    }
    const selected=catalog?.studies.find(s=>s.study_id===study)
    const researchers=progress?researcherRows(progress):[]
    const activeResearchers=researchers.filter(r=>r.live).length
    return <div className="as-panel"><style>{css}</style><div className="as-wrap">
      <header className="as-head"><h1>研究工作台</h1><button className="as-btn" aria-label="刷新列表与进度" onClick={refresh} disabled={busy}>刷新</button></header>
      <div className="as-toolbar as-navigation"><select className="as-select" aria-label="选择研究" value={study} disabled={busy} onChange={e=>setStudy(e.target.value)}><option value="">选择已初始化的研究</option>{catalog?.studies.map(s=><option key={s.study_id} value={s.study_id}>{s.study_id}</option>)}</select>
        <div className="as-tabs" role="tablist" aria-label="研究页面"><button className="as-btn" role="tab" aria-selected={tab==='progress'} onClick={()=>setTab('progress')}>研究进度</button><button className="as-btn" role="tab" aria-selected={tab==='config'} onClick={()=>setTab('config')}>运行配置</button></div></div>
      {error&&<div role="alert" className="as-notice as-error">{error}</div>}{notice&&<div role="status" className="as-notice as-success">{notice}</div>}
      {catalog?.invalid.length? <div className="as-notice">有 {catalog.invalid.length} 个研究未通过初始化校验：{catalog.invalid.map(x=>x.study_id+'：'+x.reason).join('；')}</div>:null}
      {!study?<div className="as-empty"><h2>{catalog?.studies.length?'从上方选择一项研究':'尚无已封存研究'}</h2><div className="as-muted">使用标准初始化流程准备 task、datasets 与 ideas，再来这里配置和启动。<br/>工作台不会自动选择问题或创建样例。</div></div>:loading?<div className="as-card" role="status">正在读取研究配置…</div>:null}
      {study&&tab==='config'&&config&&<section className="as-card"><div className="as-title-row"><h2>下一批次的运行参数</h2><button className="as-btn" disabled={busy} onClick={()=>setConfigTick(v=>v+1)}>重读参数</button></div><p className="as-muted">{config.model.model} · {config.model.reasoning_effort} · {config.executor==='ssh-process'?'SSH / resource-limited process (no remote sandbox)':config.executor==='ssh-docker'?'SSH Docker':options.im&&config.executor==='dsh-native'?'DSH 原生沙箱 · 无网络 / GPU · 内存 RSS 监测':'本地 Docker · 单核容器，无网络 / GPU'}</p><div className="as-grid">{fields.map(([key,label,hint,max])=><label className="as-field" key={key}><span>{label}</span><input aria-label={label} type="number" min={1} max={max} step={1} disabled={busy} value={inputs[key]??''} placeholder={key==='evaluation_calls'?'不限':undefined} onChange={e=>{setInputs(v=>({...v,[key]:e.target.value}));setSaved(undefined);setNotice('')}}/><small>{hint}</small></label>)}</div>
      <div className="as-actions"><button className="as-btn" onClick={saveConfig} disabled={busy}>{busy?'处理中…':'保存运行参数'}</button><button className="as-btn as-primary" onClick={start} disabled={!saved||busy||submitted}>{submitted?'已提交，请查看进度':selected?.existing_runs.length?'启动新批次':'启动研究'}</button></div>
      <p className="as-muted">保存不启动研究。启动后预算固定；启动即授权本次研究及最终测试、报告和图表。命令仅使用已保存参数；未保存的编辑不生效。</p>
      {saved&&<div className="as-notice as-success"><div>本次配置：<span className="as-code">{saved.config}</span></div>{!options.im&&<><div>也可在新的 AnySolvers 主会话发送：</div><code className="as-code">{saved.command}</code></>}</div>}
      <details className="as-details"><summary>配置来源与执行环境</summary><div className="as-code">{config.source}<br/>{config.image}</div></details></section>}
      {study&&tab==='progress'&&<>
      <div className="as-toolbar as-runbar"><select aria-label="选择批次" value={run} onChange={e=>{setRun(e.target.value);setOffset(0);setFinalOffset(0);setProgress(undefined)}}><option value="">最新批次{selected?.existing_runs.length?' · '+selected.existing_runs.at(-1)!.split('/').at(-1):''}</option>{selected?.existing_runs.map(r=><option key={r} value={r}>批次 {r.split('/').at(-1)}</option>)}</select><label className="as-refresh" title={progress?`每 5 秒更新，最近读取于 ${new Date(progress.updated_at).toLocaleTimeString()}`:'每 5 秒更新'}><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/>自动更新</label></div>
      {!progress?<div className="as-empty"><h2>{run||selected?.existing_runs.length?'正在读取批次状态':'尚无研究批次'}</h2><div className="as-muted">{run||selected?.existing_runs.length?'进度来自后端记录。':'在运行配置页保存参数并启动，批次建立后会出现在这里。'}</div></div>:<>
      <div className="as-progress-heading"><strong>{progress.terminal?.complete?'研究交付已完成':phaseNames[progress.phase]??progress.phase}</strong><span className="as-muted">已完成 {progress.completed_sessions} / {progress.planned_sessions} 次会话{progress.phase==='orchestrate'?` · ${activeResearchers} 位研究员在进行`:''}</span></div>
      {progress.missing_sessions>0&&progress.phase!=='orchestrate'&&<div className="as-progress-note">开发研究已结束，计划中仍有 {progress.missing_sessions} 次会话未启动。</div>}
      {options.im&&progress.workers?.length?<div className="as-progress-note">{progress.workers.map(w=><div key={w.id}>{({initialize:'初始化',research:'研究',terminal:'终局'} as Record<string,string>)[w.stage]} Worker · {states[w.state]??w.state}{w.detail?' · '+w.detail:''}</div>)}</div>:null}
      <ResearchChart key={'chart/'+progress.run_id} progress={progress}/>
      {progress.failed_sessions>0&&<div className="as-notice">有 {progress.failed_sessions} 次研究会话失败，请在主会话核对原因。</div>}
      {progress.pending_requests.length>0&&<div className="as-notice">有 {progress.pending_requests.length} 个未决评测，请在主会话核对原请求。<details className="as-details"><summary>查看请求</summary><div className="as-code">{progress.pending_requests.join('、')}</div>执行状态未确定前，请勿更换 ID 重试。</details></div>}
      <div className="as-disclosures" key={'records/'+progress.run_id}>
        <details className="as-disclosure"><summary><span>运行时评测记录</span><span className="as-muted">{progress.total_development_evaluations} 次</span><Chevron/></summary><div className="as-disclosure-body">
          <EvaluationTable rows={progress.evaluations}/>
          {(progress.offset>0||progress.next_offset!==null)&&<div className="as-actions"><button className="as-btn" disabled={offset!==progress.offset||offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>上一页</button><span className="as-muted">{progress.offset+1}–{progress.offset+progress.evaluations.length} / {progress.total_development_evaluations}</span><button className="as-btn" disabled={offset!==progress.offset||progress.next_offset===null} onClick={()=>setOffset(progress.next_offset!)}>下一页</button></div>}
        </div></details>
        <details className="as-disclosure"><summary><span>最终评测记录与报告</span><span className="as-muted">{progress.terminal?terminalNames[progress.terminal.phase]:progress.reports.length?'已有报告':progress.final_coverage.complete?'测试已完成':progress.final_coverage.covered_pairs>0?'测试尚未完成':'尚未测试'}</span><Chevron/></summary><div className="as-disclosure-body">
          <EvaluationTable rows={progress.final_evaluations}/>
          {(progress.final_offset>0||progress.final_next_offset!==null)&&<div className="as-actions"><button className="as-btn" disabled={finalOffset!==progress.final_offset||finalOffset===0} onClick={()=>setFinalOffset(Math.max(0,finalOffset-50))}>上一页</button><span className="as-muted">{progress.final_offset+1}–{progress.final_offset+progress.final_evaluations.length} / {progress.total_final_evaluations}</span><button className="as-btn" disabled={finalOffset!==progress.final_offset||progress.final_next_offset===null} onClick={()=>setFinalOffset(progress.final_next_offset!)}>下一页</button></div>}
          {progress.selection&&<p className="as-muted">冻结候选：<code className="as-code">{progress.selection.candidate_id}</code> · 开发成绩 {score(progress.selection.score)}</p>}
          <p className="as-muted">最终测试覆盖 {progress.final_coverage.covered_pairs} / {progress.final_coverage.expected_pairs} 个组合（基线 / 冻结候选 × 最终数据集）。</p>
          {progress.terminal&&!progress.terminal.final_authorized&&<p className="as-muted">本批配置仅允许开发研究，报告与图表不代表独立最终测试。</p>}
          {!progress.final_coverage.complete&&progress.terminal?.final_authorized!==false&&<p className="as-muted">最终测试由该运行的主会话在研究结束后继续执行；若中断，请在原主会话查看未决工作并继续。</p>}
          {progress.terminal?<>
            {progress.terminal.reports.map(item=><ArtifactPreview key={'report/'+item.id} runId={progress.run_id} item={item} kind="report" api={api}/>)}
            {progress.terminal.figures.map(item=><ArtifactPreview key={'figure/'+item.id} runId={progress.run_id} item={item} kind="figure" api={api}/>)}
            {!progress.terminal.figures.length&&<p className="as-muted">尚无已登记图表。主会话完成报告后，将使用可视化插件生成。</p>}
          </>:progress.reports.map(r=><div className="as-code" key={r.id}>报告：runs/{progress.run_id}/{r.manifest_path}</div>)}
        </div></details>
        <details className="as-disclosure"><summary><span>本次配置</span><span className="as-muted">本批次固定</span><Chevron/></summary><div className="as-disclosure-body"><div className="as-muted">剩余评测次数 {progress.remaining_evaluations??'不限'} · 新工作准入剩余 {duration(progress.remaining_run_s)}</div><div className="as-parameters">{fields.map(([key,label])=><div className="as-parameter" key={key}><span>{label}</span><span>{progress.values[key]??'不限'}</span></div>)}</div><p className="as-muted">{progress.model.model} · {progress.model.reasoning_effort}</p></div></details>
      <details className="as-disclosure"><summary><span>研究员会话</span><span className="as-muted">{researchers.length} 位 · 各自推进</span><Chevron/></summary><div className="as-disclosure-body">
        {researchers.map(r=><details className="as-actor" key={progress.run_id+'/'+r.actor}>
          <summary><span className="as-dot" data-live={r.live} data-issue={r.issue}/><div className="as-actor-name"><strong>{r.actor}</strong><span className="as-muted">{r.live?`第 ${r.latest.generation} 次会话`:`已完成 ${r.completed} / ${r.planned} 次会话`}{r.live&&progress.research_time[r.latest.session_id]?` · 研究时间剩余 ${duration(progress.research_time[r.latest.session_id]?.remaining_s)}`:''}</span></div><span className="as-actor-status" data-live={r.live} data-issue={r.issue}>{r.status}</span><Chevron/></summary>
          <div className="as-table-wrap"><table aria-label={r.actor+' 的会话历史'}><thead><tr><th>个人会话</th><th>状态</th><th>研究用时</th><th>评测等待 / 执行</th><th>剩余研究时间</th></tr></thead><tbody>{r.history.map(s=>{const time=progress.research_time[s.session_id];return <tr key={s.session_id}><td title={s.session_id}>第 {s.generation} 次</td><td>{states[s.status]??s.status}</td><td>{duration(time?.active_research_s)}</td><td>{duration(time?.evaluation_wait_s)}</td><td>{duration(time?.remaining_s)}</td></tr>})}</tbody></table></div>
        </details>)}
        {!researchers.length&&<p className="as-muted">尚未登记研究员会话。</p>}
        {researchers.length>0&&(progress.values.researchers??0)>researchers.length&&<div className="as-muted">另有 {(progress.values.researchers??0)-researchers.length} 个研究员名额尚未启动。</div>}
      </div></details>
      </div>
      </>}
      </>}
    </div></div>
  }
  function Icon({size=18}:{size?:number}){return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M9 3h6M10 3v7L4 19a1.3 1.3 0 0 0 1 2h14a1.3 1.3 0 0 0 1-2l-6-9V3M7 15h10"/><circle cx="10" cy="18" r=".5"/></svg>}
  ctx.slots.inject('main',()=>ctx.slots.register({name:'main',key:options.id,id:options.id},Panel))
  ctx.slots.inject('sidebar.panellist',()=>ctx.slots.register({name:'sidebar.panellist',id:options.id,order:options.im?36:35,label:options.label},Icon))
}
