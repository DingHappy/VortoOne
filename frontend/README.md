# VortoOne 新桌面前端

`VortoOne Dev` 是与公开稳定版并排安装的独立桌面 App，默认启动随包 Python 后端并连接真实本机工作区。它包含新桌面布局、可自定义的史莱姆、模型连接窗口、主人聊天和成员私聊实时更新。安装包无需本机源码、Python、Node 或 Rust。

截至 2026-11-17，已交付并验证覆盖安装的开发包为 `0.3.0-dev.7`；公开稳定版仍为 `0.2.64`。Dev 通过独立 DMG 手动更新，发布记录保留为草稿。通道、模型配置、数据目录、构建及安装命令见 [桌面版本管理](DESKTOP_CHANNELS.md)。

界面源码还包含频道、线程、任务、Drive、团队、详情栏，以及首次连接、托盘面板、偏好设置和 notch 等辅助窗口。页面存在不表示其中每项上游服务或 native 能力已经接入，具体边界见下文。

## 源码连接本项目后端

安装版由原生宿主启动后端并完成一次性交接登录。调试 Web 前端时，可先在已安装项目依赖的终端启动后端：

```sh
vortoone-runtime server --host 127.0.0.1 --port 4310
```

另开终端：

```sh
cd frontend
npm ci --ignore-scripts
npm run dev
```

- 服务连接入口：<http://127.0.0.1:5174/>，默认将 API 和 WebSocket 代理到 `http://127.0.0.1:4310`，使用本项目的兼容 API 与登录流程。
- 后端在其他地址时，在 `frontend/.env.local` 设置 `VORTO_CHAT_SERVER_URL` 后重启开发服务器。浏览器按现有流程登录：主人使用服务令牌，成员使用账号密码；安装版的原生交接登录不会由静态页面提供。

## 隔离数据界面预览

- 桌面主界面：<http://127.0.0.1:5174/desktop-preview.html?lang=zh-Hans>
- 英文对照：将 `lang` 改为 `en`。

预览直接装配原来的 `routeTree`、`AppProviders`、`Sidebar`、`DesktopTitleBar`、`HomePage` 和 `ChannelPage`，使用上游已有的内存 fixtures。页面中的成员、任务、连接和审批均为隔离示例数据，不会调用真实模型或提交业务操作。fixtures 入口只允许开发模式或明确的独立预览构建。

## 部署独立预览

```sh
npm run build:preview
```

将 `apps/web/dist-preview/` 整个目录交给静态服务器，根路径是完整桌面主界面；`/character-lab.html` 是角色编辑页；`/windows/?window=prefs&scenario=selfhost&lang=zh-Hans&bare=1` 是偏好设置预览。主界面使用 `?lang=zh-Hans` 切换中文，`version.json` 记录构建提交。

该命令使用专门的 `desktop-preview` 构建模式，打包内存数据与假宿主，不代理任何后端，也不包含 native 执行器。默认 `npm run build` 继续输出原有应用，不会启用此预览入口。发布应从干净的 main 构建；资源使用相对路径，可以托管在独立端口或现有 HTTPS 入口的独立路径下。

另开终端查看独立桌面窗口：

```sh
npm run desktop:dev
```

- 偏好设置：<http://127.0.0.1:1421/?window=prefs&scenario=selfhost&lang=zh-Hans>
- 托盘面板：`?window=popover&scenario=selfhost&lang=zh-Hans`
- 首次连接：`?window=welcome&scenario=first&lang=zh-Hans`
- `bare=1` 隐藏开发切换栏，保留实际窗口尺寸。

这些辅助页面沿用迁入的 `Host` / `DesktopStore` 模型；浏览器预览使用 `FakeHost`。示例界面和偏好预览中的权限、目录、账号及运行记录保存在假宿主中，不授予真实执行器权限。已交付 Dev 安装版的主窗口由独立宿主管理，默认连接真实后端。

## 源码对应

| 位置 | 作用 |
| --- | --- |
| `apps/desktop-dev` | 已交付的独立 Dev 原生宿主、模型连接与版本管理窗口 |
| `apps/desktop/src` | 原始托盘、首次连接、偏好设置、notch、host 与 control 前端 |
| `apps/desktop/src-tauri` | 迁入的原始辅助窗口及 host 桥接源码，不是独立 Dev 主宿主 |
| `apps/web/src/main.tsx` | 原始完整应用启动、服务装配和路由 |
| `apps/web/src/desktop-main.tsx` | 默认入口；开发时声明桌面外观元数据，不提供 native 执行权限 |
| `apps/web/src/desktop-preview-app.tsx` | 原始页面的隔离数据预览入口 |
| `packages/ui` / `client` / `tokens` / `i18n` | 原始组件、主题、查询、实时协议和国际化 |
| `apps/web/src/vortoone` | 旧 runtime 协议调试页，独立保留 |

