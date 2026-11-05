# Observer

Observer 将研究证据与 Router 当前的人类上下文 K 联系起来，关注可能改变人的先验、研究判断或实验设计的信息。正常进展、未分享的新想法、负结果和成绩下降都可以成为证据。它不是第四个阶段 Worker，不委派研究员、不运行算法、不修改框架、不向研究员注入信息，也不直接发送飞书。

## 接入与配置

已在本机核对 `dsh --version = 0.2.1-rc.5`，Node `v24.19.0`，session 格式 `4`。`controlplane.host.patch(observer=True)` 挂载独立的 `anysolvers-observer` preset 和 `lib/plugins/observer-runner.js`；该进程只有 `observer_read`、`observer_commit` 两个模型工具，不挂载 research runtime、研究控制器或执行器。研究模块仍是唯一原生研究员管理者，三个阶段边界不变。

`config/settings.example.json` 提供 `observer` 配置，默认关闭。启用 `observer.enabled` 后，`controlplane serve` 自动监督默认逻辑 observer，`controlplane host` 启动独立采集线程。Router 启动 manifest 自动增加 `router_observer` 工具。`observer.agent` 默认继承所选 `router_agent` 的 provider/model/effort/DSH 入口；这些字段保持独立。没有全局安装或配置修改。

上线应从验证后的新封存实现启动 API、Host、Router；既有研究任务保留原版本和原批次，采集仅只读它们的记录。当前开发改动没有覆盖 `versions/`、旧工程、已安装 profile 或凭据；当前 `config/settings.json` 尚未开启 observer。不要把开发目录替换为现有研究任务的绑定实现。

## Router 独占 K 与观察范围

`router_observer(action=context)` 返回最新 K、观察范围、待审汇报、发送回执和已记录的人类反馈。`action=k` 用 `expected_revision` 与完整 `people` 原子替换 K。K 在独立数据库中只有 `observer_k/current` 一条当前记录；没有历史 K 表或包含 K 内容的审计事件。revision 用于并发校验。研究笔记、游标、比较状态、未决线索、汇报投递状态另存，不堆入 K。

每个人具有以下六个数组：`shared_background`、`research_goals`、`expectations`、`judgment_basis`、`human_decisions`、`communication`。每项包含 `id`、`text`、`certainty: explicit|assumed`、非空 `basis` 引用。Router 根据人的指令、纠正与反馈维护它；observer 可在 `report.k_suggestions` 提建议，不能写 K。发送和理解不是同一状态。模型会话中已看到的上下文不作为 K 的权威版本，每次判断必须读当前 K。

报告通过 `k_refs: [{person, field, id}]` 引用涉及人员的当前 K 条目，可以只关联研究目标而没有胜负预期。兼容旧 `expectations: [id]`，但只接受报告涉及人员的预期；两种引用至少有一种非空。不存在的人员、字段、条目或过期 K revision 会拒绝提交，游标不前进。不能为了满足接口编造人的预期。

`action=scope` 的 JSON 字段：

- 默认 `oid=default`；`add`/`remove` 是 Router 的 research job ID，同一 job 固定一个批次。
- `all_research=true` 默认覆盖 Router 登记的所有研究任务；移除项保留在排除列表，重新 add 可恢复。
- `comparisons` 每项包含 `id`、`jobs`、`expectation`、`metrics`、`conditions`，供跨批次 A/B 判断。
- 新建其他 `oid` 必须给出 `independent_reason`；默认不另起进程。批次结束只产生可观察的结束记录，不结束逻辑 observer。无新材料或到期线索时不请求模型。

`action=review` 按稳定 `report_id` 提交 `decision`：`send`、`silent` 或 `defer`，附 `reason` 与当前 `k_revision`。发送还需涉及证据的 `job_id`、Router 整理的 `text` 和 `people`。在 `feishu.identity_map` 将这些人名键映射到已配置的 `ou_...`；不允许模型编造 open_id。`defer` 可给 `review_after`。每份汇报附有引用的概览/固定来源，Router 可用 `action=evidence` 和 `report_id/evidence_id/offset/bytes` 展开该报告引用的内容。`action=feedback` 关联汇报、已接纳的人类 `trigger_id` 与 Router 的解释，之后 Router 再显式更新 K。

