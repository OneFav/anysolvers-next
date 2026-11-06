# Agent 安装协议

用户可以把本文件交给安装 agent：它负责询问部署位置、模型、飞书应用及群，执行命令并解释结构化检查结果。无需独立安装 GUI。安装 agent 是用户授权的维护者；研究 Router/Worker 不得调用部署工具或修改框架。

## 1. 拉取固定版本

以下示例安装已记录部署验收的 `v0.8.0`。main 当前是 `0.9.0-dev` 开发快照；仅在明确测试该快照时，将 checkout 参数换成已核验的完整 commit SHA，并使用新的独立实例。不要把开发快照的源码测试记录当作 `v0.8.0` 部署证据，也不要覆盖既有封存版本。

要求 Git、Node >=22.19（建议 Node 24）、Python >=3.10（建议 3.12）；系统依赖由用户或其设备管理器提供。所有框架、DSH 和飞书依赖安装在所选目录内，不做全局安装。Windows 使用 Python 官方发行版和 Node 官方发行版，研究执行优先采用 Linux/macOS/WSL2。

```sh
git clone https://github.com/OneFav/anysolvers-next.git anysolvers-next
cd anysolvers-next
git checkout v0.8.0
python3 scripts/deploy.py init --instance ../anysolvers-instance --port 8787 --provider deepseek-account --dsh-credentials-home ~/.dsh
python3 scripts/deploy.py install --instance ../anysolvers-instance
```

Windows 将 `python3` 换为 `python`，并在 `init` 时添加 `--mode control`；原生 Windows 拒绝研究 Host/单机模式，研究使用 WSL2 或其他执行机。`deepseek-account` 需先在目标机器 DSH 登录；API provider 使用对应进程环境变量和原生 DSH 配置。不要复制、读取或输出认证文件。已有 DSH profile 不会被导入或修改。

程序源目录与实例目录必须分开。实例目录保存 `config/settings.json`、`.state`、`.venv`、`versions`、`projects`；可用 `--projects` 选择独立研究目录。相对 CLI 参数按调用位置解析，配置中的相对路径按配置目录的 `path_base` 解析。带空格/中文的参数应加引号。

重复 `init` 会拒绝覆盖现有实例；`install` 可恢复中断的依赖准备。安装不会启动研究。检查失败时修复对应条件，不填造验证证据。

## 2. 连接飞书

以下命令均追加 `--config ../anysolvers-instance/config/settings.json`。`prepare` 已由安装流程执行。

- 新建应用：`python3 scripts/feishu-setup.py new-app`，通过官方 CLI 完成认证、机器人能力、发布、权限与长连接事件订阅。
- 已有应用：`python3 scripts/feishu-setup.py existing-app`，由用户在终端密码框输入 App Secret。agent 不接收密钥。
- 同机明确复用现有实例的认证：`python3 scripts/feishu-setup.py check --reuse-auth-config /path/to/existing/config/settings.json`。只引用认证目录，不复制或读取凭据。旧实例不能与新实例同时处理相同群。

执行 `check` 核对公开身份，`chats` 列出机器人所在群，`members --chat-id oc_...` 获取指定群的成员。群与成员只能由用户选择或沿用已授权的配置，不能自行扩张接入范围。

可创建新的指定测试群（只邀请用户指定成员）：

```sh
python3 scripts/feishu-setup.py create-chat --config ../anysolvers-instance/config/settings.json --name 'AnySolvers 验收' --request-id install-test-001 --user-id ou_YOUR_OPEN_ID
python3 scripts/feishu-setup.py configure --config ../anysolvers-instance/config/settings.json --chat-id oc_CREATED_CHAT --user-id ou_YOUR_OPEN_ID --label 'AnySolvers 验收' --test-group
python3 scripts/feishu-setup.py check --config ../anysolvers-instance/config/settings.json
python3 scripts/feishu-setup.py enable --config ../anysolvers-instance/config/settings.json
```

创建群需要 `im:chat:create` 或官方接口列出的对应群管理权限。API 幂等窗口有限；脚本持久化建群意图和回执，未知结果超过窗口时必须人工核对，不能换 request-id 盲目重试。

