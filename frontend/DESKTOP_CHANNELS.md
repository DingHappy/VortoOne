# 桌面版本管理

截至 2026-11-17，当前已交付版本如下；源码 main 可以领先安装包，构建提交以 `desktop-dev.json` 或 App 的版本管理页为准。

| 通道 | 应用与版本 | 标识 | 当前用途 | 更新方式 |
| --- | --- | --- | --- | --- |
| 公开稳定版 | VortoOne.app · 0.2.64 | com.vorto.vortoone | 现有可用桌面 App | 原有正式版自动更新 |
| 开发版 | VortoOne Dev.app · 0.3.0-dev.7 | com.vorto.vortoone.dev | 新桌面布局、史莱姆与真实本机工作区 | 独立 DMG 手动更新、GitHub 发布草稿 |

两个 App 可并排安装。开发版的 WebKit 数据按独立应用标识保存；安装包自带经锁文件与来源校验的 Python 后端，默认启动真实本机服务，不读取正式版账号、项目或运行状态，不配置自动更新。示例界面和偏好设置预览仍可从开发版菜单单独打开。

开发版菜单「开发版」可打开主界面、史莱姆外观、偏好设置预览、版本管理，以及系统浏览器中的 GitHub 发布管理。正式版下载入口指向现有公开稳定版。

## 独立安装与模型连接

当前开发安装包包含前端、冻结的 Python 后端及其第三方声明。用户机器无需源码、Python、Node、Rust 或 `backend.json`。应用优先运行随包后端，升级时保留开发版自己的数据。

手动更新时，等待当前任务结束并退出 Dev，打开新版 `VortoOne_Dev_<版本>_aarch64.dmg`，把 VortoOne Dev 拖入「应用程序」并选择替换。该操作只替换 App；聊天和模型配置保存在独立应用数据目录中。Dev 暂无应用内自动更新，公开稳定版的自动更新不会安装 Dev 包。

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
- 主人令牌即「所有者密钥」：第一次设置这台电脑上的服务时生成，存在开发版数据目录的 `owner-key`（0600），之后每次启动沿用；只在 App 进程和后端里，窗口拿到的是一次性的交接码，不是令牌。
- 数据在开发版自己的目录（`…/com.vorto.vortoone.dev/home`），不读正式版的账号、项目或运行状态；后端日志在同目录的 `backend.log`。
- 该源码路径仅是开发时的后备方式；安装包不依赖它。

## 家的列表与首次引导（#898 第 10 节第 1 步）

- App 记着它打开过的服务（配置目录的 `servers.json`）和当前打开的那一个；只收 https，或本机回环上的 http。
- 打开时：上次选的是这台电脑上的服务，就启动它并自动登录；选的是别处的服务，主窗口直接打开它自己的登录页；什么都还没选，弹出首次引导，三选一（云端显示「即将提供」）。已经在用开发版的（数据目录里有 `home/`），按「已装在本机」照旧启动。
- 首次引导和「开发版 → 偏好设置…」用的是 agentes 桌面端的窗口界面（`frontend/apps/desktop`）连真外壳的那一份（`shell/index.html`），外壳的实现在 `src-tauri/src/{places,shell}.rs`。偏好设置里能启停、重启、备份（停着打包 `home/` 与所有者密钥，留最新两份）、恢复、清空重来。
- 这台电脑上 agent 干活会用到的环境（Chrome 一类浏览器、git、Claude Code、OpenCode、Tailscale）在「复制详细信息」里列出有没有、怎么装；正式的显示在第 3 步「工具」页。
- **搬家**（菜单「开发版」）：「搬家：导出并停用这里…」把这台电脑上的家导出成搬家包（`.tar.gz`），之后这里不再启动它，数据挪到 `moved-<时间>` 留着；「从搬家包导入…」导入到这台电脑上的家，已有数据时先问要不要替换（原来的挪到旁边）。常开的电脑上用 `vc home-export <包> [--move]` / `vc home-import <包> [--replace]`（先停服务）。
  - 带上：聊天、记忆、agent 与岗位、成员与身份组（密码只有哈希）、例行工作、任务台账、审计。
  - 不带：手机配对、IM 凭据、AI 应用的连接、agent 浏览器里的登录、运行锁、git 工作副本、日志；模型密钥和所有者密钥本来就不在家里。到新家后手机、AI 应用重新连，模型重新填。
  - 搬走的家里留 `.vortoone/moved.json`，那里的服务拒绝启动并说明已搬走。
- **连接链接** `vortoone://connect?server=<地址>[#token=<一次性登录>]`：点了链接或扫了码，App 打开首次引导窗口，用大字显示要连的地址，确认后才打开；带一次性登录的直接登录进去。一次性凭据只放在 `#` 后面，放在 `?` 里的链接不认。外壳只收 `vortoone://connect`，原样交给窗口，链接本身不能配对电脑、启动服务或安装东西。协议登记在 `src-tauri/Info.plist`，只在打包好的 App 里生效（`tauri dev` 收不到）；目前只有 macOS。
  - 一次性登录由已登录的人在网页「在手机上打开」里开（`POST /api/app/phone-sign-in`），5 分钟、用一次就作废，只换得到开它的那个人：所有者开的登录成所有者，成员开的登录成这个成员。服务重启，没用掉的全部作废。
