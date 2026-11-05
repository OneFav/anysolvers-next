# 路径约定

部署配置优先保存相对路径，在操作系统和 DSH 接口前解析为绝对路径。不要依赖 worker 当前工作目录；不要把旧运行回执中的绝对路径批量替换成新地址。

| 字段 | 相对基准 |
|---|---|
| `settings.json` 的 `path_base` | 配置文件所在目录，默认 `..`，适用于 `config/settings.json` |
| 控制平面的数据库、日志、Host 工作目录、封存版本、Python、CLI 脚本 | 上述 `path_base` |
| 插件 `pathBase` | 插件安装目录；默认安装目录本身 |
| 插件 `root`、`stateRoot`、Python | 插件 `pathBase` |
| `workspaceRoot` / `ANYSOLVERS_DSH_WORKSPACES` | 当前研究项目根目录；不允许 `..`；省略则使用项目默认位置 |
| 研究配置的 task、模块、材料与产物引用 | 既有项目或模块契约指定的根目录 |
| SSH `remote.root`、带目录的 `remote.python` | SSH 用户的 HOME；兼容 `~/` 和旧绝对路径 |
| 安装命令的 `--profile`、`--artifacts` | 调用命令时的工作目录，立即固定解析 |

Python / Node 等不带目录的命令从 PATH 解析。虚拟环境 Python 保留 `.venv/bin/python` 的调用路径，不解引用为系统 Python；否则会丢失虚拟环境依赖。凭据目录可以用 `~/.dsh` 引用已有位置，路径改造不复制或读取凭据。

本地配置示例（合并到完整配置中）：

```json
{
  "path_base": "..",
  "state_db": ".state/router.sqlite3",
  "worker_host": {
    "python": ".venv/bin/python",
    "state_root": ".state/host",
    "work_root": "projects",
    "versions": {"validated-version": "versions/CONTENT_DIGEST"}
  }
}
```

新批次 SSH 配置示例：

```yaml
remote:
  host: compute-node
  root: anysolvers-next-exec/versions/CONTENT_DIGEST
  python: miniconda3/bin/python3
```

SSH 显式切到该封存版本，核验入口哈希；Python 路径先以 HOME 锚定。远端默认登录目录改变不会影响它。源码、数据、预算和原请求 ID 仍须匹配，路径相对化不授予未知评测重放权限。新本地账本的输入/scope 使用项目相对路径，远端 ID 使用固定批次身份；不能对旧账本套用新算法。

临时文件遵循系统 `TMPDIR` / 临时目录选择，不要求 macOS 特有的 `/private/tmp`。DSH 原生策略、进程 cwd、系统设备文件、沙箱内 `/work` 等操作系统边界仍使用规范绝对路径。这些不是需要用户填写的部署目录。

移动项目之前，应停止并核对现有运行。旧 DSH 会话和项目登记保留原位置身份；搬迁后须重新选择工作区，不自动把旧进程/未知回执绑定到新位置。旧批次使用原封存版本，新行为只进入新版本及新批次。在跑的批次不会因为配置示例变化而被迁移。

验证包含带空格目录搬迁、变更 cwd、虚拟环境解释器、SSH HOME 解析，以及搬迁后已完成/未知评测的去重。它不代表已验证运行中的 DSH 会话在线迁移，也不代表所有操作系统均已实测。
