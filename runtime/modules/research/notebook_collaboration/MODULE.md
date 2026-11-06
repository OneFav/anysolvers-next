---
version: 3.2.0
execution:
  mode: main
  delegates:
    researcher: research
defaults:
  recovery_proof: null
parameters: schemas/params.json
capabilities:
  session_experiments: true
  private_notebooks: true
  shared_findings: true
id: notebook-collaboration
kind: research
accepts:
- run_config
- task
- ideas
- candidate
- evaluation
- iteration
produces:
- candidate
contracts:
  candidate: schemas/candidate.json
  notebook: schemas/notebook.json
  share: schemas/share.json
  reuse: schemas/reuse.json
  request: schemas/request.json
handlers:
  direct: client.py
  begin_session: runtime.py
  activate_session: runtime.py
  end_session: runtime.py
  validate_delivery: runtime.py
  exchange: runtime.py
  admit: runtime.py
  audit: runtime.py
  recovery_context: recovery.py
  remaining_admission: recovery.py
  replay_guard: recovery.py
files:
- RESEARCHER.md
- sessions.py
- runtime.py
- recovery.py
---

# 笔记续接与共享发现协作

本模块是唯一原生研究员管理者。主会话只在初始化、会话启动/结束/续接、预算边界、异常和最终检查时介入；不转发日常评测或共享消息。

1. 主会话 check/status，核验预算与原生进程，通过 begin-session 登记 actor。按返回 spawn_options 真正启动原生 subagent，默认 gpt-6.1-sol/high/fork_turns=none；随后立即 `tools/run.py activate-session RUN --actor ACTOR --native-id ACTUAL_ID` 绑定实际会话并开始计时。重复绑定同一 ID 不重置计时，未激活前不能评测。轮数指会话数，默认人数/轮数仍为 2×2，后续从配置覆盖。
2. 给出 RUN_ID、session_id、idea 或上一笔记指定的 iteration、任务与开发集材料、RESEARCHER.md 和预算。研究员不读 final、别人私有记录、其他项目，不再委派。
3. 研究员直接 evaluate：工具自动封存候选、预约预算、三槽排队、沙箱执行并返回摘要。研究员直接 result 按需读详情，在同一原生会话修改算法并继续测量。主会话不逐次接收草稿或发送评分；评测次数默认不限，每次和每例仍受初始化设置的执行时限约束。
4. 研究员直接读/写 findings、share、reuse、iteration、checkpoint。公开源码复用先自动登记，不需人工确认；每条声明附真实来源，下一代算法不是调度计划。
5. 每轮默认 40 分钟有效研究时间（session_wall_s=2400）。从实际 activate 开始，程序按可信时间记录扣除评测前置检查、排队、执行及等待响应的区间并集；启动等待不计入。研究员用 context 的 research_time 查看余额，不能自行上报暂停。到期不杀原生进程，只禁止新候选和新评测；允许读结果、写 iteration/checkpoint、分享已有发现并正常结束。主会话核对没有未决请求，end-session 保留实际 native_id。下一会话用全新上下文读取最新笔记和已封存 iteration。
6. 各 actor 独立推进。原生研究模型可并发，评测默认三槽；评测中元数据锁释放。整批准入窗口默认 96 小时，从 init 开始，包括基线和终局；到期不再开始新工作，已完成结果和收尾接口仍可用。进程中断或未决尝试不重放，交主会话处理。
7. 所有会话与请求结束后 audit/check，主会话交 final 冻结与测量，再生成报告和图。最终反馈不进入研究。

runtime.py 负责会话准入与不可变记录；sessions.py 是共用生命周期校验；client.py 是研究员的固定工具入口。它们都不调用模型，也不含另一套后台 loop。CLI 会话范围校验不等于宿主文件隔离。

首轮中断后的独立维护接续可在新版本、新批次封存 `recovery_proof` 文件引用。证明必须固定原初始化、3 项预算、每名 actor 已消耗的有效时间、原总截止、历史证据和不可重放的失败源码。新批次第一个会话扣除原已耗时间，以后的会话保持原限额；累计轮数不得重置增加。每人只收到自己的固定恢复上下文，原始研究会话与笔记仍保留引用。模型不能生成或改写该证明，也不能将丢失结果补成分数。只有本 research 方法创建新原生研究员。

## research-directions 方法的轮次交接（优先于旧 iteration 续接说明）

当 selections.capabilities.coordinator_authored=true：研究员每轮只写 checkpoint（next_iteration_id=null），记录本轮起点、路线变化、证据与建议。研究主会话按 selections 契约生成下一轮 idea，调用 study_selection_context/study_selection_submit 封存；驱动按 actor 独立等待并消费该决定，不设全局轮次屏障。下一原生会话读取自己的 starting_idea 和上一笔记。research/ideas/<session>.json 保存每轮绑定及正文哈希，observer 可只读采集。候选是否继承父算法与 idea 的演变谱系是两回事。旧 researcher_authored 方法仍按其原契约续接。

当 selections.capabilities.method_reports=true，每轮结束由 selection 主会话读取本轮最后一份checkpoint指定的最终封存候选源码并写 research/reports/<session>.json 方法短报告，附工具核验的开发成绩；包括最后一轮。研究员无须另写此报告，但结束前的checkpoint.candidate_id必须明确本轮最终方法（可保留本人以前轮次的候选），null表示没有封存方法。中间版本不逐项进入报告。