## 材料与深读

采集器只遍历研究目录、初始化 task/ideas，以及共享 environment 中的 Python 源码与 benchmark 元数据；不遍历 final、visualization、凭据或任意工具参数中的路径。DSH 会话只收录研究工具的输入/结果/错误、研究员明确表达的分析文本和机械生命周期进展，排除系统提示词、请求头、用量及未知工具。未 share 的分析也进入观察。不会请 Worker 总结、等空闲或调用新工具。

记录包含稳定 ID、job/batch/session、原始 seq、时间、对象、内容 SHA256、来源位置和固定版本。第一流只给短摘录、截断标志及展开入口，不按 K 相关性、share 或分数筛除。原始 DSH 记录仍在原日志中，观察快照保存在独立数据库；源码不会被执行。tool call ID 保留，可读配对输入、结果及错误。分析标记为作者未验证解释；评测也必须进一步核查合法性、数据用途、覆盖、实现与后端，不能只凭作者措辞认定为可信测量。

`observer_read` 接收 JSON query，读取上限默认 32 KiB、50 项，每次返回明确 `cutoff`：

| kind | 查询 |
|---|---|
| context | 最新 K、scope revision、比较关系、范围及最近已提交报告的有界摘要 |
| status | 观察范围内 Worker 的已知状态及封存实现身份 |
| index / events | `after` 增量游标、`limit`、可选 `job_id` |
| session | `session`、`position`、`before`、`after_window`、`after` 分页 |
| tool | `call_id`、可选 `session`、`after` 分页 |
| material | 固定 `id`、`offset`、`bytes`，用于材料/源码/开发评测详情 |

`next` 是证据序号；`next_offset` 是 Unicode 字符位置；输出同时限制 UTF-8 字节数。大记录/文件分块保存，每块标明 chunk、总数、来源截止位置及哈希。跨 UTF-8 边界时保留可逆 base64 原文和明确标记的预览。变化中的多块文件不被声称为同一时刻的完整文件快照。后到材料不能越过当前判断的固定读取截止位置。

## 调度、提交与恢复

首次待处理材料开启固定 30 秒窗口，后续到达不延长它；输入达到 80 项/48 KB 或最老材料等到 120 秒也满足入队条件。忙时材料持续入持久队列，已有判断只返回同一个 batch ID，不并发执行第二个判断。跨 job 轮转，每个 job 每次最多 20 项；头部扫描只加载有界数量的概览，其他范围只读队列头部元数据。K/范围变化、相关新证据、范围结束或线索到期可触发重审。计时只触发阅读，不强迫汇报；模型服务不可用时排队时间可超过上述窗口。

判断可 `ignore`、`merge`、`wait` 或 `report`。等待必须有命题、缺失证据、重查条件/未来时间、分开的支持与反对引用。同一命题合并，不把重复日志或不同事件的惊奇度加成概率。每批最多合并一份汇报；报告包含事件判断、当前 K 引用、影响的人与决定、可信程度、限制、固定证据引用、建议时机。低价值材料提交游标但不产生报告。

当人的目标是“足够新且有效、值得扩大研究的方法苗头”时，作者自称创新、换名、涨分或泛泛建议验证都不足以自动汇报。判断应交代相对已记录方法的新增内容、可比较的有效性证据、扩展可能、反证及下一项有区分力的实验。人的熟悉程度和全球新颖性可以未知；无统一涨分门槛，可行范围扩大也可能有价值。排除尚未上报的乐观说法通常只记内部笔记；负面通知须关联真实 K 预期、明确决策或已有建议的更正，不能把研究员的说法当成人的先验。

建议不能超出已验证的目标与输入范围。例如固定开发集达到分数上界，不代表资源效率、规模或鲁棒性已无研究价值；建议换更难输入应明确以继续寻找质量提升为条件。沙箱端到端墙钟可能包含启动开销，不能单独据此排除算法资源改善。共享输入的多批运行也不能算作独立数据复现。

