window.__ModuleLoader__.load({id:"@onefav/anysolvers-im",factory:(require)=>{const module={exports:{}};const exports=module.exports;
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.inject = void 0;
exports.readProgress = readProgress;
exports.panelFields = panelFields;
exports.researcherRows = researcherRows;
exports.buildChart = buildChart;
exports.stepPath = stepPath;
exports.ArtifactPreview = ArtifactPreview;
exports.apply = apply;
exports.applyPanel = applyPanel;
const React = require("react");
const react_1 = require("react");
// Desktop can serve a rebuilt browser bundle while retaining an older host plugin in memory.
// Zero is the host default; omit the new optional cursor until final pagination is requested.
async function readProgress(api, runId, offset = 0, finalOffset = 0, signal) {
    const payload = { runId, offset, ...(finalOffset > 0 ? { final_offset: finalOffset } : {}) };
    try {
        return await api('progress', payload, signal);
    }
    catch (error) {
        if (finalOffset > 0 && error instanceof Error && error.message === 'Unknown research UI field') {
            throw new Error('当前 DSH App 仍加载旧版 AnySolvers 后端，暂不支持最终评测翻页。请重启 DSH App 后重试。');
        }
        throw error;
    }
}
const fields = [
    ['researchers', '研究员数量', '最多同时研究的独立研究员'], ['rounds_per_researcher', '每位研究员会话数', '各自续接笔记，不等待其他研究员'],
    ['session_wall_s', '每次会话研究时间 · 秒', '有效研究时间，不含评测等待与执行'], ['run_wall_s', '整批准入窗口 · 秒', '到期停止接纳新工作，允许收尾'],
    ['evaluation_calls', '评测次数上限', '包含基线与最终测试；留空表示不限'], ['max_parallel_evaluations', '并发评测数', '本地 Docker 当前支持 1–3 槽', 3],
    ['evaluation_wall_s', '整次评测超时 · 秒', '取得执行槽后计时，不包含排队'], ['wall_s', '单例墙钟上限 · 秒', '每个实例的实际运行时限'],
    ['cpu_s', '单例 CPU 上限 · 秒', '单核容器累计 CPU 时间'], ['memory_mb', '单例内存 · MiB', '容器内存上限'],
    ['pids', '进程数上限', '容器内允许的进程数量'], ['tmp_mb', '临时空间 · MiB', '容器临时目录大小'], ['output_bytes', '输出上限 · 字节', '限制单例程序输出'],
];
function panelFields(im) {
    if (!im)
        return fields;
    const hints = { max_parallel_evaluations: '本批次评测并发数；与 10 个 Worker 任务槽位分别计数', cpu_s: '单例累计 CPU 时间', memory_mb: 'DSH 原生环境使用 RSS 监测终止', pids: '执行后端的进程限制', tmp_mb: '执行后端的临时空间预算' };
    return fields.map(([key, label, hint, max]) => [key, label, hints[key] ?? hint, ...(max === undefined ? [] : [max])]);
}
const terminalNames = { 'research': '研究尚未结束', 'final-evaluation': '最终测试待完成', 'report': '报告待交付', 'visualization': '图表待交付', 'complete': '终局已完成', 'development-only': '仅开发结果' };
const phaseNames = { orchestrate: '开发研究', finalize: '候选已冻结', reported: '报告已交付' };
const states = { active: '研究中', completed: '已完成', failed: '失败', starting: '启动中', closing: '收尾中', reconciliation: '需要核对', running: '执行中', queued: '排队中', waiting_input: '等待回复', waiting_provider: '等待模型恢复', host_lost: '执行节点失联', recovering: '自动恢复核对中', stopped: '已停止' };
const duration = (s) => {
    if (s == null)
        return '—';
    const n = Math.max(0, Math.round(s));
    if (n < 60)
        return `${n} 秒`;
    if (n >= 3600)
        return `${Math.floor(n / 3600)} 小时 ${Math.floor(n % 3600 / 60)} 分`;
    return `${Math.floor(n / 60)} 分 ${n % 60} 秒`;
};
const score = (v) => typeof v !== 'number' || !Number.isFinite(v) ? '—' : v !== 0 && (Math.abs(v) < .0001 || Math.abs(v) >= 1e7) ? v.toExponential(3) : v.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
const submittedTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '时间未记录';
// A generation belongs to one actor. There is no shared round or synchronization barrier.
function researcherRows(progress) {
    const actors = new Map();
    for (const session of progress.sessions) {
        const history = actors.get(session.actor) ?? [];
        history.push(session);
        actors.set(session.actor, history);
    }
    return [...actors].sort(([a], [b]) => a.localeCompare(b, 'zh-CN', { numeric: true })).map(([actor, history]) => {
        history.sort((a, b) => a.generation - b.generation);
        const latest = history[history.length - 1];
        const completed = history.filter(s => s.status === 'completed').length;
        const planned = progress.values.rounds_per_researcher ?? 0;
        const live = ['active', 'starting', 'closing'].includes(latest.status);
        const issue = ['failed', 'reconciliation'].includes(latest.status);
        const status = latest.status === 'completed' && completed < planned
            ? progress.phase === 'orchestrate' ? '待续接' : '已结束'
            : states[latest.status] ?? latest.status;
        return { actor, history, latest, completed, planned, live, issue, status };
    });
}
function Chevron() { return React.createElement("svg", { className: "as-chevron", width: "14", height: "14", viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: "1.5", "aria-hidden": "true" },
    React.createElement("path", { d: "m6 3 5 5-5 5" })); }
const actorColor = (index) => ['#729be8', '#d78c69', '#5daa93', '#b597dd', '#cd8dad', '#59a7bb'][index] ?? `hsl(${(index * 137.5) % 360} 48% 60%)`;
function buildChart(history, sessions, dataset) {
    const entries = history.evaluations.filter(e => e.dataset_id === dataset);
    const timed = (e) => e.submitted_at ? Date.parse(e.submitted_at) : Infinity;
    // Stable sort preserves the backend's microsecond submission order within a JS millisecond.
    const ordered = (a, b) => (timed(a) - timed(b) || 0);
    const valid = (e) => e.eligible && typeof e.score === 'number' && Number.isFinite(e.score);
    const baseline = entries.filter(e => e.candidate_id === 'baseline' && valid(e)).sort(ordered)[0] ?? null;
    const actors = [...new Set(sessions.map(s => s.actor))].sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }));
    const series = actors.map((actor, index) => {
        const own = sessions.filter(s => s.actor === actor).sort((a, b) => a.generation - b.generation);
        const latest = own.at(-1);
        const points = [];
        for (const session of own) {
            const submissions = entries.filter(e => e.actor === actor && e.session_id === session.session_id && e.generation === session.generation).sort(ordered);
            submissions.forEach((entry, i) => points.push({ ...entry, x: session.generation - 1 + (i + 1) / (submissions.length + 1), ordinal: i + 1, historical: session.generation < latest.generation }));
        }
        let best = null;
        const frontier = [];
        for (const point of points) {
            if (!valid(point))
                continue;
            const number = point.score;
            if (best === null || (history.direction === 'minimize' ? number < best : number > best))
                best = number;
            frontier.push({ x: point.x, score: best });
        }
        const endX = ['completed', 'failed'].includes(latest.status) ? latest.generation : Math.max(latest.generation - 1, points.at(-1)?.x ?? 0);
        if (best !== null && endX > (frontier.at(-1)?.x ?? 0))
            frontier.push({ x: endX, score: best });
        return { actor, color: actorColor(index), dash: index % 3 === 1 ? '7 3' : index % 3 === 2 ? '2 3' : undefined, latest, points, frontier, best };
    });
    const values = [...series.flatMap(s => s.points).filter(valid).map(e => e.score), ...(baseline ? [baseline.score] : [])];
    const low = values.length ? Math.min(...values) : 0, high = values.length ? Math.max(...values) : 1;
    const padding = (high - low) * .14 || Math.abs(high) * .1 || 1;
    return { series, baseline, rounds: Math.max(1, ...sessions.map(s => s.generation)), yMin: low - padding, yMax: high + padding,
        unscored: series.flatMap(s => s.points).filter(p => !valid(p)).length,
        unassigned: entries.filter(e => e.candidate_id !== 'baseline' && !e.actor).length };
}
function stepPath(points, x, y) {
    return points.map((point, i) => i === 0 ? `M ${x(point.x)} ${y(point.score)}` : `H ${x(point.x)} V ${y(point.score)}`).join(' ');
}
function ResearchChart({ progress }) {
    const [dataset, setDataset] = (0, react_1.useState)(''), [focus, setFocus] = (0, react_1.useState)(null), [hover, setHover] = (0, react_1.useState)(null);
    const [width, setWidth] = (0, react_1.useState)(800);
    const canvas = (0, react_1.useRef)(null);
    (0, react_1.useEffect)(() => { const node = canvas.current; if (!node)
        return; const observer = new ResizeObserver(([entry]) => { if (entry)
        setWidth(Math.max(300, entry.contentRect.width)); }); observer.observe(node); return () => observer.disconnect(); }, []);
    const history = progress.chart;
    const selected = history.datasets.find(d => d.id === dataset)?.id ?? history.datasets.find(d => d.purpose === 'anchor')?.id ?? history.datasets[0]?.id ?? '';
    const data = buildChart(history, progress.sessions, selected);
    const points = data.series.flatMap(s => s.points.map(p => ({ ...p, color: s.color })));
    const point = points.find(p => p.evaluation_id === hover);
    const left = 64, right = 20, top = 26, bottom = 270, height = data.unscored ? 368 : 330;
    const x = (v) => left + v / data.rounds * (width - left - right);
    const y = (v) => bottom - (v - data.yMin) / (data.yMax - data.yMin) * (bottom - top);
    const py = (p) => p.eligible && p.score !== null ? y(p.score) : bottom + 29;
    const tickEvery = Math.max(1, Math.ceil(data.rounds / Math.max(1, Math.floor((width - left - right) / 95))));
    const tooltipText = (p) => `${p.actor} · 第 ${p.generation} 次会话 · 第 ${p.ordinal} 次提交；${p.eligible ? '成绩 ' + score(p.score) : p.probe ? '探测评测，不计入曲线' : p.status === 'completed' ? '无有效成绩' : states[p.status] ?? p.status}；${p.candidate_id}；${submittedTime(p.submitted_at)}`;
    return React.createElement("section", { className: "as-card as-chart", "aria-label": "\u7814\u7A76\u8FDB\u5C55\u56FE" },
        React.createElement("div", { className: "as-title-row" },
            React.createElement("h2", null, "\u7814\u7A76\u8FDB\u5C55"),
            history.datasets.length > 1 ? React.createElement("select", { "aria-label": "\u56FE\u8868\u6570\u636E\u96C6", value: selected, onChange: e => { setDataset(e.target.value); setHover(null); } }, history.datasets.map(d => React.createElement("option", { key: d.id, value: d.id }, d.id))) : React.createElement("span", { className: "as-muted" }, selected || '开发评测')),
        React.createElement("div", { className: "as-chart-meta" },
            React.createElement("span", null,
                history.metric,
                " \u00B7 ",
                history.direction === 'minimize' ? '越低越好' : '越高越好'),
            React.createElement("span", null, "\u4E2A\u4EBA\u8F6E\u6B21\uFF0C\u72EC\u7ACB\u63A8\u8FDB")),
        React.createElement("div", { ref: canvas, className: "as-chart-canvas" },
            React.createElement("svg", { viewBox: `0 0 ${width} ${height}`, style: { height }, role: "group", "aria-label": "\u5404\u7814\u7A76\u5458\u6700\u4F73\u6210\u7EE9\u66F2\u7EBF\u4E0E\u6BCF\u6B21\u8BC4\u6D4B\u6563\u70B9" },
                React.createElement("title", null, "\u6BCF\u79CD\u989C\u8272\u4EE3\u8868\u4E00\u4F4D\u7814\u7A76\u5458\uFF1B\u5B9E\u7EBF\u6216\u865A\u7EBF\u4E3A\u5176\u5386\u53F2\u6700\u4F73\u6210\u7EE9\uFF0C\u70B9\u4E3A\u8BC4\u6D4B\u7ED3\u679C\u3002\u6BCF\u6B21\u4F1A\u8BDD\u5185\u6309\u63D0\u4EA4\u987A\u5E8F\u7B49\u8DDD\u6392\u5E03\uFF0C\u975E\u5B9E\u9645\u7B49\u65F6\u523B\u3002"),
                Array.from({ length: 5 }, (_, i) => { const value = data.yMin + (data.yMax - data.yMin) * i / 4; return React.createElement("g", { key: i },
                    React.createElement("line", { className: "as-chart-grid", x1: left, x2: width - right, y1: y(value), y2: y(value) }),
                    React.createElement("text", { className: "as-chart-tick", x: left - 10, y: y(value) + 4, textAnchor: "end" }, score(Number(value.toPrecision(4))))); }),
                Array.from({ length: data.rounds + 1 }, (_, i) => React.createElement("line", { key: i, className: "as-chart-grid as-chart-boundary", x1: x(i), x2: x(i), y1: top, y2: bottom })),
                data.baseline && React.createElement("g", { className: "as-chart-baseline" },
                    React.createElement("line", { x1: left, x2: width - right, y1: y(data.baseline.score), y2: y(data.baseline.score), stroke: "currentColor", strokeDasharray: "5 5" }),
                    React.createElement("rect", { x: left - 3.5, y: y(data.baseline.score) - 3.5, width: 7, height: 7, fill: "currentColor" }),
                    React.createElement("title", null, `基线 ${data.baseline.candidate_id}：${score(data.baseline.score)}`)),
                data.series.map(s => React.createElement("path", { key: s.actor, d: stepPath(s.frontier, x, y), fill: "none", stroke: s.color, strokeWidth: 2.5, strokeDasharray: s.dash, opacity: focus && focus !== s.actor ? 0.12 : 1, strokeLinejoin: "round" })),
                data.unscored > 0 && React.createElement("g", null,
                    React.createElement("line", { className: "as-chart-grid", x1: left, x2: width - right, y1: bottom + 29, y2: bottom + 29 }),
                    React.createElement("text", { className: "as-chart-tick", x: left - 10, y: bottom + 33, textAnchor: "end" }, "\u672A\u8BA1\u5206")),
                points.map(p => React.createElement("g", { key: p.evaluation_id, opacity: focus && focus !== p.actor ? 0.12 : 1 },
                    React.createElement("circle", { cx: x(p.x), cy: py(p), r: hover === p.evaluation_id ? 6 : 4.5, fill: p.eligible ? p.color : 'none', stroke: p.color, strokeWidth: 1.5, opacity: hover === p.evaluation_id ? 1 : p.historical ? 0.25 : 0.82 }),
                    React.createElement("circle", { className: "as-chart-point", cx: x(p.x), cy: py(p), r: 11, fill: "transparent", tabIndex: 0, role: "button", "aria-label": tooltipText(p), onMouseEnter: () => setHover(p.evaluation_id), onMouseLeave: () => setHover(null), onFocus: () => setHover(p.evaluation_id), onBlur: () => setHover(null), onClick: () => setHover(p.evaluation_id), onKeyDown: e => { if (e.key === 'Escape')
                            setHover(null); if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            setHover(p.evaluation_id);
                        } } },
                        React.createElement("title", null, tooltipText(p))))),
                Array.from({ length: data.rounds }, (_, i) => i % tickEvery === 0 ? React.createElement("text", { key: i, className: "as-chart-tick", x: x(i + .5), y: data.unscored ? bottom + 62 : bottom + 29, textAnchor: "middle" },
                    "\u7B2C ",
                    i + 1,
                    " \u6B21\u4F1A\u8BDD") : null)),
            point && React.createElement("div", { className: "as-chart-tooltip", role: "status", style: { left: Math.min(width - 250, Math.max(0, x(point.x) - 125)), top: Math.max(0, py(point) - 88) } },
                React.createElement("strong", { style: { color: point.color } },
                    point.actor,
                    " \u00B7 \u7B2C ",
                    point.generation,
                    " \u6B21\u4F1A\u8BDD"),
                React.createElement("span", null,
                    point.eligible ? '成绩 ' + score(point.score) : point.probe ? '探测评测，不计入曲线' : point.status === 'completed' ? '无有效成绩' : states[point.status] ?? point.status,
                    " \u00B7 \u7B2C ",
                    point.ordinal,
                    " \u6B21\u63D0\u4EA4"),
                React.createElement("span", { className: "as-code" }, point.candidate_id),
                React.createElement("small", null, submittedTime(point.submitted_at))),
            !points.length && !data.baseline && React.createElement("div", { className: "as-chart-empty" }, progress.sessions.length ? '等待开发评测结果' : '等待研究员开始研究')),
        React.createElement("div", { className: "as-chart-legend", "aria-label": "\u56FE\u4F8B\uFF0C\u70B9\u51FB\u7814\u7A76\u5458\u7A81\u51FA\u663E\u793A\u5176\u66F2\u7EBF" },
            data.series.map(s => React.createElement("button", { key: s.actor, className: "as-legend-item", "aria-pressed": focus === s.actor, onClick: () => setFocus(focus === s.actor ? null : s.actor) },
                React.createElement("span", { className: "as-legend-stroke", style: { borderColor: s.color, borderTopStyle: s.dash ? 'dashed' : 'solid' } }),
                React.createElement("strong", null, s.actor),
                React.createElement("span", null, score(s.best)))),
            data.baseline && React.createElement("span", { className: "as-legend-item as-legend-baseline" },
                React.createElement("span", { className: "as-legend-stroke" }),
                "\u57FA\u7EBF ",
                React.createElement("span", null, score(data.baseline.score)))),
        React.createElement("div", { className: "as-chart-caption" },
            "\u7EBF\u8868\u793A\u5386\u53F2\u6700\u4F73\uFF0C\u70B9\u8868\u793A\u6BCF\u6B21\u8BC4\u6D4B\uFF1B\u540C\u8F6E\u5185\u6309\u63D0\u4EA4\u987A\u5E8F\u7B49\u8DDD\u6392\u5217\uFF0C\u5386\u53F2\u4F1A\u8BDD\u70B9\u6DE1\u5316\u3002",
            data.unassigned > 0 ? ` ${data.unassigned} 次评测未关联研究员，可在记录中查看。` : '',
            !data.baseline ? ' 本数据集暂无有效基线成绩。' : ''));
}
function EvaluationTable({ rows }) {
    return !rows.length ? React.createElement("p", { className: "as-muted" }, "\u6682\u65E0\u8BC4\u6D4B\u8BB0\u5F55\u3002") : React.createElement("div", { className: "as-table-wrap" },
        React.createElement("table", null,
            React.createElement("thead", null,
                React.createElement("tr", null,
                    React.createElement("th", null, "\u5019\u9009 / \u7814\u7A76\u5458"),
                    React.createElement("th", null, "\u6570\u636E\u96C6"),
                    React.createElement("th", null, "\u63D0\u4EA4\u65F6\u95F4"),
                    React.createElement("th", null, "\u72B6\u6001"),
                    React.createElement("th", null, "\u5408\u6CD5 / \u603B\u4F8B\u6570"),
                    React.createElement("th", null, "\u6210\u7EE9"))),
            React.createElement("tbody", null, rows.map(v => React.createElement("tr", { key: v.evaluation_id },
                React.createElement("td", { title: v.evaluation_id },
                    v.candidate_id,
                    React.createElement("div", { className: "as-muted" }, v.actor ? `${v.actor} · 第 ${v.generation} 次会话` : v.candidate_id === 'baseline' ? '基线' : '主会话评测')),
                React.createElement("td", null, v.dataset_id),
                React.createElement("td", { title: v.time_source === 'started' ? '无研究员提交记录，显示评测开始时间' : undefined },
                    submittedTime(v.submitted_at),
                    v.time_source === 'started' ? ' *' : ''),
                React.createElement("td", null,
                    states[v.status] ?? v.status,
                    v.probe ? ' · 探测' : ''),
                React.createElement("td", null,
                    v.valid_cases ?? '—',
                    " / ",
                    v.total_cases ?? '—'),
                React.createElement("td", null, v.eligible ? score(v.score) : '—'))))),
        rows.some(v => v.time_source === 'started') && React.createElement("div", { className: "as-muted" }, "* \u6B64\u8BB0\u5F55\u663E\u793A\u8BC4\u6D4B\u5F00\u59CB\u65F6\u95F4\u3002"));
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
`;
function ArtifactPreview({ runId, item, kind, api }) {
    const [open, setOpen] = (0, react_1.useState)(false), [content, setContent] = (0, react_1.useState)(), [data, setData] = (0, react_1.useState)(), [error, setError] = (0, react_1.useState)('');
    (0, react_1.useEffect)(() => {
        if (!open)
            return;
        const abort = new AbortController();
        setError('');
        const request = (asset) => api('artifact', { runId, kind, id: item.id, asset }, abort.signal);
        Promise.all([request(kind === 'report' ? 'report' : 'image'), kind === 'figure' ? request('data') : Promise.resolve(undefined)])
            .then(([a, b]) => { if (!abort.signal.aborted) {
            setContent(a);
            setData(b);
        } })
            .catch(e => { if (!abort.signal.aborted)
            setError(e.message); });
        return () => abort.abort();
    }, [open, runId, item.id, kind, api]);
    const url = (v) => v.encoding === 'base64' ? `data:${v.mime};base64,${v.content}` : `data:${v.mime};charset=utf-8,${encodeURIComponent(v.content)}`;
    return React.createElement("details", { className: "as-details as-artifact", onToggle: e => setOpen(e.currentTarget.open) },
        React.createElement("summary", null,
            kind === 'report' ? '报告' : '图表',
            " \u00B7 ",
            item.id),
        error ? React.createElement("p", { role: "alert" }, error) : !content ? React.createElement("p", { className: "as-muted" }, "\u5C55\u5F00\u540E\u8BFB\u53D6\u5DF2\u767B\u8BB0\u6587\u4EF6") : React.createElement(React.Fragment, null,
            kind === 'figure' ? React.createElement("img", { className: "as-artifact-image", src: url(content), alt: item.id + ' · 已登记评测图表' }) : React.createElement("pre", { className: "as-report-preview" }, content.content),
            React.createElement("div", { className: "as-actions" },
                React.createElement("a", { className: "as-btn", download: content.filename, href: url(content) },
                    "\u4E0B\u8F7D",
                    kind === 'figure' ? '图像' : '报告'),
                data && React.createElement("a", { className: "as-btn", download: data.filename, href: url(data) }, "\u4E0B\u8F7D\u7ED8\u56FE\u6570\u636E")),
            React.createElement("p", { className: "as-muted" },
                "\u5F15\u7528 ",
                item.evaluation_ids.length,
                " \u9879\u8BC4\u6D4B \u00B7 ",
                item.manifest_path)));
}
exports.inject = ['slots', 'connection', 'layout', 'sessions', 'remote'];
function apply(ctx) { applyPanel(ctx); }
function applyPanel(ctx, options = { id: 'anysolvers', label: 'AnySolvers', im: false }) {
    const fields = panelFields(options.im);
    const selectionEvent = options.im ? 'anysolvers-im-selection' : 'research-runtime-selection';
    async function hostApi(method, payload, signal) {
        const result = await ctx.connection.rpc.call('/api', options.id + '/' + method, payload, signal);
        if (!result.ok)
            throw new Error(result.error.message);
        return result.value;
    }
    function Panel() {
        const [navigation, setNavigation] = (0, react_1.useState)(() => { try {
            return JSON.parse(sessionStorage.getItem(selectionEvent) ?? 'null');
        }
        catch {
            return null;
        } });
        (0, react_1.useEffect)(() => { const listener = (event) => { const value = event.detail; if (value?.implementationId === options.id) {
            setNavigation(value);
            setSelected(value.projectId);
        } }; window.addEventListener(selectionEvent, listener); return () => window.removeEventListener(selectionEvent, listener); }, []);
        const [projects, setProjects] = (0, react_1.useState)(), [selected, setSelected] = (0, react_1.useState)(navigation?.implementationId === options.id ? navigation.projectId : ''), [error, setError] = (0, react_1.useState)('');
        (0, react_1.useEffect)(() => {
            const abort = new AbortController();
            let timer;
            const load = async () => {
                try {
                    const result = await hostApi('projects', {}, abort.signal);
                    if (!abort.signal.aborted) {
                        setProjects(result);
                        setSelected(id => id || result.defaultProjectId || '');
                        setError('');
                    }
                }
                catch (e) {
                    if (!abort.signal.aborted)
                        setError(e instanceof Error ? e.message : String(e));
                }
                finally {
                    if (!abort.signal.aborted)
                        timer = setTimeout(load, 10000);
                }
            };
            void load();
            return () => { abort.abort(); clearTimeout(timer); };
        }, []);
        const [deployment, setDeployment] = (0, react_1.useState)();
        const [serviceBusy, setServiceBusy] = (0, react_1.useState)(false);
        (0, react_1.useEffect)(() => {
            if (!options.im)
                return;
            const abort = new AbortController();
            let timer;
            const read = async () => { try {
                const d = await hostApi('deployment', {}, abort.signal);
                if (!abort.signal.aborted)
                    setDeployment(d);
            }
            catch { }
            finally {
                if (!abort.signal.aborted)
                    timer = setTimeout(read, 5000);
            } };
            void read();
            return () => { abort.abort(); clearTimeout(timer); };
        }, []);
        async function manage(action) { setServiceBusy(true); try {
            await hostApi(action, {});
            setDeployment(await hostApi('deployment', {}));
            setError('');
        }
        catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
        finally {
            setServiceBusy(false);
        } }
        const project = projects?.projects.find(p => p.id === selected);
        return React.createElement("div", { className: "as-project-panel" },
            React.createElement("style", null, css),
            options.im && React.createElement("section", { className: "as-card" },
                React.createElement("h2", null, "\u5B9E\u4F8B\u670D\u52A1"),
                React.createElement("p", null, deployment ? `${deployment.running ? '运行中' : '已停止'} · ${deployment.mode} · ${deployment.version}` : '正在连接实例…'),
                deployment && React.createElement(React.Fragment, null,
                    React.createElement("p", { className: "as-muted" }, deployment.instance),
                    React.createElement("p", null,
                        "\u98DE\u4E66\u7FA4\uFF1A",
                        deployment.chats.map((c) => c.label || c.chat_id).join('、') || '尚未绑定，请让安装 agent 运行飞书接入向导'),
                    React.createElement("button", { className: "as-btn", disabled: serviceBusy, onClick: () => manage(deployment.running ? 'service-stop' : 'service-start') }, deployment.running ? '停止接入服务' : '启动服务'),
                    React.createElement("p", { className: "as-muted" }, "\u5347\u7EA7\u3001\u673A\u5668\u4EBA\u8BA4\u8BC1\u548C\u7FA4\u7ED1\u5B9A\u7531\u5B89\u88C5 agent \u6309 INSTALL.md \u64CD\u4F5C\u3002\u7814\u7A76\u4F7F\u7528\u5404\u81EA\u5C01\u5B58\u7248\u672C\u3002"),
                    deployment.source_health.map((h) => React.createElement("p", { key: h.id },
                        h.component,
                        "\uFF1A",
                        h.state,
                        " \u00B7 ",
                        h.detail)))),
            React.createElement("div", { className: "as-project-switch" },
                React.createElement("label", null,
                    "\u9879\u76EE ",
                    React.createElement("select", { "aria-label": "\u9009\u62E9\u9879\u76EE", value: selected, onChange: e => setSelected(e.target.value) }, projects?.projects.map(p => React.createElement("option", { key: p.id, value: p.id },
                        p.name,
                        p.id === projects.defaultProjectId ? ' · 最近执行' : '')))),
                React.createElement("div", { className: "as-muted as-project-path" }, project?.root),
                error && React.createElement("div", { role: "alert", className: "as-notice as-error" }, error)),
            project ? React.createElement(WorkspacePanel, { key: project.id + (navigation?.runId ?? ''), project: project, selectedRun: navigation?.implementationId === options.id && navigation?.projectId === project.id ? navigation.runId : undefined }) : React.createElement("div", { className: "as-empty" }, projects ? '暂无研究项目；在已绑定飞书群中派工后显示。' : '正在读取项目…'));
    }
    function WorkspacePanel({ project, selectedRun }) {
        const api = (method, payload, signal) => hostApi(method, { ...payload, projectId: project.id }, signal);
        const [catalog, setCatalog] = (0, react_1.useState)();
        const [study, setStudy] = (0, react_1.useState)((selectedRun ?? project.latestRun)?.split('/')[0] ?? '');
        const [tab, setTab] = (0, react_1.useState)('progress');
        const [config, setConfig] = (0, react_1.useState)();
        const [inputs, setInputs] = (0, react_1.useState)({});
        const [saved, setSaved] = (0, react_1.useState)();
        const [run, setRun] = (0, react_1.useState)(selectedRun ?? '');
        const [progress, setProgress] = (0, react_1.useState)();
        const [offset, setOffset] = (0, react_1.useState)(0);
        const [finalOffset, setFinalOffset] = (0, react_1.useState)(0);
        const [error, setError] = (0, react_1.useState)('');
        const [notice, setNotice] = (0, react_1.useState)('');
        const [busy, setBusy] = (0, react_1.useState)(false);
        const [auto, setAuto] = (0, react_1.useState)(true);
        const [configTick, setConfigTick] = (0, react_1.useState)(0);
        const [loading, setLoading] = (0, react_1.useState)(false);
        const [tick, setTick] = (0, react_1.useState)(0);
        const [submitted, setSubmitted] = (0, react_1.useState)(false);
        const generation = (0, react_1.useRef)(0);
        const actionLock = (0, react_1.useRef)(false);
        const refresh = () => setTick(n => n + 1);
        (0, react_1.useEffect)(() => { const abort = new AbortController(); api('catalog', {}, abort.signal).then(value => { if (!abort.signal.aborted) {
            setCatalog(value);
            setStudy(id => id || (value.studies.length === 1 ? value.studies[0].study_id : ''));
        } }).catch(e => { if (!abort.signal.aborted)
            setError(String(e.message)); }); return () => abort.abort(); }, [tick]);
        (0, react_1.useEffect)(() => {
            generation.current++;
            setConfig(undefined);
            setSaved(undefined);
            setProgress(undefined);
            setRun(selectedRun ?? '');
            setOffset(0);
            setFinalOffset(0);
            setError('');
            setNotice('');
            setSubmitted(false);
            if (!study)
                return;
            const abort = new AbortController();
            setLoading(true);
            api('configuration', { studyId: study }, abort.signal).then(value => {
                if (abort.signal.aborted)
                    return;
                setConfig(value);
                setSaved(value.saved ?? undefined);
                setInputs(Object.fromEntries(Object.entries(value.values).map(([k, v]) => [k, v == null ? '' : String(v)])));
                setRun(selectedRun ?? '');
                if (!value.existing_runs.length)
                    setTab('config');
            }).catch(e => { if (!abort.signal.aborted)
                setError(e.message); }).finally(() => { if (!abort.signal.aborted)
                setLoading(false); });
            return () => abort.abort();
        }, [study, configTick]);
        // Poll only while the progress page is visible; sequential reads cannot pile up.
        (0, react_1.useEffect)(() => {
            if (tab !== 'progress' || !study)
                return;
            const abort = new AbortController();
            let timer;
            const read = async () => {
                try {
                    const next = await api('catalog', {}, abort.signal);
                    if (abort.signal.aborted)
                        return;
                    setCatalog(next);
                    const chosen = run || next.studies.find(s => s.study_id === study)?.existing_runs.at(-1);
                    if (chosen) {
                        const p = await readProgress(api, chosen, offset, finalOffset, abort.signal);
                        if (!abort.signal.aborted) {
                            setProgress(p);
                            setError('');
                        }
                    }
                }
                catch (e) {
                    if (!abort.signal.aborted)
                        setError(e instanceof Error ? e.message : String(e));
                }
                finally {
                    if (auto && !abort.signal.aborted)
                        timer = setTimeout(read, 5000);
                }
            };
            void read();
            return () => { abort.abort(); clearTimeout(timer); };
        }, [study, run, tab, auto, tick, offset, finalOffset]);
        async function saveConfig() {
            if (!config || actionLock.current)
                return;
            const values = {};
            for (const [key] of fields) {
                const raw = inputs[key]?.trim() ?? '';
                if (!raw && key === 'evaluation_calls') {
                    values[key] = null;
                    continue;
                }
                const n = Number(raw);
                if (!raw || !Number.isSafeInteger(n) || n < 1) {
                    setError('请填写有效的正整数：' + fields.find(f => f[0] === key)[1]);
                    return;
                }
                values[key] = n;
            }
            const gen = generation.current;
            actionLock.current = true;
            setBusy(true);
            setError('');
            setNotice('');
            try {
                const value = await api('save', { studyId: study, revision: config.revision, values });
                if (gen !== generation.current)
                    return;
                setSaved(value);
                const latest = await api('configuration', { studyId: study });
                if (gen !== generation.current)
                    return;
                setConfig(latest);
                setSubmitted(false);
                setNotice(options.im ? '参数已保存为下一批次草稿。启动后由 Next 调度研究及终局 Worker。' : '参数已保存到当前项目。/as-research 将使用这些参数创建新 run，完成研究、final 与图表。');
            }
            catch (e) {
                if (gen === generation.current)
                    setError(e instanceof Error ? e.message : String(e));
            }
            finally {
                actionLock.current = false;
                setBusy(false);
            }
        }
        async function start() {
            if (!saved || !catalog || actionLock.current || submitted)
                return;
            actionLock.current = true;
            setBusy(true);
            setError('');
            setSubmitted(true);
            let sessionId = '';
            try {
                if (options.im) {
                    const reply = await api('start', { studyId: study, config: saved.config });
                    setNotice(`已提交 Next 研究任务 ${reply.job_id}；本页自动跟踪新批次及终局交付。`);
                    setRun('');
                    setOffset(0);
                    setFinalOffset(0);
                    setProgress(undefined);
                    setTab('progress');
                    refresh();
                    return;
                }
                sessionId = await ctx.sessions.create({ cwd: catalog.root, agentPreset: 'anysolvers-main' });
                const reply = await ctx.remote.commands.execute(sessionId, saved.command, []);
                if (!reply.ok)
                    throw new Error(reply.error.message);
                if (!reply.value || reply.value.result.kind !== 'success')
                    throw new Error(reply.value?.result.text ?? '原生命令未接受请求');
                setNotice(`研究已提交。主会话 ${sessionId} 可在左侧会话列表打开；本页将跟踪新批次。`);
                setRun(selectedRun ?? '');
                setOffset(0);
                setFinalOffset(0);
                setProgress(undefined);
                setTab('progress');
                refresh();
                await ctx.sessions.refresh();
            }
            catch (e) {
                setError(`${e instanceof Error ? e.message : String(e)}${sessionId ? ' 主会话：' + sessionId + '。请先核对会话，避免重复启动。' : ''}`);
            }
            finally {
                actionLock.current = false;
                setBusy(false);
            }
        }
        const selected = catalog?.studies.find(s => s.study_id === study);
        const researchers = progress ? researcherRows(progress) : [];
        const activeResearchers = researchers.filter(r => r.live).length;
        return React.createElement("div", { className: "as-panel" },
            React.createElement("style", null, css),
            React.createElement("div", { className: "as-wrap" },
                React.createElement("header", { className: "as-head" },
                    React.createElement("h1", null, "\u7814\u7A76\u5DE5\u4F5C\u53F0"),
                    React.createElement("button", { className: "as-btn", "aria-label": "\u5237\u65B0\u5217\u8868\u4E0E\u8FDB\u5EA6", onClick: refresh, disabled: busy }, "\u5237\u65B0")),
                React.createElement("div", { className: "as-toolbar as-navigation" },
                    React.createElement("select", { className: "as-select", "aria-label": "\u9009\u62E9\u7814\u7A76", value: study, disabled: busy, onChange: e => setStudy(e.target.value) },
                        React.createElement("option", { value: "" }, "\u9009\u62E9\u5DF2\u521D\u59CB\u5316\u7684\u7814\u7A76"),
                        catalog?.studies.map(s => React.createElement("option", { key: s.study_id, value: s.study_id }, s.study_id))),
                    React.createElement("div", { className: "as-tabs", role: "tablist", "aria-label": "\u7814\u7A76\u9875\u9762" },
                        React.createElement("button", { className: "as-btn", role: "tab", "aria-selected": tab === 'progress', onClick: () => setTab('progress') }, "\u7814\u7A76\u8FDB\u5EA6"),
                        React.createElement("button", { className: "as-btn", role: "tab", "aria-selected": tab === 'config', onClick: () => setTab('config') }, "\u8FD0\u884C\u914D\u7F6E"))),
                error && React.createElement("div", { role: "alert", className: "as-notice as-error" }, error),
                notice && React.createElement("div", { role: "status", className: "as-notice as-success" }, notice),
                catalog?.invalid.length ? React.createElement("div", { className: "as-notice" },
                    "\u6709 ",
                    catalog.invalid.length,
                    " \u4E2A\u7814\u7A76\u672A\u901A\u8FC7\u521D\u59CB\u5316\u6821\u9A8C\uFF1A",
                    catalog.invalid.map(x => x.study_id + '：' + x.reason).join('；')) : null,
                !study ? React.createElement("div", { className: "as-empty" },
                    React.createElement("h2", null, catalog?.studies.length ? '从上方选择一项研究' : '尚无已封存研究'),
                    React.createElement("div", { className: "as-muted" },
                        "\u4F7F\u7528\u6807\u51C6\u521D\u59CB\u5316\u6D41\u7A0B\u51C6\u5907 task\u3001datasets \u4E0E ideas\uFF0C\u518D\u6765\u8FD9\u91CC\u914D\u7F6E\u548C\u542F\u52A8\u3002",
                        React.createElement("br", null),
                        "\u5DE5\u4F5C\u53F0\u4E0D\u4F1A\u81EA\u52A8\u9009\u62E9\u95EE\u9898\u6216\u521B\u5EFA\u6837\u4F8B\u3002")) : loading ? React.createElement("div", { className: "as-card", role: "status" }, "\u6B63\u5728\u8BFB\u53D6\u7814\u7A76\u914D\u7F6E\u2026") : null,
                study && tab === 'config' && config && React.createElement("section", { className: "as-card" },
                    React.createElement("div", { className: "as-title-row" },
                        React.createElement("h2", null, "\u4E0B\u4E00\u6279\u6B21\u7684\u8FD0\u884C\u53C2\u6570"),
                        React.createElement("button", { className: "as-btn", disabled: busy, onClick: () => setConfigTick(v => v + 1) }, "\u91CD\u8BFB\u53C2\u6570")),
                    React.createElement("p", { className: "as-muted" },
                        config.model.model,
                        " \u00B7 ",
                        config.model.reasoning_effort,
                        " \u00B7 ",
                        config.executor === 'ssh-process' ? 'SSH / resource-limited process (no remote sandbox)' : config.executor === 'ssh-docker' ? 'SSH Docker' : options.im && config.executor === 'dsh-native' ? 'DSH 原生沙箱 · 无网络 / GPU · 内存 RSS 监测' : '本地 Docker · 单核容器，无网络 / GPU'),
                    React.createElement("div", { className: "as-grid" }, fields.map(([key, label, hint, max]) => React.createElement("label", { className: "as-field", key: key },
                        React.createElement("span", null, label),
                        React.createElement("input", { "aria-label": label, type: "number", min: 1, max: max, step: 1, disabled: busy, value: inputs[key] ?? '', placeholder: key === 'evaluation_calls' ? '不限' : undefined, onChange: e => { setInputs(v => ({ ...v, [key]: e.target.value })); setSaved(undefined); setNotice(''); } }),
                        React.createElement("small", null, hint)))),
                    React.createElement("div", { className: "as-actions" },
                        React.createElement("button", { className: "as-btn", onClick: saveConfig, disabled: busy }, busy ? '处理中…' : '保存运行参数'),
                        React.createElement("button", { className: "as-btn as-primary", onClick: start, disabled: !saved || busy || submitted }, submitted ? '已提交，请查看进度' : selected?.existing_runs.length ? '启动新批次' : '启动研究')),
                    React.createElement("p", { className: "as-muted" }, "\u4FDD\u5B58\u4E0D\u542F\u52A8\u7814\u7A76\u3002\u542F\u52A8\u540E\u9884\u7B97\u56FA\u5B9A\uFF1B\u542F\u52A8\u5373\u6388\u6743\u672C\u6B21\u7814\u7A76\u53CA\u6700\u7EC8\u6D4B\u8BD5\u3001\u62A5\u544A\u548C\u56FE\u8868\u3002\u547D\u4EE4\u4EC5\u4F7F\u7528\u5DF2\u4FDD\u5B58\u53C2\u6570\uFF1B\u672A\u4FDD\u5B58\u7684\u7F16\u8F91\u4E0D\u751F\u6548\u3002"),
                    saved && React.createElement("div", { className: "as-notice as-success" },
                        React.createElement("div", null,
                            "\u672C\u6B21\u914D\u7F6E\uFF1A",
                            React.createElement("span", { className: "as-code" }, saved.config)),
                        !options.im && React.createElement(React.Fragment, null,
                            React.createElement("div", null, "\u4E5F\u53EF\u5728\u65B0\u7684 AnySolvers \u4E3B\u4F1A\u8BDD\u53D1\u9001\uFF1A"),
                            React.createElement("code", { className: "as-code" }, saved.command))),
                    React.createElement("details", { className: "as-details" },
                        React.createElement("summary", null, "\u914D\u7F6E\u6765\u6E90\u4E0E\u6267\u884C\u73AF\u5883"),
                        React.createElement("div", { className: "as-code" },
                            config.source,
                            React.createElement("br", null),
                            config.image))),
                study && tab === 'progress' && React.createElement(React.Fragment, null,
                    React.createElement("div", { className: "as-toolbar as-runbar" },
                        React.createElement("select", { "aria-label": "\u9009\u62E9\u6279\u6B21", value: run, onChange: e => { setRun(e.target.value); setOffset(0); setFinalOffset(0); setProgress(undefined); } },
                            React.createElement("option", { value: "" },
                                "\u6700\u65B0\u6279\u6B21",
                                selected?.existing_runs.length ? ' · ' + selected.existing_runs.at(-1).split('/').at(-1) : ''),
                            selected?.existing_runs.map(r => React.createElement("option", { key: r, value: r },
                                "\u6279\u6B21 ",
                                r.split('/').at(-1)))),
                        React.createElement("label", { className: "as-refresh", title: progress ? `每 5 秒更新，最近读取于 ${new Date(progress.updated_at).toLocaleTimeString()}` : '每 5 秒更新' },
                            React.createElement("input", { type: "checkbox", checked: auto, onChange: e => setAuto(e.target.checked) }),
                            "\u81EA\u52A8\u66F4\u65B0")),
                    !progress ? React.createElement("div", { className: "as-empty" },
                        React.createElement("h2", null, run || selected?.existing_runs.length ? '正在读取批次状态' : '尚无研究批次'),
                        React.createElement("div", { className: "as-muted" }, run || selected?.existing_runs.length ? '进度来自后端记录。' : '在运行配置页保存参数并启动，批次建立后会出现在这里。')) : React.createElement(React.Fragment, null,
                        React.createElement("div", { className: "as-progress-heading" },
                            React.createElement("strong", null, progress.terminal?.complete ? '研究交付已完成' : phaseNames[progress.phase] ?? progress.phase),
                            React.createElement("span", { className: "as-muted" },
                                "\u5DF2\u5B8C\u6210 ",
                                progress.completed_sessions,
                                " / ",
                                progress.planned_sessions,
                                " \u6B21\u4F1A\u8BDD",
                                progress.phase === 'orchestrate' ? ` · ${activeResearchers} 位研究员在进行` : '')),
                        progress.missing_sessions > 0 && progress.phase !== 'orchestrate' && React.createElement("div", { className: "as-progress-note" },
                            "\u5F00\u53D1\u7814\u7A76\u5DF2\u7ED3\u675F\uFF0C\u8BA1\u5212\u4E2D\u4ECD\u6709 ",
                            progress.missing_sessions,
                            " \u6B21\u4F1A\u8BDD\u672A\u542F\u52A8\u3002"),
                        options.im && progress.workers?.length ? React.createElement("div", { className: "as-progress-note" }, progress.workers.map(w => React.createElement("div", { key: w.id },
                            { initialize: '初始化', research: '研究', terminal: '终局' }[w.stage],
                            " Worker \u00B7 ",
                            states[w.state] ?? w.state,
                            w.detail ? ' · ' + w.detail : ''))) : null,
                        React.createElement(ResearchChart, { key: 'chart/' + progress.run_id, progress: progress }),
                        progress.failed_sessions > 0 && React.createElement("div", { className: "as-notice" },
                            "\u6709 ",
                            progress.failed_sessions,
                            " \u6B21\u7814\u7A76\u4F1A\u8BDD\u5931\u8D25\uFF0C\u8BF7\u5728\u4E3B\u4F1A\u8BDD\u6838\u5BF9\u539F\u56E0\u3002"),
                        progress.pending_requests.length > 0 && React.createElement("div", { className: "as-notice" },
                            "\u6709 ",
                            progress.pending_requests.length,
                            " \u4E2A\u672A\u51B3\u8BC4\u6D4B\uFF0C\u8BF7\u5728\u4E3B\u4F1A\u8BDD\u6838\u5BF9\u539F\u8BF7\u6C42\u3002",
                            React.createElement("details", { className: "as-details" },
                                React.createElement("summary", null, "\u67E5\u770B\u8BF7\u6C42"),
                                React.createElement("div", { className: "as-code" }, progress.pending_requests.join('、')),
                                "\u6267\u884C\u72B6\u6001\u672A\u786E\u5B9A\u524D\uFF0C\u8BF7\u52FF\u66F4\u6362 ID \u91CD\u8BD5\u3002")),
                        React.createElement("div", { className: "as-disclosures", key: 'records/' + progress.run_id },
                            React.createElement("details", { className: "as-disclosure" },
                                React.createElement("summary", null,
                                    React.createElement("span", null, "\u8FD0\u884C\u65F6\u8BC4\u6D4B\u8BB0\u5F55"),
                                    React.createElement("span", { className: "as-muted" },
                                        progress.total_development_evaluations,
                                        " \u6B21"),
                                    React.createElement(Chevron, null)),
                                React.createElement("div", { className: "as-disclosure-body" },
                                    React.createElement(EvaluationTable, { rows: progress.evaluations }),
                                    (progress.offset > 0 || progress.next_offset !== null) && React.createElement("div", { className: "as-actions" },
                                        React.createElement("button", { className: "as-btn", disabled: offset !== progress.offset || offset === 0, onClick: () => setOffset(Math.max(0, offset - 50)) }, "\u4E0A\u4E00\u9875"),
                                        React.createElement("span", { className: "as-muted" },
                                            progress.offset + 1,
                                            "\u2013",
                                            progress.offset + progress.evaluations.length,
                                            " / ",
                                            progress.total_development_evaluations),
                                        React.createElement("button", { className: "as-btn", disabled: offset !== progress.offset || progress.next_offset === null, onClick: () => setOffset(progress.next_offset) }, "\u4E0B\u4E00\u9875")))),
                            React.createElement("details", { className: "as-disclosure" },
                                React.createElement("summary", null,
                                    React.createElement("span", null, "\u6700\u7EC8\u8BC4\u6D4B\u8BB0\u5F55\u4E0E\u62A5\u544A"),
                                    React.createElement("span", { className: "as-muted" }, progress.terminal ? terminalNames[progress.terminal.phase] : progress.reports.length ? '已有报告' : progress.final_coverage.complete ? '测试已完成' : progress.final_coverage.covered_pairs > 0 ? '测试尚未完成' : '尚未测试'),
                                    React.createElement(Chevron, null)),
                                React.createElement("div", { className: "as-disclosure-body" },
                                    React.createElement(EvaluationTable, { rows: progress.final_evaluations }),
                                    (progress.final_offset > 0 || progress.final_next_offset !== null) && React.createElement("div", { className: "as-actions" },
                                        React.createElement("button", { className: "as-btn", disabled: finalOffset !== progress.final_offset || finalOffset === 0, onClick: () => setFinalOffset(Math.max(0, finalOffset - 50)) }, "\u4E0A\u4E00\u9875"),
                                        React.createElement("span", { className: "as-muted" },
                                            progress.final_offset + 1,
                                            "\u2013",
                                            progress.final_offset + progress.final_evaluations.length,
                                            " / ",
                                            progress.total_final_evaluations),
                                        React.createElement("button", { className: "as-btn", disabled: finalOffset !== progress.final_offset || progress.final_next_offset === null, onClick: () => setFinalOffset(progress.final_next_offset) }, "\u4E0B\u4E00\u9875")),
                                    progress.selection && React.createElement("p", { className: "as-muted" },
                                        "\u51BB\u7ED3\u5019\u9009\uFF1A",
                                        React.createElement("code", { className: "as-code" }, progress.selection.candidate_id),
                                        " \u00B7 \u5F00\u53D1\u6210\u7EE9 ",
                                        score(progress.selection.score)),
                                    React.createElement("p", { className: "as-muted" },
                                        "\u6700\u7EC8\u6D4B\u8BD5\u8986\u76D6 ",
                                        progress.final_coverage.covered_pairs,
                                        " / ",
                                        progress.final_coverage.expected_pairs,
                                        " \u4E2A\u7EC4\u5408\uFF08\u57FA\u7EBF / \u51BB\u7ED3\u5019\u9009 \u00D7 \u6700\u7EC8\u6570\u636E\u96C6\uFF09\u3002"),
                                    progress.terminal && !progress.terminal.final_authorized && React.createElement("p", { className: "as-muted" }, "\u672C\u6279\u914D\u7F6E\u4EC5\u5141\u8BB8\u5F00\u53D1\u7814\u7A76\uFF0C\u62A5\u544A\u4E0E\u56FE\u8868\u4E0D\u4EE3\u8868\u72EC\u7ACB\u6700\u7EC8\u6D4B\u8BD5\u3002"),
                                    !progress.final_coverage.complete && progress.terminal?.final_authorized !== false && React.createElement("p", { className: "as-muted" }, "\u6700\u7EC8\u6D4B\u8BD5\u7531\u8BE5\u8FD0\u884C\u7684\u4E3B\u4F1A\u8BDD\u5728\u7814\u7A76\u7ED3\u675F\u540E\u7EE7\u7EED\u6267\u884C\uFF1B\u82E5\u4E2D\u65AD\uFF0C\u8BF7\u5728\u539F\u4E3B\u4F1A\u8BDD\u67E5\u770B\u672A\u51B3\u5DE5\u4F5C\u5E76\u7EE7\u7EED\u3002"),
                                    progress.terminal ? React.createElement(React.Fragment, null,
                                        progress.terminal.reports.map(item => React.createElement(ArtifactPreview, { key: 'report/' + item.id, runId: progress.run_id, item: item, kind: "report", api: api })),
                                        progress.terminal.figures.map(item => React.createElement(ArtifactPreview, { key: 'figure/' + item.id, runId: progress.run_id, item: item, kind: "figure", api: api })),
                                        !progress.terminal.figures.length && React.createElement("p", { className: "as-muted" }, "\u5C1A\u65E0\u5DF2\u767B\u8BB0\u56FE\u8868\u3002\u4E3B\u4F1A\u8BDD\u5B8C\u6210\u62A5\u544A\u540E\uFF0C\u5C06\u4F7F\u7528\u53EF\u89C6\u5316\u63D2\u4EF6\u751F\u6210\u3002")) : progress.reports.map(r => React.createElement("div", { className: "as-code", key: r.id },
                                        "\u62A5\u544A\uFF1Aruns/",
                                        progress.run_id,
                                        "/",
                                        r.manifest_path)))),
                            React.createElement("details", { className: "as-disclosure" },
                                React.createElement("summary", null,
                                    React.createElement("span", null, "\u672C\u6B21\u914D\u7F6E"),
                                    React.createElement("span", { className: "as-muted" }, "\u672C\u6279\u6B21\u56FA\u5B9A"),
                                    React.createElement(Chevron, null)),
                                React.createElement("div", { className: "as-disclosure-body" },
                                    React.createElement("div", { className: "as-muted" },
                                        "\u5269\u4F59\u8BC4\u6D4B\u6B21\u6570 ",
                                        progress.remaining_evaluations ?? '不限',
                                        " \u00B7 \u65B0\u5DE5\u4F5C\u51C6\u5165\u5269\u4F59 ",
                                        duration(progress.remaining_run_s)),
                                    React.createElement("div", { className: "as-parameters" }, fields.map(([key, label]) => React.createElement("div", { className: "as-parameter", key: key },
                                        React.createElement("span", null, label),
                                        React.createElement("span", null, progress.values[key] ?? '不限')))),
                                    React.createElement("p", { className: "as-muted" },
                                        progress.model.model,
                                        " \u00B7 ",
                                        progress.model.reasoning_effort))),
                            React.createElement("details", { className: "as-disclosure" },
                                React.createElement("summary", null,
                                    React.createElement("span", null, "\u7814\u7A76\u5458\u4F1A\u8BDD"),
                                    React.createElement("span", { className: "as-muted" },
                                        researchers.length,
                                        " \u4F4D \u00B7 \u5404\u81EA\u63A8\u8FDB"),
                                    React.createElement(Chevron, null)),
                                React.createElement("div", { className: "as-disclosure-body" },
                                    researchers.map(r => React.createElement("details", { className: "as-actor", key: progress.run_id + '/' + r.actor },
                                        React.createElement("summary", null,
                                            React.createElement("span", { className: "as-dot", "data-live": r.live, "data-issue": r.issue }),
                                            React.createElement("div", { className: "as-actor-name" },
                                                React.createElement("strong", null, r.actor),
                                                React.createElement("span", { className: "as-muted" },
                                                    r.live ? `第 ${r.latest.generation} 次会话` : `已完成 ${r.completed} / ${r.planned} 次会话`,
                                                    r.live && progress.research_time[r.latest.session_id] ? ` · 研究时间剩余 ${duration(progress.research_time[r.latest.session_id]?.remaining_s)}` : '')),
                                            React.createElement("span", { className: "as-actor-status", "data-live": r.live, "data-issue": r.issue }, r.status),
                                            React.createElement(Chevron, null)),
                                        React.createElement("div", { className: "as-table-wrap" },
                                            React.createElement("table", { "aria-label": r.actor + ' 的会话历史' },
                                                React.createElement("thead", null,
                                                    React.createElement("tr", null,
                                                        React.createElement("th", null, "\u4E2A\u4EBA\u4F1A\u8BDD"),
                                                        React.createElement("th", null, "\u72B6\u6001"),
                                                        React.createElement("th", null, "\u7814\u7A76\u7528\u65F6"),
                                                        React.createElement("th", null, "\u8BC4\u6D4B\u7B49\u5F85 / \u6267\u884C"),
                                                        React.createElement("th", null, "\u5269\u4F59\u7814\u7A76\u65F6\u95F4"))),
                                                React.createElement("tbody", null, r.history.map(s => { const time = progress.research_time[s.session_id]; return React.createElement("tr", { key: s.session_id },
                                                    React.createElement("td", { title: s.session_id },
                                                        "\u7B2C ",
                                                        s.generation,
                                                        " \u6B21"),
                                                    React.createElement("td", null, states[s.status] ?? s.status),
                                                    React.createElement("td", null, duration(time?.active_research_s)),
                                                    React.createElement("td", null, duration(time?.evaluation_wait_s)),
                                                    React.createElement("td", null, duration(time?.remaining_s))); })))))),
                                    !researchers.length && React.createElement("p", { className: "as-muted" }, "\u5C1A\u672A\u767B\u8BB0\u7814\u7A76\u5458\u4F1A\u8BDD\u3002"),
                                    researchers.length > 0 && (progress.values.researchers ?? 0) > researchers.length && React.createElement("div", { className: "as-muted" },
                                        "\u53E6\u6709 ",
                                        (progress.values.researchers ?? 0) - researchers.length,
                                        " \u4E2A\u7814\u7A76\u5458\u540D\u989D\u5C1A\u672A\u542F\u52A8\u3002"))))))));
    }
    function Icon({ size = 18 }) { return React.createElement("svg", { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.6", "aria-hidden": "true" },
        React.createElement("path", { d: "M9 3h6M10 3v7L4 19a1.3 1.3 0 0 0 1 2h14a1.3 1.3 0 0 0 1-2l-6-9V3M7 15h10" }),
        React.createElement("circle", { cx: "10", cy: "18", r: ".5" })); }
    ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: options.id, id: options.id }, Panel));
    ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({ name: 'sidebar.panellist', id: options.id, order: options.im ? 36 : 35, label: options.label }, Icon));
}

return {...module.exports,inject:['slots','connection','layout'],apply:ctx=>module.exports.applyPanel(ctx,{id:'anysolvers-im',label:'AnySolvers-IM',im:true})};}});
