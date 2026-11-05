# 实现说明

参考官方 [dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype/tree/6337fc823ce20251c8de8085948165c81aaa701e)，本次阅读版本为 `6337fc823ce20251c8de8085948165c81aaa701e`。借鉴其 Source/Trigger/Thread、Mandate、Ticket/Assignment、Subscription 与持久 Effects 思路，保留 AnySolvers 的八类平级模块和确定性评测内核。

## 代码与职责

| 位置 | 职责 |
|---|---|
| `controlplane/store.py` | SQLite 事务、唯一消息/决定、事件日志 |
| `controlplane/engine.py` | 来源授权、派工、attempt 代际隔离、订阅、告警、outbox |
| `controlplane/feishu.py` | CLI 长连接、群与话题历史补拉、可靠回复 |
| `controlplane/server.py` | 操作员、执行节点与 Worker 的分离认证接口 |
| `controlplane/host.py` | 跨平台进程包装、启动回执、恢复核对、原会话续接 |
| `src/plugins/next-runner.ts` | DSH Router 与阶段 Worker、受限工具、实际模型错误等待 |
| `src/next/durable.ts` | 工具意图/结果本地 fsync 与中心补账 |
| `src/core/session-controller.ts` | 原研究员 ID/代际恢复、研究计划续接，不重启已完成代际 |
| `runtime/dsh/stage.py` | 可信阶段完成校验、基础设施等待时钟 |
| `controlplane/versions.py` | 内容摘要、不可变版本快照 |
| `controlplane/engineering.py` | 拒绝旧版框架修改入口 |
| `controlplane/web/` | 直接读取状态的监控页；优先显示异常和等待人工处理的状态 |
| `controlplane/observer.py` | Router 当前 K、跨批次范围、合批、固定证据读取、原子判断和报告回执 |
| `controlplane/observer_collector.py` | Host 独立只读采集，压缩帧游标、研究材料及源码快照 |
| `controlplane/observer_runtime.py` | 独立 observer 原生进程监督与崩溃后接管 |
| `src/plugins/observer-runner.ts` | 只读模型工具、当前判断提交、上下文轮换，不管理研究员 |

`Router` 只看到已接纳消息、可访问的现有任务、线程上下文与已登记版本；路由模型通过 `router_decide` 提交决定。来源成员白名单和阶段授权由程序校验，不能被聊天中的指令扩大。订阅能接收未获派工权限的人的上下文，但不授予执行权限。模型不必要求人类记住 task ID，内部匹配仍使用明确身份。

三个阶段均是根原生 Session 的独立进程。它们共享封存产物，不共享一段无限增长的主聊天。研究阶段再由现有研究控制器管理原生研究员。初始化工具无法发起研究，研究工具无法读取最终反馈，终局工具无法重新开始研究。

对人的事件报告由持久 outbox 驱动；Router 模型暂不可用时，API/飞书进程仍可发送已确定的进程/节点故障报告。通知标明是“失联”“执行状态未知”还是“开发结果不佳”，避免把失联直接当成已崩溃。

## 当前边界

- 默认只在新版本的 `projects/` 创建项目；没有自动导入、修改或迁移原有进行中项目的入口。若以后要接入旧项目的监控，应补只读观察适配器，再单独验收；本次没有把这项能力伪装为已完成。
- Windows 的控制面/进程标识实现已提供；这次运行环境是 macOS，Windows 实机验收仍待部署。
- 真实语义路由准确率、飞书权限/消息往返、真实模型/评测容器中断后续接，需指定机器人、测试群、模型路由和执行节点后完成。
- 工具与来源权限是协议边界；研究员文件容器、评测容器提供各自隔离。不能把整个 DSH 进程宣称为操作系统级多租户沙箱。
- 未知副作用不会自动重放。保留原 ID、先补账/核验再继续，比声称任意宕机都能无条件恢复更符合实际协议。
