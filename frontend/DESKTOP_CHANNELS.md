# 桌面版本管理

| 通道 | 应用 | 标识 | 当前用途 | 更新方式 |
| --- | --- | --- | --- | --- |
| 公开稳定版 | VortoOne.app | com.vorto.vortoone | 现有可用桌面 App | 原有正式版自动更新 |
| 开发版 | VortoOne Dev.app | com.vorto.vortoone.dev | 新桌面布局与史莱姆界面验收 | 独立安装包、GitHub 发布草稿 |

两个 App 可并排安装。开发版的 WebKit 数据按独立应用标识保存；默认使用内存示例数据和假宿主，不读取正式版账号、项目或运行状态，不配置自动更新。只有在本机放了 `backend.json` 时才会启动一个本机后端（见下一节）。辅助窗口在 native 环境中继续使用假宿主。

开发版菜单「开发版」可打开主界面、史莱姆外观、偏好设置预览、版本管理，以及系统浏览器中的 GitHub 发布管理。正式版下载入口指向现有公开稳定版。

## 开发版连接本机后端

开发版默认仍是示例数据。要让它用真实后端，在开发版的配置目录（macOS：`~/Library/Application Support/com.vorto.vortoone.dev/`）放一个 `backend.json`：

```json
{ "python": "/绝对路径/python", "source": "/绝对路径/VortoOne 源码", "env_file": "/绝对路径/模型凭据.env" }
```

- `python` 要装好了源码的依赖；`source` 是仓库根目录；`env_file` 可省。
- `env_file` 中的 `OPENAI_*`、`VORTOONE_MODEL*`、`VORTOONE_LLM_*`，以及现有的 `DEFAULT_MODEL`、`LLM_MODEL_CHEAP`、`LLM_MODEL_BALANCED`、`LLM_MODEL_POWERFUL` 会带给后端，其余行忽略。
- 环境文件按 dotenv 语法读取，支持引号、`export` 和行尾注释；注释不会成为模型地址或凭据的一部分。
- 有这个文件时，App 启动会在本机回环地址上起一个后端（私人助理范围，不开 IM、定时与巡视），主窗口打开它并自动登录；没有时行为与原来一样。菜单「开发版 → 连接本机后端」可以重连。
- 主人令牌每次启动现生成，只在 App 进程和后端里；窗口拿到的是一次性的交接码，不是令牌。
- 数据在开发版自己的目录（`…/com.vorto.vortoone.dev/home`），不读正式版的账号、项目或运行状态；后端日志在同目录的 `backend.log`。
- 开发版不带 Python 运行时，所以这只适合有源码的开发机。随包带运行时属于做正式版时的工作。

## 本机管理

在 `frontend/` 运行：

```sh
npm run desktop-dev:status  # 查看两个 App 的安装版本
npm run desktop-dev:version -- 0.3.0-dev.5 # 统一递增开发版版本号（下一版时使用）
npm run desktop-dev:bundle # 从干净且已合并的 main 构建 Apple Silicon macOS 开发安装包
npm run desktop-dev:install # 校验并安装到 /Applications/VortoOne Dev.app
npm run desktop-dev:manage # 打开 GitHub Release 管理
npm run desktop-dev:draft  # 将已校验的开发包和校验和上传为发布草稿
```

首次构建需要 Node、Rust、Xcode 工具和已有的 Developer ID Application 签名身份。脚本签名但不向 Apple 提交公证。当前开发包面向本机验收；提供给其他人的可分发安装包需要后续公证和对应验收。

安装只会替换开发版的固定路径；已有开发版会先备份再替换，失败时恢复。先退出正在运行的开发版，再执行安装。正式版继续在原来的应用路径。

## 管理发布

开发版使用 `0.3.0-dev.N` 版本号，GitHub tag 为 `v0.3.0-dev.N`。用 `desktop-dev:version` 同步更新配置、Rust 包和锁文件中的版本；命令只接受递增的开发版本号。先走 PR 合并，再构建。

`desktop-dev:draft` 固定创建 Draft + Pre-release，附件包括独立 DMG、`desktop-dev.json` 和 SHA256 校验和。你在 GitHub Releases 中可编辑说明、删除草稿或发布为预发布，供测试者手动安装。开发版不提供正式版自动更新清单，公开稳定版仍由原有 `desktop/` 发版流程管理。

本阶段公开稳定版保留现有可用版本。新界面接通原有后端并通过真实桌面验收后，再构建带正式标识的稳定安装包；不能直接把示例数据开发包改成 Stable 发布。