`context.recent_reports` 保留最近四份已提交汇报的 ID、状态、时间、前 600 字判断与前六个引用，并标明截断。它跨会话提供已报命题线索，独立于模型是否正确写好笔记；更早命题仍依靠当前笔记。已接收但尚未外发的报告也算已提交。范围结束、轮换或重读同一证据本身不构成新报告，应有新的支持结论、更正或由 K 解释的决策时机变化。数据库按 observer 和时间建索引，限制 SQL 投影和返回数量，不在 context 中读取全部历史报告。

一次 SQLite 事务提交当前笔记、比较状态、线索、处理游标和报告 outbox。提交失败全部回滚；同 batch 的相同提交返回原回执。Router 在自己的数据库幂等接收报告，再确认 observer outbox，断开重试不会产生第二份接收。Router 的接收、审阅、飞书发送与人类反馈是独立记录。发送前先记 sending 意图，响应丢失保留 unknown，先查询回执；历史接口不能证明已送达时保持 unknown，不把空历史当作未发送，也不自动重发。已有非 observer 通知保留原投递协议。

逻辑 observer 持续存在，原生会话可替换。默认每 12 批或约 240 KB 输入/判断后轮换；模型可在完成本批后请求 rotate。运行端还计入深读返回的上下文字节，接近阈值时要求先提交当前线索并轮换。这是上下文容量保护，不是 token 配额核算，也不声称可精确预测任意模型的上下文上限。新会话 ready 后才退出旧会话，旧会话 token 失效；交接期间继续排队。进程退出由 supervisor 核对 OS PID/birth 后重建会话并接管原未提交 batch。启动意图后缺少 PID 回执的未知进程状态保留现场，不盲目再启。provider 故障持久化，成功模型响应并提交判断后才标记 verified_recovered。

## 热路径与实际成本

没有在研究 Worker 热路径增加事件监听、网络发送、模型判断或 fsync。采集在线程/子进程中运行，直接只读 DSH v4 分帧文件，独立校验完整 zstd 帧后才推进压缩字节游标；不调用可能解压/复制整个日志的 persistence read，也不取得写所有权或触发 flush。解析、快照复制及观察数据库事务仍有 CPU、内存和磁盘成本，不能据此宣称零开销。

单 Host 采集串行：每次最多四个 job，每 job 最多两次会话读取（每次压缩输入 1 MiB、解压输出 2 MiB），文件遍历最多 12 个目录项，每文件最多两个块。不变日志只 stat，文件只在 stat 变化后读取。observer 深读并发为一，返回默认最多 32 KiB 的 JSON 转义文本及最多 9 KiB 概览元数据；按行消费 SQLite 查询，避免先复制整页详情。模型在事务之外运行。会话路径发现和任务登记仍有元数据扫描成本。采集 checkpoint 的 fsync 位于 Host 自己的 observer 文件中，不涉及研究元数据锁。采集 metrics 记录读取/解析字节、目录项和快照数量。

已用固定记录验证持有研究元数据文件锁时采集仍可完成，持有 Router SQLite 写事务时 observer 判断仍可提交；两者没有依赖该写锁。对一份现有原生 DSH 日志的只读探针读取 151714 压缩字节、解析 401233 字节/239 个事件，再次从游标读取 0 字节；本次约 5 ms，仅是本机单样本，不代表真实研究负载下的延迟或吞吐。

## 验证与尚未完成项

```sh
npm run check
TMPDIR=/private/tmp python3 -B -m unittest discover -s tests/controlplane -v
TMPDIR=/private/tmp python3 -B tests/native-observer-smoke.py
```

`test_observer.py` 是模拟记录/确定性判断，覆盖 A/B 预期被挑战、未分享新思路深读、不同密度合批、低价值静默、K 重审、轮换、事务内故障回滚、接收后确认前崩溃及回执去重。`test_observer_collector.py` 使用真实 Node 子进程读取固定记录，检查上传中断、完整帧游标、final 排除及锁边界；它不是实际研究。`observer-frames.test.mjs` 检查压缩分帧、读取预算及损坏拒绝。`native-observer-smoke.py` 启动两个真实 DSH 进程，创建四个原生会话，执行只读/提交与 ready 后交接，核对旧会话 fencing；无模型请求、无沙箱评测、无飞书发送。

真实部署证据见 DEPLOYMENT-ACCEPTANCE.md。持续研究质量、跨机器故障和 Windows 实机不能由上述固定测试推断。
