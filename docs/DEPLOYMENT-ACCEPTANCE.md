# Portable deployment acceptance — 0.8.0

验收日期：2026-11-05。源码通过公开 GitHub 仓库拉取到独立的中文、空格路径，使用独立实例、私有 Python/DSH/CLI 依赖、数据库、日志和封存版本。原有研究、profile 和认证文件未被迁移或修改；机器人通过官方 CLI 引用现有认证。

| 层级 | 实际执行与结果 |
|---|---|
| 干净部署 | GitHub clone → init → install → seal → check → start；检查通过，API/Router/Host 独立运行 |
| 构建与固定回归 | Node 主套件 92 通过、7 个显式 opt-in 跳过；控制平面包含安装目录、重复群租约和真实进程检查；完整输出保存在实例外的本地验收日志 |
| CI 平台 | Linux/macOS 完整 Node 回归；Windows 只验证接入端相关构建、测试和安装协议；持续状态见 GitHub Actions |
| 原生 DSH 会话 | 4 个真实 DSH 创建/恢复进程，执行阶段工具边界检查；0 模型请求、0 研究 |
| Host 恢复 | 2 个真实 wrapper + 2 个 DSH 进程，第二代恢复相同 native session；0 模型请求 |
| 原生沙箱 | 6 项固定夹具验证：成功、文件/网络/fork 限制、超时、输出上限、故障关闭、工作区文件；实际子进程执行，不是科学研究结果 |
| 模型探针 | 1 次真实 deepseek-account/deepseek-flash 请求，无工具，正常结束 |
| 飞书 | 使用当前机器人创建新的指定私有验收群，仅加入授权用户；身份、群历史、真实收信、Router 回复、@ 与回执已验证 |
| 停机恢复 | 停机期间在新群发送第二条测试消息，启动后通过历史补拉接收并回复；首条回执未重复，研究任务数为 0 |
| GitHub 插件 | 在独立 DSH_HOME 的 web profile 从 GitHub 安装预构建 bundle；已安装入口通过真实 Python bridge 读取运行实例与绑定群 |

实时飞书输入由获授权的安装 agent 通过用户的飞书客户端发送，消息中明确标识验收用途。不是伪造事件，也不声称是用户手工输入。群 ID、用户 ID、消息 ID、原始模型记录和凭据不进入公开仓库。

本轮没有运行新科学研究。原生沙箱固定夹具不能当作受计账的真实研究评测；真实模型探针不能证明研究质量。跨物理机器的 HTTPS/SSH 故障切换、Windows 实机完整安装以及跨平台原生研究执行没有在本轮实测。Windows 原生仅允许 control 模式，研究使用 Linux、macOS 或 WSL2。

GitHub CLI 安装使用命名的 web profile；固定 DSH 版本拒绝绝对路径 profile，并将 desktop 保留给 Electron。桌面用户使用插件页面的 GitHub 输入。没有修改用户既有桌面 profile。

公开发布前对全部源码文件生成哈希清单，扫描真实平台 ID、个人目录、凭据格式与私有目录，并通过 Gitleaks 全量扫描。真实历史聊天样本替换为标注清楚的合成回归样本；本机旧记录另行保留。