前端来源版本、版权及第三方声明见 [仓库 NOTICE](../NOTICE)。

## 当前接入边界

独立 Dev 安装版已构建、签名、公证并完成本机覆盖安装验收，标识为 `com.vorto.vortoone.dev`。公开稳定版的标识为 `com.vorto.vortoone`，仍由根目录 `desktop/` 的流程交付。

新界面通过 `src/web/routers/app_api.py`、`src/web/app_bridge.py`、`app_views.py`、`app_staff.py` 和 `app_members.py` 等适配复用现有 Python runtime。主人聊天与模型回复已在实际安装的 App 中验证；成员使用 `/api/ws` 接收自己有权访问的 agent 私聊消息和任务结果，首次订阅及重连补发有界快照。

main 已实现 [AI 应用接入第一阶段](../docs/AI_APPS.md)：服务作为远程 MCP 服务器，以连接者身份给 agent 派活、读取回复，并增加本机授权确认窗口。该改动晚于 Dev7 的构建提交；现有 Dev7 安装包不包含它，下一版需重新打包与单独验收。

其他上游设置和 runner control 等功能仍需逐项接入和验收；不能把静态预览中的成功状态作为真实后端能力。独立 Dev 宿主不启动上游 runner，也不创建第二套 Agent 执行器。

旧 runtime 协议调试页仍单独保留：

```sh
npm run runtime:dev
```

打开 <http://127.0.0.1:5177/runtime.html>；它独立代理到原有 `http://127.0.0.1:8080`。可用 `.env.local` 中的 `VORTO_CHAT_SERVER_URL`、`VORTO_RUNTIME_URL` 分别配置两个开发目标。

## 角色

以明确保留的 `03-pixel-slime-v3-full-crown.png` 为造型基准，原图及其校验值保留。本体与三种主要配饰由原稿提取独立透明材质，`packages/ui/src/agent/engine.js` 组合材质、动态配色、黑眼睛和斜向小嘴，不再用五段硬色带近似原稿。

- 外观体验：<http://127.0.0.1:5174/character-lab.html>，可换预设色或自定义 Hex、眼型、表情、工作状态和配饰，外观保存在该浏览器。
- 原来的 Agent 外观编辑器继续使用同一渲染器；头像、贴纸、桌面启动页、搜索和工具插画同步更新。颜色与眼型/配饰是身份配置；表情与工作状态是独立的瞬时输入。
- 加入眼镜配饰，现有耳机、围巾、帽子等仍可切换。增加配饰时在 `ACCESSORIES` 注册像素图层，同步 tokens 的允许列表、类型与翻译。
- `npm run brand:generate` 生成 Web 和迁入辅助窗口的角色品牌素材；独立 Dev 安装包与公开稳定版的打包图标统一使用根目录 `desktop/app-icon.png`，通过 `npm --prefix ../desktop run icons:generate` 生成打包资源。
- README 使用 [应用标志](../desktop/app-icon.svg) 和 [像素字标](../design/vortoone-wordmark.svg)；[深色背景字标](../design/vortoone-wordmark-dark.svg) 保留相同轮廓与珊瑚橙装饰。

透明 PNG 候选保存在 `assets/character-reference/`。其中 `material/` 的无脸本体、眼镜、右侧斜戴耳机和围巾是内置 imagegen 从 v3 提取的材质图层，原始生成文件保留；`node scripts/prepare-character-material.mjs` 将其编译为浏览器与图标导出共用的 RGBA 数据。换色保留局部明暗，脸部表情仍独立绘制，配饰使用相对于本体的固定锚点。原设计稿未覆盖。

## 验证

```sh
npm run check
```

检查 Web 与辅助窗口的 TypeScript、离线协议测试，并分别构建前端资源。

原生安装后另运行 `npm run desktop-dev:check-installed`：在全新临时数据、无源码/Python 环境中校验内置后端、页面和交接登录，不发送模型请求。真实模型回复、原生窗口重连和升级后记录保留须在 App 中单独验收。示例数据预览和真实安装共用界面，但验收范围不同。