- **经 Tailscale 让别的设备连进来**（首次引导里勾选，或偏好设置里打开）：外壳跑 `tailscale serve --bg --https=443 http://127.0.0.1:<端口>`，只在这个人的 tailnet 里、走 HTTPS；服务本身照旧只听 127.0.0.1。443 上已经在分享别的东西就用 8443。tailnet 里还没开 HTTPS 时，Tailscale 给一个管理页地址，窗口显示出来等人去打开（最多 10 分钟，可以不等）。打开后对外地址写进数据目录的 `public-url`，服务带着 `VORTOONE_PUBLIC_URL` 重启一次，链接、二维码和 AI 应用都报这个地址；关掉时只撤我们开的那个端口。端口换了会自动改分享。Tailscale 没连上时偏好设置里提示「别人这会儿连不进来」。
  - 后端经代理拿到的来源和协议照 uvicorn 默认只信 127.0.0.1 上的代理；经 tailnet 进来的请求按它的真实地址算，不算本机。
- **发现**：连接页的「找到的」先列这台电脑上的服务，再列 tailnet 里的 `vortoone.<tailnet>` 和在线的、名字是 `vortoone` 或以 `vortoone-` 开头的设备（最多 5 台）。不扫局域网；每个地址是不是 VortoOne 由窗口问它的 `/api/instance`。
- **常开的电脑**：用一条安装命令装，装完打印所有者认领链接；想被发现和连进来，在 Tailscale 里把它改名为 `vortoone`（或 `vortoone-<名字>`）、跑 `tailscale serve`、填 `VORTOONE_PUBLIC_URL`。见 `docs/INSTALL_SERVER.md`。

## 本机管理

在 `frontend/` 运行：

```sh
npm run desktop-dev:status  # 查看两个 App 的安装版本
npm run desktop-dev:version -- 0.3.0-dev.8 # 统一递增开发版版本号（下一版时使用）
npm run desktop-dev:bundle # 从干净且已合并的 main 构建 Apple Silicon macOS 开发安装包
npm run desktop-dev:install # 校验并安装到 /Applications/VortoOne Dev.app
npm run desktop-dev:check-installed # 全新临时数据、无源码/Python 路径验证随包后端和登录，不调用模型
npm run desktop-dev:manage # 打开 GitHub Release 管理
npm run desktop-dev:draft  # 将已校验的开发包和校验和上传为发布草稿
```

首次构建需要 Node、Rust、Xcode 工具和已有的 Developer ID Application 签名身份。打包复用 `desktop/scripts/build-sidecar.mjs` 的固定 CPython 来源与依赖锁，逐个签名后端 Mach-O，再签整个 App，并执行全新数据目录的独立启动验收。脚本默认签名但不向 Apple 提交公证；对外公开分发前仍需公证与对应验收。

Dev7 成品来自提交 [`28d5e294`](https://github.com/DingHappy/VortoOne/commit/28d5e29436985458a72a8a362bc7dd93cefc7f9d)，已额外完成 Apple 公证、票据附加、Gatekeeper、新图标核对、独立启动以及覆盖安装后聊天记录保留的验收。上述结果只针对该成品，重新构建仍需重新执行签名、公证和安装验收。

后续合入 main 的 [AI 应用 MCP 接入](../docs/AI_APPS.md) 尚未进入上述 Dev7 成品。其后端接口和原生授权窗口需要新的构建与验收；合并源码不会自动更新已安装的 App。

安装只会替换开发版的固定路径；已有开发版会先备份再替换，失败时恢复。先退出正在运行的开发版，再执行安装。正式版继续在原来的应用路径。

## 管理发布

开发版使用 `0.3.0-dev.N` 版本号，GitHub tag 为 `v0.3.0-dev.N`。用 `desktop-dev:version` 同步更新配置、Rust 包和锁文件中的版本；命令只接受递增的开发版本号。先走 PR 合并，再构建。

`desktop-dev:draft` 固定创建 Draft + Pre-release，附件包括独立 DMG、`desktop-dev.json` 和 SHA256 校验和。你在 GitHub Releases 中可编辑说明、删除草稿或发布为预发布，供测试者手动安装。开发版不提供正式版自动更新清单，公开稳定版仍由原有 `desktop/` 发版流程管理。

草稿只向有仓库写权限的协作者显示，普通访客看不到安装包；在公开仓库中发布为 Pre-release 后，访客即可下载。`dev` 版本号本身不提供访问限制，已合入公开 main 的源码始终可见。

公开稳定版保留现有可用版本。独立运行和真实桌面验收在开发通道完成后，再核对正式标识、升级保数据、公证与更新清单；不通过修改 Release 标签来替代这些交付步骤。