默认使用 `im.message.receive_v1` 长连接，无需公网 webhook。同一应用跨机器只指定一个接收端；本机新版本提供进程锁，但不能锁住其他软件或其他机器。若原应用必须继续接收其他群，使用 `enable --receive-mode history`，只轮询此实例独占的新群，通常增加约 10 秒延迟；需要群历史权限。不可让两个实例同时管理同一群。仅 Webhook 自定义机器人不足以交互派工。

向导默认不开启派工。指定测试群、允许用户和权限检查通过后才运行 `enable`。`disable`、`enable` 和群配置变更在服务重启后生效。

## 3. 验证、封存和启动

```sh
python3 scripts/deploy.py seal --instance ../anysolvers-instance
python3 scripts/deploy.py check --instance ../anysolvers-instance
python3 scripts/deploy.py start --instance ../anysolvers-instance
python3 scripts/deploy.py status --instance ../anysolvers-instance
python3 scripts/deploy.py stop --instance ../anysolvers-instance
```

`seal` 运行构建后测试并将源码摘要绑定到证据，安装封存版本自己的 Node 依赖。已有版本和运行不随源码升级。`start` 管理独立后台进程，关闭 DSH 不会停止服务，不自动注册开机任务。`status` 只证明进程状态；飞书及模型健康在控制台或插件显示。

服务令牌保存在实例 `.state/services/tokens.json`，仅供本地服务使用；不要打印或发送到聊天。跨机器执行节点只提供自己的节点令牌，不提供 operator token。模型实际调用、真实飞书和实际评测必须另外验收，不能把 `seal` 的固定测试写成真实研究。

在指定测试群中由真人发测试消息后，可运行 `send-test --chat-id ... --reply-to ...` 验证回复和 @。机器人自己发的消息不能冒充真人派工。实际模型探针：`python3 scripts/check-dsh-model.py --config ... --inference`；不启动研究。

## 4. DSH 插件入口

桌面用户可通过 DSH 的 GitHub 插件输入安装固定版本：

```text
github:OneFav/anysolvers-next#plugin-v0.8.0
```

等价命令（使用所选 DSH 安装的 CLI，不要求全局安装）：

```sh
DSH_HOME=/path/to/NEW/dsh-home dsh plugin --profile web add github:OneFav/anysolvers-next#plugin-v0.8.0
```

插件发布标签包含预构建的完整 UI bundle，无安装期构建钩子。后台仍按上述 agent 协议安装，插件与 CLI 共用同一实例。给插件 `anysolvers-im-ui` 配置 `instance: /absolute/path/to/anysolvers-instance`，或启动 DSH 前设置 `ANYSOLVERS_INSTANCE`。插件显示绑定群、健康、研究进度并允许 owner 启停服务；首次安装/升级和认证继续交给安装 agent。

当前固定 DSH 版本的 CLI 只接受 profile 名称；desktop 名称由 Electron 管理。CLI 验收使用独立 DSH_HOME 下的 web profile；桌面内使用插件页面的 GitHub 链接输入。不得修改源项目使用的已安装 profile。

## 5. 跨机器部署

接入机 `init --mode control`，执行机 `init --mode host --router-url https://your-router.example`。两机分别安装依赖；执行机配置与接入机登记相同的封存版本 ID 和内容摘要。通过 HTTPS 反向代理将 API 暴露给执行机，配置 `listen` 与 `behind_tls_proxy=true`；或使用 SSH 隧道连接 loopback 地址。不要直接暴露未加密端口。

执行机首次启动前由操作员在进程环境设置与接入机匹配的 `AS_NEXT_DEVELOPMENT_TOKEN`。不匹配或缺少令牌时不能领取任务。新机器不得自动导入旧研究。

SSH 计算继续使用现有研究配置的 `remote.host/root/python`，通过计账评测器执行，无需 Docker。远端没有文件/网络沙箱；CPU、墙钟与实际内存限制按后端回执记录。native DSH 本地沙箱不保护远端进程。

## 6. 升级、回退与迁移

把新版本拉到新的程序目录。先检查活动任务并让 Router 正常结清，再停接入服务。对同一实例从新目录运行 `install`、`seal`，验证后启动；旧封存版本继续保留。失败时使用原目录、原配置备份恢复服务，不修改旧评测账本。`seal` 禁止在活动任务或服务运行时更换实例默认版本。

移动实例目录需要重新准备 venv、DSH/CLI 二进制与部署路径。现有 DSH 会话不支持在线路径迁移；旧运行身份、未知评测和回执必须核对。未知评测阻止自动重放。
