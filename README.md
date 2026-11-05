# AnySolvers Next

独立的 DSH 研究框架：飞书消息进入持久化 Router，初始化、研究与终局分别在独立 Worker 中执行。研究者只由 research 方法创建；候选与基线只在计账评测器执行。框架维护由用户授权的安装/开发 agent 完成，研究进程没有自主修改框架的能力。

## 安装

把 [INSTALL.md](INSTALL.md) 交给安装 agent。支持任意程序目录、独立实例目录、单机或分布式接入/执行节点。本地安装 Node、Python、DSH 与飞书依赖，无全局安装。模型与飞书认证由原生工具管理。

- 源码：`git clone https://github.com/OneFav/anysolvers-next.git`，选择固定版本 `v0.8.0`。
- DSH UI 插件：`github:OneFav/anysolvers-next#plugin-v0.8.0`，连接独立后台实例。
- 飞书：复用企业自建应用或经官方 CLI 创建新应用，绑定已有群或新建指定测试群。

Node >=22.19，DSH 固定 `0.2.1-rc.5`，Python 推荐 3.12。平台支持和实测范围见 [部署验收](docs/DEPLOYMENT-ACCEPTANCE.md)。Windows 原生研究执行尚未验证；可用 Windows 接入端搭配 Linux/macOS/WSL2 执行端。

## 运行边界

完整保留 `runtime/modules/<category>/<method>` 包。初始化与实现版本冻结；最终数据和结果不回流研究。已有项目默认只读监控。模型服务不可用时持久化事件，验证恢复后继续；未知评测不自动重放。不做 token 配额计账。

框架和 DSH 工作区在执行节点运行。SSH 评测使用受资源限制的远端 Python 进程，无需 Docker，也没有远端文件/网络沙箱。原封存批次保留原后端和证据，不跨环境比较成绩。

## 开发验证

```sh
npm ci
npm run check
npm run test:runtime
python3 -B -m unittest discover -s tests/controlplane -v
```

真实 DSH 会话、实际模型调用、实际沙箱评测和飞书收发单独验收，不把固定测试当成真实研究。真实飞书测试必须使用指定测试群。

- [安装协议与升级](INSTALL.md)
- [路径约定](docs/PATHS.md)
- [恢复协议](docs/RECOVERY.md)
- [实现索引](docs/IMPLEMENTATION.md)
- [Observer](docs/OBSERVER.md)

仓库不包含认证、实例配置、研究数据或原有真实联调聊天记录。
