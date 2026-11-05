---
version: 3.5.0
execution:
  mode: main
  delegates: {}
defaults:
  trace_retention: summary
parameters: schemas/params.json
capabilities:
  short_state_transactions: true
  trusted_feedback: true
id: queued-sandbox
kind: evaluation
accepts:
- run_config
- task
- dataset
- candidate
- selection
produces:
- evaluation
contracts:
  evaluation: schemas/evaluation.json
handlers:
  evaluate: measure.py
  details: measure.py
  preflight: measure.py
  evaluation_active: measure.py
  resource_contract: measure.py
  validate_compute: measure.py
files:
- execution_adapter.py
- sandbox.py
- native.py
- remote.py
- volume.py
- process.py
- retention.py
- receipts.py
---

# 可信三槽评测

`compute.executor=ssh-process` / `image=ssh-process:v1` 是用户明确授权的开发评测后端，需要封存 `process_authorization=explicit-user-development-only`。本机 DSH 保持框架、研究员工作区、计账和 observer；`solver_evaluate` 默认把可信 episode 仿真及候选送到固定 SSH 主机。无需远端 DSH、Docker、Landlock 或 namespace。远端进程没有文件或网络沙箱，结果明确 `isolation_verified=false`，不能将本机 DSH 的隔离范围称为远端隔离。CPU 使用每进程 RLIMIT_CPU 和单 CPU affinity；内存是每进程 RLIMIT_AS 地址空间上限，非 RSS 或容器内存。不保证 PID 数量或临时目录总大小，普通文件输出 RLIMIT_FSIZE=0。清理并核对进程组；逃离进程组的恶意源码不在该资源管理边界内。每份候选仍须按任务审阅源码，不能宣称已隔离私有环境状态。仅允许开发 anchor/challenge，最终数据在传输前拒绝。SSH 回执和本地账本继续去重，未知执行不重放；不提供任意远端 shell 绕过评测入口。

`preflight` 接收完整 compute 合约（兼容旧本地 image 字符串），在远端检查选定后端，不能误检查本机 Docker。

新配置的 `remote.root` 与带目录的 `remote.python` 可以相对 SSH 用户 HOME，接受 `~/`，也兼容旧绝对路径；单独的 `python3` 从远端 PATH 解析。执行前显式切到封存版本目录，核验入口内容，不依赖 SSH 登录目录。新本地执行账本以项目相对路径登记输入和 scope；远端请求 ID 使用固定批次初始化/创建身份，不含项目绝对目录。目录迁移不生成新评测身份，未知回执继续阻止重放。旧封存批次保持原协议。临时目录遵循系统 TMPDIR，不再要求 `/private/tmp`。详见 `docs/PATHS.md`。

SSH Docker 的代码通过 Docker archive API 写入专属命名卷；暂存容器始终不启动，复制回读逐字节核验后删除。真正候选容器仅以只读卷挂载这两个代码文件，启动前仍完整检查非 root、断网、只读根盘、CPU/内存/PID/ulimit 等边界。清理涵盖候选容器、暂存容器与卷，名称由已持久化的容器意图派生；未知清理仍阻塞执行。此方式不依赖 SSH 宿主路径能被 Docker daemon bind mount。旧 local-docker 继续原 bind 文件协议。

`compute.executor=ssh-docker` 将完整 episode 的可信仿真和候选 Docker 放在指定 SSH 主机，模型及本地计账仍通过现有接口。`compute.remote` 固定 host/root/python，镜像须是已审阅的远端内容 ID。传送的环境只含已登记评分源码/依赖与本次案例，不上传整套最终集或凭据。单案远端先持久化 request 与容器意图，再执行；返回固定 request SHA、远端账本和结果。SSH 断开或缺少结果属于未知执行，必须核对原账本，不更换 ID 重跑。远端三槽及非 root、只读根盘、断网、单核、硬内存限制继续有效；新后端只支持 episode，必须创建新批次，重新测量基线。远端的 Python/镜像和节点信息应随验证记录保存。

研究员直接通过 tools/research.py evaluate 调用；主会话仅在准备基线、异常处理和最终阶段用 tools/measure.py 管理入口。程序负责评分，不允许研究员写正式分数。

输入为已封存候选、开发集、会话、请求ID与固定预算。提交时原子预约预算；实际开始时再次检查会话/源码/数据/资源。默认最多三个数据集评测并行，每个槽内逐例运行，每个候选容器仍为单核。槽位等待不占状态锁，admission_queue_wait_s 与沙箱 queue_wait_s 单列；启动等待、评测前置检查、排队和执行均不消耗研究员的有效研究时间。测量运行中其他 actor 可登记笔记、发现和新候选。

`compute.evaluation_wall_s` 是取得槽位后整次数据集评测的时间预算；排队不计入。每个新案例只有在剩余额度能容纳完整单案 wall_s 与准备/清理预留时才启动，不缩短最后一例的原计算限额；不足则保留已完成结果并明确报告未运行案例。单案 wall_s/cpu_s/内存等由初始化按任务推荐，向用户说明后写入配置。`resource_contract` 返回执行器的开销预留，供初始化计算整次预算；该预留不是给算法增加的计算时间。96 小时整批窗口在每次新案例开始前检查，到期让已运行案例按原限额结束、停止后续案例，不强杀原生研究员。

