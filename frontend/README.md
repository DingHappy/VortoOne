# VortoOne Desktop 前端基底

独立的 `VortoOne Dev` 桌面安装版与公开稳定版的管理方式见 [桌面版本管理](DESKTOP_CHANNELS.md)。开发版当前为本地界面预览，使用示例数据。

VortoOne Desktop 包含首页、频道、Agent、线程、任务、Drive、团队与右侧详情，以及首次连接、托盘面板、偏好设置和 notch 等独立窗口。角色使用 VortoOne 素材。

上一版自组的简化聊天界面移为 runtime 接口实验页，不再作为默认入口。

## 查看桌面界面

```sh
cd frontend
npm ci --ignore-scripts
npm run dev
```

- 完整桌面主界面预览：<http://127.0.0.1:5174/desktop-preview.html?lang=zh-Hans>
- 英文对照：将 `lang` 改为 `en`。
- 完整 UI 的服务连接入口：<http://127.0.0.1:5174/>，默认代理到本机已有的原版服务 `http://127.0.0.1:4310`，沿用原始登录与 API 合同。

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

这些窗口沿用上游 `Host` / `DesktopStore`：浏览器预览使用原有 `FakeHost`；Tauri 产品使用原有 native host。预览中的权限、目录、账号和运行记录只保存在假宿主中。

## 源码对应

| 位置 | 作用 |
| --- | --- |
| `apps/desktop/src` | 原始托盘、首次连接、偏好设置、notch、host 与 control 前端 |
| `apps/desktop/src-tauri` | 迁入的原始 Rust 窗口、菜单、托盘与 host 桥接源码 |
| `apps/web/src/main.tsx` | 原始完整应用启动、服务装配和路由 |
| `apps/web/src/desktop-main.tsx` | 默认入口；开发时声明桌面外观元数据，不提供 native 执行权限 |
| `apps/web/src/desktop-preview-app.tsx` | 原始页面的隔离数据预览入口 |
| `packages/ui` / `client` / `tokens` / `i18n` | 原始组件、主题、查询、实时协议和国际化 |
| `apps/web/src/vortoone` | 上一版 VortoCode runtime 协议实验，独立保留 |

来源提交和复制路径见 `upstream.json`。保留上游内部包名和协议名，减少迁移改动；第三方声明见 `NOTICE`。

## 当前接入边界

完整桌面前端与窗口源码已迁入，主窗口和独立窗口的前端资源可构建。新 Tauri app 使用独立 bundle identifier，尚未编译、安装或替换已有桌面应用。

原项目的 `/api/app/*`、组织/频道模型和 runner control 协议与 VortoCode 的 `/api/*`、会话协议不同。现有 runtime 适配实验仍然保留；尚未把它全面接到这套原始页面，也没有启动原版 runner 或创建第二套执行器。

如需运行旧实验：

```sh
npm run runtime:dev
```

打开 <http://127.0.0.1:5177/runtime.html>；它独立代理到原有 `http://127.0.0.1:8080`。可用 `.env.local` 中的 `VORTO_CHAT_SERVER_URL`、`VORTO_RUNTIME_URL` 分别配置两个开发目标。

## 角色

以明确保留的 `03-pixel-slime-v3-full-crown.png` 为造型基准，原图及其校验值保留。本体与三种主要配饰由原稿提取独立透明材质，`packages/ui/src/agent/engine.js` 组合材质、动态配色、黑眼睛和斜向小嘴，不再用五段硬色带近似原稿。

- 外观体验：<http://127.0.0.1:5174/character-lab.html>，可换预设色或自定义 Hex、眼型、表情、工作状态和配饰，外观保存在该浏览器。
- 原来的 Agent 外观编辑器继续使用同一渲染器；头像、贴纸、桌面启动页、搜索和工具插画同步更新。颜色与眼型/配饰是身份配置；表情与工作状态是独立的瞬时输入。
- 加入眼镜配饰，现有耳机、围巾、帽子等仍可切换。增加配饰时在 `ACCESSORIES` 注册像素图层，同步 tokens 的允许列表、类型与翻译。
- `npm run brand:generate` 从同一几何与色板生成 Web favicon、应用图标、邮件素材、桌面 Dock/窗口图标及七种菜单栏状态；无须重新生成角色图片。

透明 PNG 候选保存在 `assets/character-reference/`。其中 `material/` 的无脸本体、眼镜、右侧斜戴耳机和围巾是内置 imagegen 从 v3 提取的材质图层，原始生成文件保留；`node scripts/prepare-character-material.mjs` 将其编译为浏览器与图标导出共用的 RGBA 数据。换色保留局部明暗，脸部表情仍独立绘制，配饰使用相对于本体的固定锚点。原设计稿未覆盖。

## 验证

```sh
npm run check
```

检查完整 Web 与 desktop TypeScript、runtime 协议测试，并分别构建主窗口和独立窗口。为 TypeScript 5.9 补充了一处 `useQuery<Message[]>` 类型参数，未改变查询行为。

本轮已通过实际安装的原版桌面应用抓取首页、对话与偏好设置参考，再对照迁入页面。截图保存在忽略的 `qa-artifacts/`。浏览器中的 Desktop 布局与真实 native 权限、真实 runner、模型执行和正式安装分别验收。
