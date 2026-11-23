# Desktop 的 GitHub App 接入草案

当前 Settings 可显示 GitHub App 未配置。服务器不提供创建或安装入口。旧的 GitHub 设备授权仅为本机私人助理提供连接器凭据。它按连接器名称使用操作系统用户目录下的凭据，状态只验证本地凭据是否可用。新版 Desktop 的官方连接器 OAuth 仍返回 `connector.unsupported`。因此工作区没有可信的 GitHub 组织或仓库清单来源。 [现有扩展合同](EXTENSIONS.md)

## App 注册与安装

1. 服务器所有者先配置自己的 GitHub App，或通过 `POST /api/app/github-app/manifest` 发起注册。注册回调核对短时、单次使用的 `state` 以及发起人和回跳路径，再向 GitHub 兑换 App 身份及凭据。App 私钥只进入服务器已有的密钥存储，界面只读状态、名称和公开链接。来源为环境配置时，Settings 显示只读。 [GitHub App manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest)
2. `POST ORG/github-app/install` 由工作区所有者开始安装，并把安装流程绑定到当前 App、工作区和发起会话。`POST ORG/github-app/complete` 核对发起凭证后，通过 GitHub 用户授权与安装接口确认安装属于该 App 且安装者有相应的权限。回调里的 `installation_id` 无法独立证明归属。操作者的浏览器需要能访问回跳地址。 [GitHub 安装配置](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url)
3. 验证通过后，工作区登记保存 App ID、安装 ID、GitHub 账号标识及安装元数据。绑定记录区分活跃、暂停和失效。只有注册凭据可用、安装回跳可用且验证流程接好时，`info` 才开启安装按钮。安装绑定和密钥分别存储。安装令牌按需短期签发并用于其对应安装。 [GitHub 安装令牌](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
4. 工作区解绑和从 GitHub 卸载是两种操作。旧的本地访问在解绑后立即关闭。只有 GitHub 确认卸载成功后，卸载接口才返回 `uninstalled: true`。移除服务器 App 配置时逐个作废其本地安装绑定。 [GitHub App REST 接口](https://docs.github.com/en/rest/apps/apps)

## 组织与仓库读取

- `GET ORG/github/access` 用该工作区的已验证 GitHub 用户授权读取可访问组织。GitHub 成功返回空集合时回 `[]`。来源不存在或 GitHub 查询失败时按客户端合同返回未知状态。管理链接需来自对应授权的 GitHub 页面。
- `GET ORG/github/repositories` 从当前工作区有效绑定的安装或用户授权读取 GitHub 实际授予的仓库，按账号分组。多个来源重复列出的仓库按稳定仓库 ID 去重，分批查询并在结果不全时标明截断。读取时重新核对工作区绑定状态。第一批只对已认证所有者返回账号和仓库详情。 [GitHub 安装与仓库 REST 接口](https://docs.github.com/en/rest/apps/installations)
- 读取结果用于设置页和仓库选择器。Agent 访问仍由现有会话权限与确认门决定。当前分支只支持显示 App 未配置。公开 webhook 接收、订阅和任何触发 Agent 执行的行为留给独立批次。

离线验收覆盖状态过期和重用、错误工作区或发起人、安装归属无法确认、暂停或解绑后读取、无授权与空结果的区别、分页，以及成员读不到私有仓库。真机验收使用隔离工作区，由所有者在 GitHub 选择合成测试仓库并核对 Desktop 实际显示的授权范围。
