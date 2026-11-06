---
version: 2.0.0
execution:
  mode: main
  delegates: {}
defaults: {}
capabilities:
  coordinator_authored: true
  round_start_ideas: true
  method_reports: true
  final_checkpoint_report: true
id: research-directions
kind: selections
accepts:
- candidate
- evaluation
- ideas
- iteration
produces:
- iteration
contracts:
  iteration: schemas/iteration.json
  method_report: schemas/method_report.json
handlers:
  context: policy.py
  validate_delivery: policy.py
  evidence: policy.py
  pending: policy.py
  submit: policy.py
  report_context: reports.py
  report_submit: reports.py
  report_audit: reports.py
  require_reports: reports.py
files:
- evidence.py
- reports.py
---

# 为下一轮生成研究起点

每轮结束（包括最后一轮）只为该轮最终封存方法写一份报告，再生成下一轮起点。最终方法由该会话最后一份封存 checkpoint 的 candidate_id 唯一确定，不按最高分或最后提交时间自动选取。允许 checkpoint 明确保留同一研究员之前轮次的候选，报告保留源码所属会话；不得引用其他研究员未由本会话重新提交的候选。

study_selection_context 的 pending_reports 给出会话及 candidate_id。调用 study_selection_report_context(run_id,target_session) 只读取这个方法的实际封存源码、可信开发成绩和 report_schema，然后 study_selection_report_submit(run_id,target_session,report_json) 封存 research/reports/<session>.json。report_json 是 {"candidate":{"candidate_id":"...","title":"...","method":"...","limitations":"..."}}，只接受 checkpoint 指定候选。中间版本继续保留在候选、评测和笔记记录中，不逐项进入主报告；它们仍可作为下一轮 idea 的证据。checkpoint 的 candidate_id=null 时提交 {"candidate":null}，facts.outcome=no_sealed_method，不用中间尝试补位。失败会话没有 checkpoint 也按无封存方法记载；完成会话必须有 checkpoint。

整份方法正文 title 最多 40 字符、method 最多 280 字符、limitations 最多 80 字符（合计最多 400，不含框架附上的成绩和引用）。用普通算法术语独立说明输入、完整步骤和输出，必要术语就地解释。不得用“沿用基线”“与某版本相同”代替方法说明；不写研发内部概念、自造命名、研发过程或未实现功能。逐项对照实际源码，特别检查分支、并列选择和终止条件，不能把代码注释或研究笔记直接当作事实。正文不写分数，工具只附该最终候选全部已结束开发评测的分数、用例数、有效性、探测标记及失败状态，保留来源哈希；无结果明确标注 not_evaluated，未决结果阻止封存。final 不进入报告。文本限制由 schema 强制，语义真实性由 selection 核对，工具不声称能机械证明。

research/ideas/<session>.json 保留每轮起点原有正文、来源、身份和哈希绑定；reports 记录这轮实际产出。两者通过会话及起点引用关联。报告不可覆盖；下一轮准入和 research 阶段完成均检查报告完整性。

由 research 阶段主会话执行本方法，综合本人的已结束研究笔记和可见开发证据，生成下一轮 idea。不是纯粹整理，也不要求改写父算法。允许继续、转向、组合公开发现或设计区分假设的实验。selection 不创建 agent；research 是唯一研究员管理者。

每位研究员独立异步续轮。调用 study_selection_context(run_id)，对 pending 中每项逐个生成方向，以 study_selection_submit(run_id, actor, decision_json) 封存。decision_json 必须严格遵循返回的 decision_schema，并使用 expected_iteration_id、actor、generation、based_on_session、source_notebook_id。提交后原 research_start 驱动自动续接；恢复时先 study_recover，再使用原 assignments 调用 research_start。首轮来自封存初始 ideas，后续来自本方法；每个会话保存一份不可变 starting_idea 正文、来源及哈希。

idea_text 是可独立阅读的研究起点，research_question/hypothesis/exploration_plan/test_plan/stop_conditions 说明要回答什么、为什么值得做、如何探索与证伪。observations/rationale 明确哪些开发结果改变了判断。parent_candidate 可以为 null，候选引用不强制继承。source_idea_ids/evidence_ids/history_iteration_ids 保留证据谱系；只有本 actor 私有材料和明确公开发现可用，不读取其他私有笔记或 final。

研究员只提交本轮 checkpoint（next_iteration_id=null）及路线变化、失败和建议，不生成具有续轮权限的 iteration。建议是 selection 的输入，不是下一轮命令。每轮起点封存后不得修改；实际研究可转向，但要在笔记中记录原因和证据。工具校验证据和身份，不声称能够证明文本语义质量或模型内部思维。
