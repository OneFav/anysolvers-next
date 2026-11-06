import type {JsonObject} from '../services.js'
export function researchPlan(runId:string,profile:JsonObject):string {
  return `用户已通过 /as-research 明确授权一次完整研究、final 最终测试、报告和可视化。初始化检定已通过，本次 run 已由程序创建：${runId}。不要再次调用 study_launch 或 batch_init，不修改预算或初始化。所有工具自动路由至本会话的工作区。配置与分配：${JSON.stringify(profile)}。
先 study_check、study_config，并用 study_module 读取 research、final、visualization 的要求。开发基线：对每个 purpose=anchor 的数据集 baseline_measure，ID 使用 as-baseline-数据集ID。随后 research_start(run_id=${runId}, assignments=上述 assignments, wait=true)。各研究员按自己的个人轮次异步续接，不设同轮屏障；每轮结束由主会话按selections方法调用study_selection_context并通过study_selection_report_context/study_selection_report_submit只为本轮最后checkpoint指定的最终封存候选写独立方法与成绩报告（含最后一轮），再通过study_selection_submit生成封存下一轮研究idea；研究员只提供本轮笔记和建议；等所有会话及评测请求结清后，study_audit 与 study_check。
然后 final_select 冻结合格候选；final_status 查看现有覆盖；对每个封存 final 数据集，仅基线与冻结候选各 final_measure 一次，ID 使用 as-final-候选ID-数据集ID。用户在命令启动时已授权这个新 run 的终局，权限服务会校验本次配置与主会话身份。研究员不可读 final 反馈，不重启研究。有限评测预算须预留 anchors.length + 2*finals.length 次给基线及终局，先把此要求传达给研究员。
使用 final_evidence 读取可信证据，按 final 契约 final_write、final_deliver 交付报告。用 visualization_render、visualization_deliver 生成并登记基线/冻结候选的最终对比图；最后 final_status.complete 必须为 true 才宣告交付完成。若执行失败、存在未决请求或没有合格候选，停止依赖步骤，明确报告阻塞与原请求 ID；不更换 ID 绕过，不伪造终局成功。`
}
export function formatPreflight(result:JsonObject):string {
  const issues=Array.isArray(result.issues)?result.issues:[]
  return '初始化检定未通过；未创建 run，未启动研究员。\n'+issues.map((v:any)=>`• ${v.path}：${v.message}`).join('\n')
    +(Array.isArray(result.choices)&&result.choices.length?'\n可选研究：'+result.choices.join('、'):'')
}
