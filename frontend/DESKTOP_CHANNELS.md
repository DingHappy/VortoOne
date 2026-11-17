# 桌面版本管理

| 通道 | 应用 | 标识 | 当前用途 | 更新方式 |
| --- | --- | --- | --- | --- |
| 公开稳定版 | VortoOne.app | com.vorto.vortoone | 现有可用桌面 App | 原有正式版自动更新 |
| 开发版 | VortoOne Dev.app | com.vorto.vortoone.dev | 新桌面布局与史莱姆界面验收 | 独立安装包、GitHub 发布草稿 |

两个 App 可并排安装。开发版的 WebKit 数据按独立应用标识保存；安装包自带经锁文件与来源校验的 Python 后端，默认启动真实本机服务，不读取正式版账号、项目或运行状态，不配置自动更新。示例界面和偏好设置预览仍可从开发版菜单单独打开。

开发版菜单「开发版」可打开主界面、史莱姆外观、偏好设置预览、版本管理，以及系统浏览器中的 GitHub 发布管理。正式版下载入口指向现有公开稳定版。

## 独立安装与模型连接

从 `0.3.0-dev.5` 起，安装包包含前端、冻结的 Python 后端及其第三方声明。用户机器无需源码、Python、Node、Rust 或 `backend.json`。应用优先运行随包后端，升级时保留开发版自己的数据。

第一次打开会显示「连接模型」窗口，填写兼容 OpenAI 的服务地址、模型名和 API Key（本地服务可留空）。之后从「开发版 → 模型连接…」修改。API Key 不回传到页面，配置只保存在本机，Unix 上文件权限为 `0600`；保存会重新连接，保留聊天记录。旧开发版显式指定的模型配置继续读取，修改后写入开发版自己的 `model.env`，不改原环境文件。

成员在 `/api/ws` 上订阅有权使用的 agent 私聊，自动收到自己的消息与任务结果；当前台账约每秒核对一次。首次订阅/重连补发有界快照，前端按消息 ID 合并；不推送主会话或空间广播。每轮和每次推送前核对会话及权限，登出、重置密码、移除成员或撤销 agent 访问后关闭连接。HTTP 历史读取仍是内容来源。

## 源码开发时连接后端

未打包随包运行时的源码调试环境，仍可在配置目录（macOS：`~/Library/Application Support/com.vorto.vortoone.dev/`）放一个 `backend.json`：

```json
{ "python": "/绝对路径/python", "source": "/绝对路径/VortoOne 源码", "env_file": "/绝对路径/模型凭据.env" }
```

- `python` 要装好了源码的依赖；`source` 是仓库根目录；`env_file` 可省。
- `env_file` 中的 `OPENAI_*`、`VORTOONE_MODEL*`、`VORTOONE_LLM_*`，以及现有的 `DEFAULT_MODEL`、`LLM_MODEL_CHEAP`、`LLM_MODEL_BALANCED`、`LLM_MODEL_POWERFUL` 会带给后端，其余行忽略。
- 环境文件按 dotenv 语法读取，支持引号、`export` 和行尾注释；注释不会成为模型地址或凭据的一部分。
- App 在本机回环地址启动后端（私人助理范围，不开 IM、定时与巡视），主窗口打开它并自动登录。菜单「开发版 → 连接本机后端」可以重连。
- 主人令牌每次启动现生成，只在 App 进程和后端里；窗口拿到的是一次性的交接码，不是令牌。
- 数据在开发版自己的目录（`…/com.vorto.vortoone.dev/home`），不读正式版的账号、项目或运行状态；后端日志在同目录的 `backend.log`。
- 该源码路径仅是开发时的后备方式；安装包不依赖它。

## 本机管理

在 `frontend/` 运行：

```sh
npm run desktop-dev:status  # 查看两个 App 的安装版本
npm run desktop-dev:version -- 0.3.0-dev.6 # 统一递增开发版版本号（下一版时使用）
npm run desktop-dev:bundle # 从干净且已合并的 main 构建 Apple Silicon macOS 开发安装包
npm run desktop-dev:install # 校验并安装到 /Applications/VortoOne Dev.app
npm run desktop-dev:check-installed # 全新临时数据、无源码/Python 路径验证随包后端和登录，不调用模型
npm run desktop-dev:manage # 打开 GitHub Release 管理
npm run desktop-dev:draft  # 将已校验的开发包和校验和上传为发布草稿
```

首次构建需要 Node、Rust、Xcode 工具和已有的 Developer ID Application 签名身份。打包复用 `desktop/scripts/build-sidecar.mjs` 的固定 CPython 来源与依赖锁，逐个签名后端 Mach-O，再签整个 App，并执行全新数据目录的独立启动验收。脚本默认签名但不向 Apple 提交公证；对外公开分发前仍需公证与对应验收。

安装只会替换开发版的固定路径；已有开发版会先备份再替换，失败时恢复。先退出正在运行的开发版，再执行安装。正式版继续在原来的应用路径。

## 管理发布

开发版使用 `0.3.0-dev.N` 版本号，GitHub tag 为 `v0.3.0-dev.N`。用 `desktop-dev:version` 同步更新配置、Rust 包和锁文件中的版本；命令只接受递增的开发版本号。先走 PR 合并，再构建。

`desktop-dev:draft` 固定创建 Draft + Pre-release，附件包括独立 DMG、`desktop-dev.json` 和 SHA256 校验和。你在 GitHub Releases 中可编辑说明、删除草稿或发布为预发布，供测试者手动安装。开发版不提供正式版自动更新清单，公开稳定版仍由原有 `desktop/` 发版流程管理。

公开稳定版保留现有可用版本。独立运行和真实桌面验收在开发通道完成后，再核对正式标识、升级保数据、公证与更新清单；不通过修改 Release 标签来替代这些交付步骤。