compute.executor=dsh-native、image=dsh-native:0.2.1-rc.5 使用 DSH 原生 macOS 执行器，仅支持 function 协议。DSH read-only 加追加策略限制读取范围、禁止联网与 fork；Python 子进程执行候选，宿主评分器不 import 候选。CPU 使用 RLIMIT_CPU，墙钟和输出有界；RSS 采样监测内存（可能短暂超限，并非硬内存隔离），没有 CPU 核心硬绑定，禁止临时写入。pids/tmp_mb 在此模式下是兼容预算字段，实际规则是单进程和禁止写入。

旧 compute.executor=local-docker 及固定镜像继续使用 Docker 非root、只读根盘、断网、单核和硬内存/进程限制。切换执行器必须建新批次；不能重用原环境成绩。结果含所有 attempt/result，未知耗时不作0。全体合法、清理与基础设施正常才有总体均值。

开发结果在 research/evaluations，最终在 final/evaluations。默认返回摘要，研究员 result --details/--case 按需读取；接口只开放自身与公开开发证据。最终仍需固定选择与用户授权，研究员直接接口一律拒绝。

同请求不重复求解，同一快照请求互斥。三槽由本项目共享锁限制，正在运行的评测持有独立租约，不能把其他槽的真实在途记录误判为孤儿。中断记录、缺失结果、未知清理或基础设施错误保留并阻止后续测量；主会话先核验现场，不改ID绕过。锁由工作进程持有，不启动后台服务。evaluation_calls 默认为 null，即不限制会话或批次的评测次数；设置有限值时为全批共享，排队请求也占预约容量。probe_calls 仍是单独的探测授权，默认 0；它不限制普通开发集评测。

## 输入的可执行评测包

评分器、基线、实例与仿真规则均由 datasets 初始化并登记；本模块不创建数据、仿真器或评分目标。运行时通过所选 datasets 的 load_environment 接口加载已封存的 benchmark，并核对文件。batch challenge 的 @anchor 引用复用共享环境，主会话无需再复制评分器。

- function 协议：每个实例在新容器调用一次 `solve(case)`，由可信评分器核验返回解。
- episode 协议：每个实例新建一个持久策略容器，多次调用 `solve(observation)` 返回动作；同一 episode 内可保留进程内记忆。可信环境在宿主执行 reset/step/score_episode，只有当前 observation 发送给候选。候选没有环境代码、完整私有实例或未来事件文件挂载。环境自身必须正确过滤观测，接口不能替错误的环境定义保证未来隔离。

交互协议受 max_steps、每动作 action_wall_s、整例 wall_s 以及原单核/内存/CPU/输出上限约束；响应超时或非法动作无分数，未知清理或基础设施错误阻止继续测量。静态评分及交互可信 Python 回调使用主线程 POSIX 定时器控制，计入单案时限；三槽通过三个独立进程执行，不能用子线程替代。当前不支持不可中断的原生仿真引擎。环境运行消耗宿主资源，不包含在候选容器的内存/单核限制内，重型环境须另行配置执行器，不能将该限制宣称为全环境资源上限。

新批次默认 `modules.evaluation.params.trace_retention: summary`：可信评分器仍在内存中获得完整 trace，评分完成后才裁剪持久化结果。在远端回执及 SSH 回传前移除逐步 observation/action/next_observation，保留逐例分数、全部可信 metrics、步数、资源与清理回执、输入/源码/环境固定引用，以及未保存 trace 的规范 JSON 哈希、字节数和记录数。失败最多保存末尾 3 条、合计 4KiB 的轨迹窗口；stdout/stderr 各保留最多 4KiB 的首尾摘录及截断标记、原长度与哈希。默认没有完整逐步轨迹展开入口，不能把摘要说成完整日志，也不能为补日志自动重跑评测。

确需完整轨迹时，在封存新批次前显式设置 `trace_retention: full`；该选项进入原请求哈希，不能用同一请求 ID 改策略重放。存储策略不改变评分、候选执行限制、预算或开发/最终数据边界。旧批次继续原封存实现和原成绩；历史数据只能通过独立维护工具校验、无损归档，不能原地重写封存成绩。候选 manifest 中 solve(case) 的 case 在 episode 协议下表示本步 observation。

独立维护可将已完成的远端 `result.json` 无损压缩为 `result.json.gz`，并保留原始字节哈希、长度、请求身份及压缩文件哈希的 `result.archive.json`。新读取器逐项校验后返回原回执；意图、进程记录、租约和未知结果不删除。不支持该归档格式的旧执行器保持暂停，不能把缺少明文结果当作可重跑。归档工具必须先验证完整异地备份，再核对无活动租约；失败/未决请求不得清理。
