# 新版 Desktop 后端接口：剩余清单

核对日期 2026-11-22，基于 main（已含 #971–#978）。
客户端合同里一共 631 个不重复接口，后端已接 354 个，剩 **277** 个：
- **要做：206**（其中 27 个在界面代码里没有调用，可以往后排）
- **Codex 连接电脑线：58**（设备、机器、浏览器、浏览器配置、AI 账号）
- **按已定决定不做：13**
- **暂缓：0**（投票与选择卡已在 agent 进团队频道后接上）

表里 ☐ 是一般待办，☑ 是已接入的（注明限制），— 表示单独统计的连接电脑待办、已定不做或暂缓。「读已接、写没接」表示后端只有 GET，界面保存会 405。

> **接口统计口径（2026-11-22）**。「已接」按清单中打勾的客户端合同计数。已经登记路由但缺合同关键行为的接口仍记待办。已打勾的接口中有至少三十来个返回固定答案。
> `app_api.py` 的 `_EMPTY` 表回固定的空，另一些回固定值或一律拒绝。多数是如实的「没有这个功能」，但数据明明存在却回空的是缺陷：
> `task-manager`、`waiting`、`active-runs` 已改成读真实记录；`mail/accounts`、`calendar`、`proposals` 等还要逐个核对。
> 报进度时把这一类单独说，别混在「已接」里。
>
> **密钥申请依赖连接电脑线**：`secret-requests` 4 个和 agent 的 `secrets` 2 个，合同里密钥只有三种去处：那台电脑上 agent 的
> 环境变量、文件、浏览器里的登录框。家里的 agent 三样都没有，现在接上只会收下密钥而没人能用；等电脑侧能执行时一起做。

## 怎么核对

- **合同**：`frontend/packages/client/src/api/http-client.ts`（路径与方法）和 `types.ts`（字段）
- **后端**：`src/web/routers/app_api.py`
- **清单的生成方法**：解析 `http-client.ts` 里每个 `request(...)` 调用的路径和方法，和 `tests/integration/server_routes_baseline.json` 按路径段匹配（路径参数当通配）。「界面代码里没调用」是在 `apps/`、`packages/` 里搜 `.命名空间.方法` 没搜到

## 已经做完的（参考写法）

| PR | 内容 | 可复用的 |
|---|---|---|
| #971 | 侧栏偏好、界面语言、空间名字和时区 | `src/web/app_prefs.py`：每个人自己的偏好、整个家的开关 |
| #972 | GIF | `src/web/app_gifs.py`、`src/web/provider_keys.py`（第三方 Key，0600，只回末 4 位） |
| #973 | 语音消息 | `src/web/app_voice.py`：不引入 python-multipart，用标准库解析上传，先限大小 |
| #974 | 技能写入、删制品、批量审批 | `_owner_write`、`_skill_write` |
| #975 | 背景信息进所有 agent 系统提示 | `assistant_profile.home_context_part` |
| #976 | 例行工作新建（走提议核对）、排期解析 | `src/web/app_schedule.py` |
| #977 | 邀请批量、修改、全部列表 | `MemberRegistry.update_invitation` |
| #978 | 任务看板 | `src/workflow/board/`、`src/web/app_board.py`：新登记处加权限层的写法 |

## 约定（这次踩出来的）

1. **不假装成功**：没有对应能力的写入回 4xx 并说明原因，不存一份不生效的数据。客户端对这几个接口的 404 本来就当「没有这功能」并藏起入口：遥测、Jev、GIF Key、语音 Key、`push/web`、首次引导进度，`settings` 里的 `*Available` 字段也是这个作用
2. **谁在调用**：一律用 `member_auth.principal(request, root)`，不要写死「登录的就是主人」
   - 成员能用的路由要加进 `member_auth.MEMBER_ROUTES`，处理函数里再按权限判断
   - `tests/unit/test_member_login.py` 会用成员会话把全服务扫一遍，并核对白名单里的路由都存在。它把路径参数填成 `x`，所以白名单正则别写成 `\d+`
3. **主人写入**：要 `views.owner_connection()`（服务设了主人令牌）；涉及派活、频道等的，还要 `app_staff.owner_proof(request)`（Bearer，或 Cookie 加同源）
4. **成员权限**：`app_members.permission_keys()` 返回的是界面上的键名（如 `edit_wiki`、`assign_work`、`invite_people`），不是内核的 `material.edit` 这种名字
5. **新路由**要把新增的条目插进 `tests/integration/server_routes_baseline.json` 里对应路径的位置，不要整体重排；多个 PR 并行时这里最容易冲突
6. **新状态文件**：生成态登记进 `src/utils/state_dir.py` 的 `_STATE_ENTRIES`；主人自己写的配置（像 `persona.md`、`home_context.md`）登记进 `test_state_dir_coverage.py` 的 `USER_CONFIG`
7. **安全边界，别动**：
   - 例行工作必须走「提议 → 主人按摘要核对」
   - 助理提的专员提议进新界面的审批列表（2026-11-22 主人定）：按界面上看到的那一版核对、只有所有者能批、不参加批量；例行工作核对页仍只列成员提交的和界面新建的
   - agent 权限页（access/brain/repositories/scopes）保持只读
   - 往 agent 系统提示里加东西，要标明「是资料不是指令」，并且同一会话内逐字不变
   - 外部内容按污点处理
8. Codex 连接电脑线单独统计，按批次提交 PR。ADR-008 冻结的 `workflow/tasks` 高级模块继续冻结。不新增依赖（要加就单独开 PR 刷锁文件）
9. **本地门禁**：本机 Python 3.14 下 `test_computer_connector.py::test_real_https_validates_injected_ca_and_rejects_untrusted_peer[True]` 必挂，和改动无关；CI 上的 3.10 和 3.12 是绿的
10. **端到端验证**要带 `VORTOONE_WORKSPACE_SCOPE=general`，常驻助理和 Desktop 都以这个范围运行
11. 在 worktree 里不要用 `git stash`：它整个仓库共用，会弹出别人的暂存
12. 每完成两到三块，在 General 作用域的原生 Desktop 验证实际入口，并在对应 PR 记录操作结果和未覆盖的场景。个人 Vault、Markdown 导入和个人状态的验收分别见 #995、#1004、#1002

## 建议顺序

wiki → 任务 missions → 频道 / 话题 / 消息 / 通知 / 加入申请 / 邀请链接 → 制品、技能、例行的剩余部分 → 个人与账号 → vault / 凭据 / 连接器 / 数据库 / 存储 → 邮件、日历、提醒 → 其它零散的

几处需要主人定的：
- wiki 页面进 agent 上下文时，成员写的内容算不算可信
- vault 里的密钥归谁、怎么撤销
- 存储要不要接 S3（D06、D07 当时都没定）

## 清单

### org/missions（40 个，要做 32）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/missions` | `missions.create` | 仅所有者在已加入的团队频道话题新建草案。话题归档后新建返回 409，既有草案仍可读取并按原参数重试。 |
| ☐ | GET | `ORG/missions/eligible-agents` | `missions.eligibleAgents` |  |
| ☑ | GET | `ORG/missions/{x}` | `missions.get` |  |
| ☐ | PATCH | `ORG/missions/{x}` | `missions.setResumeTasks` |  |
| ☑ | GET | `ORG/missions/{x}/brain` | `missions.brain` | 仅所有者读取草案绑定专员的模型；专员版本变化后拒绝。模型配置只读，不探测或启动执行。 |
| ☐ | PUT | `ORG/missions/{x}/brain` | `missions.setBrain` |  |
| ☐ | POST | `ORG/missions/{x}/close` | `missions.close` |  |
| ☐ | PUT | `ORG/missions/{x}/command-approval` | `missions.setCommandApproval` |  |
| ☐ | DELETE | `ORG/missions/{x}/command-rules/{x}` | `missions.removeCommandRule` |  |
| ☐ | GET | `ORG/missions/{x}/commands` | `missions.commands` |  |
| ☐ | POST | `ORG/missions/{x}/commands/retry` | `missions.retryCommand` |  |
| ☐ | POST | `ORG/missions/{x}/commands/{x}/decide` | `missions.decideCommand` |  |
| ☐ | GET | `ORG/missions/{x}/decisions` | `missions.decisions` |  |
| ☐ | POST | `ORG/missions/{x}/decisions/approve-provisional` | `missions.approveProvisional` |  |
| ☐ | POST | `ORG/missions/{x}/decisions/{x}/choose` | `missions.choose` |  |
| ☐ | POST | `ORG/missions/{x}/decisions/{x}/opinion` | `missions.opinion` |  |
| ☐ | GET | `ORG/missions/{x}/deliveries` | `missions.deliveries` |  |
| ☐ | POST | `ORG/missions/{x}/deliveries/{x}/accept` | `missions.acceptDelivery` |  |
| ☐ | POST | `ORG/missions/{x}/deliveries/{x}/changes` | `missions.requestDeliveryChanges` |  |
| ☐ | PUT | `ORG/missions/{x}/deliveries/{x}/checks/{x}` | `missions.checkDelivery` |  |
| ☐ | PUT | `ORG/missions/{x}/executor` | `missions.setExecutor` |  |
| ☑ | GET | `ORG/missions/{x}/files` | `missions.files` | 所有者可查看已有草案的空文件列表。草案尚无隔离目录或文件。 |
| ☐ | GET | `ORG/missions/{x}/join-requests` | `missions.joinRequests` |  |
| ☐ | POST | `ORG/missions/{x}/join-requests` | `missions.requestJoin` |  |
| ☐ | POST | `ORG/missions/{x}/join-requests/{x}/{x}` | `missions.decideJoin` |  |
| ☐ | PUT | `ORG/missions/{x}/machine` | `missions.setMachine` | 界面代码里没调用 |
| ☐ | POST | `ORG/missions/{x}/messages` | `missions.message` | 界面代码里没调用 |
| ☑ | GET | `ORG/missions/{x}/participants` | `missions.participants` |  |
| ☐ | POST | `ORG/missions/{x}/participants` | `missions.json` | 界面代码里没调用 |
| ☐ | DELETE | `ORG/missions/{x}/participants/{x}` | `missions.removeParticipant` |  |
| ☐ | PATCH | `ORG/missions/{x}/participants/{x}` | `missions.setParticipant` |  |
| ☑ | GET | `ORG/missions/{x}/plan` | `missions.plan` |  |
| ☐ | GET | `ORG/missions/{x}/reviews` | `missions.reviews` |  |
| ☐ | POST | `ORG/missions/{x}/reviews/{x}/{x}` | `missions.review` |  |
| ☑ | GET | `ORG/missions/{x}/settings` | `missions.settings` | 仅所有者读取已有草案；审批策略固定，草案没有规则或执行统计。 |
| ☐ | POST | `ORG/missions/{x}/suggestions/{x}/{x}` | `missions.suggestion` |  |
| ☑ | GET | `ORG/missions/{x}/summary` | `missions.summary` |  |
| ☐ | POST | `ORG/missions/{x}/transfer` | `missions.transfer` |  |
| ☐ | POST | `ORG/missions/{x}/worktree/retry` | `missions.retryWorktree` |  |
| ☐ | POST | `ORG/missions/{x}/{x}` | `missions.control` |  |

### org/agents（35 个，要做 23）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | PUT | `ORG/agents/{x}/access` | `access.set` | 不做：改它等于改岗位授权，岗位改动要走提议核对，新界面没有核对页 |
| ☑ | GET | `ORG/agents/{x}/approval-rules` | `approvals.rules` | 没有按 agent 存的放行规则（只认主人的授权档位），是空的 |
| ☑ | DELETE | `ORG/agents/{x}/approval-rules/{x}` | `approvals.removeRule` | 同上，一律 404 |
| ☑ | POST | `ORG/agents/{x}/audience/mail-loss` | `agents.mailLoss` | agent 不接邮箱，回空列表 |
| — | PUT | `ORG/agents/{x}/brain` | `agents.setBrain` | 不做：改它等于改岗位授权，岗位改动要走提议核对，新界面没有核对页 |
| ☑ | GET | `ORG/agents/{x}/brain${scope ` | `agents.brain` | 就是已接的只读 `brain`，`scope` 不影响结果 |
| ☐ | PUT | `ORG/agents/{x}/browser` | `browser.setDomains` | 读已接、写没接 |
| ☐ | PUT | `ORG/agents/{x}/browser-profile` | `browserProfiles.setDefault` | 界面代码里没调用 |
| ☐ | POST | `ORG/agents/{x}/browser-profile/request` | `browserProfiles.request` | 界面代码里没调用 |
| ☐ | POST | `ORG/agents/{x}/browser/open` | `browser.open` |  |
| ☐ | POST | `ORG/agents/{x}/browser/restart` | `browser.restart` |  |
| ☐ | POST | `ORG/agents/{x}/browser/stop` | `browser.stop` |  |
| ☑ | GET | `ORG/agents/{x}/channels` | `agents.channels` | agent 所在的团队频道（只给所有者） |
| ☑ | PUT | `ORG/agents/{x}/channels` | `agents.setChannels` | 所有者整组替换；主助理、本机 CLI、连登记项目的 agent 不进频道；不 @ 也回答回 409 |
| ☐ | PUT | `ORG/agents/{x}/computer/target` | `sandbox.setTarget` |  |
| ☐ | GET | `ORG/agents/{x}/databases` | `databases.agent` |  |
| ☐ | PUT | `ORG/agents/{x}/databases/{x}` | `databases.setAgent` |  |
| ☐ | GET | `ORG/agents/{x}/desktop/picture` | `desktop.picture` |  |
| ☐ | GET | `ORG/agents/{x}/for-you` | `agentForYou.get` | 界面代码里没调用 |
| ☑ | PUT | `ORG/agents/{x}/group` | `agentGroups.move` | 分组就是部门，调整要经提议，回 409 |
| ☐ | DELETE | `ORG/agents/{x}/holds/{x}` | `desktop.stopHold` |  |
| ☑ | GET | `ORG/agents/{x}/learned-skills` | `skills.learned` | agent 不自己写技能，是空的 |
| ☐ | GET | `ORG/agents/{x}/mail` | `mail.agent` |  |
| ☐ | PUT | `ORG/agents/{x}/mail/{x}` | `mail.setAgent` |  |
| ☐ | GET | `ORG/agents/{x}/merge-repositories` | `repositories.merge` |  |
| ☐ | PUT | `ORG/agents/{x}/merge-repositories` | `repositories.setMerge` |  |
| ☐ | PUT | `ORG/agents/{x}/mission-command-approval` | `missions.setAgentDefault` |  |
| — | PUT | `ORG/agents/{x}/repositories` | `repositories.set` | 不做：改它等于改岗位授权，岗位改动要走提议核对，新界面没有核对页 |
| — | PUT | `ORG/agents/{x}/scopes` | `scopes.set` | 不做：改它等于改岗位授权，岗位改动要走提议核对，新界面没有核对页；界面代码里没调用 |
| ☐ | GET | `ORG/agents/{x}/secrets` | `secrets.list` |  |
| ☐ | DELETE | `ORG/agents/{x}/secrets/{x}${machineId ` | `secrets.remove` |  |
| ☐ | GET | `ORG/agents/{x}/usage` | `agents.planUsage` |  |
| ☐ | GET | `ORG/agents/{x}/usage-queue` | `agents.usageQueue` |  |
| ☐ | POST | `ORG/agents/{x}/usage-queue/cancel` | `agents.cancelUsageQueue` |  |
| ☐ | GET | `ORG/agents/{x}/{x}/control` | `browser.agentControl` | 读已接、写没接 |

### org/wiki（24 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | PUT | `ORG/wiki/bulk/agents` | `wiki.setAgents` |  |
| ☑ | POST | `ORG/wiki/bulk/delete` | `wiki.removeMany` |  |
| ☑ | GET | `ORG/wiki/folder-audience` | `wiki.folderAudience` | 仅所有者读取当前文件夹和最近有规则的上级。成员名单叠加祖先限制，只显示当前在册成员。 |
| ☑ | PUT | `ORG/wiki/folder-audience` | `wiki.setFolderAudience` | 仅所有者按在册成员收紧，页面与各级文件夹规则取交集。只有所有者能移动页面并收紧可见范围，移动不能扩大可见范围。角色、编辑权限和智能体授权返回 409。 |
| ☑ | GET | `ORG/wiki/graph` | `wiki.graph` | 节点与链接两端均按当前查看者的页面权限筛选 |
| ☐ | POST | `ORG/wiki/import` | `wiki.import` | 支持纯 Markdown 文件、Obsidian 笔记和所有者的 GitHub 笔记，保存来源、目录、标签与正文。重发相同导入号及批次内容会复用回执。笔记别名和 Drive 附件待接。 |
| ☑ | GET | `ORG/wiki/import/github` | `wiki.githubNotes` | 所有者预览公开 GitHub 仓库、分支目录或单个 Markdown 文件。仅返回笔记 ZIP，保存在后续导入确认时执行。私有仓库及附件暂不可用。 |
| ☑ | POST | `ORG/wiki/pages` | `wiki.create` | 读已接、写没接 |
| ☑ | DELETE | `ORG/wiki/pages/{x}` | `wiki.remove` |  |
| ☑ | GET | `ORG/wiki/pages/{x}` | `wiki.get` |  |
| ☑ | PUT | `ORG/wiki/pages/{x}` | `wiki.update` |  |
| ☑ | POST | `ORG/wiki/pages/{x}/append` | `wiki.append` | 支持摘录已加入的公开团队频道消息。私信和受限频道来源暂不可用 |
| ☑ | PUT | `ORG/wiki/pages/{x}/audience` | `wiki.audience` | 所有者可在上级文件夹权限内按成员收紧单页的可见范围，保留原链接。扩大范围、角色、独立编辑授权和智能体授权返回 409。 |
| ☑ | POST | `ORG/wiki/pages/{x}/context` | `wiki.context` | 已接入。开启时返回明确的 409，页面不会进入智能体上下文 |
| ☑ | GET | `ORG/wiki/pages/{x}/history` | `wiki.history` |  |
| ☑ | POST | `ORG/wiki/pages/{x}/lock` | `wiki.lock` |  |
| ☑ | POST | `ORG/wiki/pages/{x}/move` | `wiki.move` |  |
| ☑ | POST | `ORG/wiki/pages/{x}/pin` | `wiki.pin` |  |
| ☑ | POST | `ORG/wiki/pages/{x}/rename` | `wiki.rename` |  |
| ☑ | GET | `ORG/wiki/pages/{x}/versions/{x}` | `wiki.version` |  |
| ☐ | GET | `ORG/wiki/reads` | `wiki.reads` |  |
| ☑ | GET | `ORG/wiki/search` | `wiki.search` | 按当前标题搜索，只回调用者可见的未删除页面摘要；界面仍用本地筛选。 |
| ☑ | POST | `ORG/wiki/trash/purge` | `wiki.purge` |  |
| ☑ | POST | `ORG/wiki/trash/restore` | `wiki.restoreMany` |  |

### org/devices（21 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/devices` | `devices.list` | 只列当前调用者配对的有效电脑，在线时说明仅支持配对能力。 |
| — | POST | `ORG/devices/access-requests/{x}/decide` | `devices.decideAccess` | Codex 连接电脑线 |
| ☑ | DELETE | `ORG/devices/computers/{x}` | `devices.removeComputer` | 由配对人撤销凭据并断开对应连接。 |
| — | PATCH | `ORG/devices/computers/{x}` | `devices.updateComputer` | Codex 连接电脑线。配对人可改名，共享、工作区归属、任务数和溢出设置返回 400。 |
| — | POST | `ORG/devices/computers/{x}/phone-looks` | `devices.lookForPhones` | Codex 连接电脑线 |
| — | GET | `ORG/devices/computers/{x}/wifi-iphones` | `devices.wifiIphones` | Codex 连接电脑线 |
| — | PUT | `ORG/devices/computers/{x}/wifi-iphones/{x}` | `devices.pickIphoneOverWifi` | Codex 连接电脑线 |
| — | POST | `ORG/devices/computers/{x}/wifi-pairings` | `devices.startWifiPairing` | Codex 连接电脑线 |
| — | DELETE | `ORG/devices/computers/{x}/wifi-pairings/{x}` | `devices.cancelWifiPairing` | Codex 连接电脑线 |
| — | GET | `ORG/devices/computers/{x}/wifi-pairings/{x}` | `devices.wifiPairing` | Codex 连接电脑线 |
| — | DELETE | `ORG/devices/computers/{x}/xcode-fix` | `devices.cancelXcodeFix` | Codex 连接电脑线 |
| — | GET | `ORG/devices/computers/{x}/xcode-fix` | `devices.xcodeFix` | Codex 连接电脑线 |
| — | POST | `ORG/devices/computers/{x}/xcode-fix` | `devices.startXcodeFix` | Codex 连接电脑线 |
| — | POST | `ORG/devices/computers/{x}/xcode-open` | `devices.openXcode` | Codex 连接电脑线 |
| — | POST | `ORG/devices/connect-links` | `devices.createLink` | Codex 连接电脑线 |
| — | GET | `ORG/devices/connect-links/{x}` | `devices.link` | Codex 连接电脑线 |
| — | DELETE | `ORG/devices/phones/{x}` | `devices.forgetPhone` | Codex 连接电脑线 |
| — | PATCH | `ORG/devices/phones/{x}` | `devices.updatePhone` | Codex 连接电脑线 |
| — | POST | `ORG/devices/phones/{x}/stop` | `devices.stopPhone` | Codex 连接电脑线 |
| — | POST | `ORG/devices/requests/{x}/approve` | `devices.approveRequest` | Codex 连接电脑线 |
| — | POST | `ORG/devices/requests/{x}/deny` | `devices.denyRequest` | Codex 连接电脑线 |

### org/machines（20 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | POST | `ORG/machines/device/{x}/confirm` | `machines.confirmDevice` | Codex 连接电脑线 |
| — | GET | `ORG/machines/host` | `machines.host` | Codex 连接电脑线 |
| — | POST | `ORG/machines/host` | `machines.hostOn` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/access/allow` | `machines.allowTool` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/access/ask` | `machines.askTool` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/access/reduce` | `machines.reduceAccess` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/account-logins` | `machines.beginAccountLogin` | Codex 连接电脑线 |
| — | DELETE | `ORG/machines/{x}/account-logins/{x}` | `machines.cancelAccountLogin` | Codex 连接电脑线 |
| — | GET | `ORG/machines/{x}/account-logins/{x}` | `machines.accountLogin` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/account-logins/{x}/code` | `machines.sendAccountCode` | Codex 连接电脑线 |
| — | DELETE | `ORG/machines/{x}/accounts/{x}` | `machines.removeAccount` | Codex 连接电脑线 |
| — | PATCH | `ORG/machines/{x}/accounts/{x}` | `machines.renameAccount` | Codex 连接电脑线 |
| — | DELETE | `ORG/machines/{x}/cli-install` | `machines.cliInstall` | Codex 连接电脑线 |
| — | GET | `ORG/machines/{x}/cli-install` | `machines.cliInstall` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/cli-install` | `machines.cliInstall` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/cli-install/ask` | `machines.askCliInstall` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/clis/check` | `machines.checkClis` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/move-agents` | `machines.moveAgents` | Codex 连接电脑线 |
| — | GET | `ORG/machines/{x}/queue` | `machines.queue` | Codex 连接电脑线 |
| — | POST | `ORG/machines/{x}/{x}` | `machines.setPause` | Codex 连接电脑线 |

### org/vault（12 个，要做 5）

首批只支持个人登录和 API Key 条目。每个当前成员只访问自己的条目，所有者按同一条规则。删除后当前条目失效，
成员离开后无法访问原个人库。API 只回条目元数据，角色和 Agent 暂不能使用个人库。

部署需已有 `cryptography`，并由管理员显式设置 `VORTOONE_PERSONAL_VAULT_DIR` 指向家和家状态目录之外、
由服务用户持有且权限为 `0700` 的 `.vortoone-personal-vault` 目录。目录需要预置同一用户的 `0600`
普通文件 `master.key`，内容为 32 字节随机密钥。配置缺失、权限不对或库无法解密时，接口返回不可用。
个人库数据按家、身份和信任代次使用 AES-GCM 密封，独立于家备份、搬家包和助理备份。恢复或重新认领会更换信任代次，
新代次从空库开始，旧库仍在原处，不会被访问或覆盖。跨信任代次恢复个人库还未开放。
接口要求有效身份，并使用 HTTPS 或本机回环连接。

现有新建界面默认提交 `allAgents: true`，这个端点会拒绝。在当前界面通过「编辑智能体」关闭「所有智能体」后，可以把条目保存为仅本人可见。

「导入密码」支持 Chrome、1Password、Bitwarden 和有网址、用户名、密码列的 CSV。预览只统计，本人确认后才批量保存。
登录条目按 HTTPS 来源（协议、主机和端口）及用户名匹配，重复条目跳过，唯一匹配且密码变化时只更新密码。
「导入 .env」支持前端解析并审阅后的环境变量，按名称匹配，已存在的只有勾选「替换」才更新，请在界面关闭「所有智能体」。
批量导入沿用原个人库加密文件、身份隔离和 64 条容量上限，响应只回统计。团队库、Agent 授权和密码管理器连接未开放。

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/vault` | `vault.overview` | 仅调用人自己的元数据，显式启用 |
| ☐ | POST | `ORG/vault/connections` | `vault.connect` | 界面代码里没调用 |
| ☐ | DELETE | `ORG/vault/connections/{x}` | `vault.removeConnection` | 界面代码里没调用 |
| ☐ | PATCH | `ORG/vault/connections/{x}` | `vault.updateConnection` | 界面代码里没调用 |
| ☐ | POST | `ORG/vault/connections/{x}/sync` | `vault.syncConnection` | 界面代码里没调用 |
| ☑ | POST | `ORG/vault/import` | `vault.importCsv` | 「导入密码」支持预览及保存到本人库，返回统计 |
| ☑ | POST | `ORG/vault/items` | `vault.create` | 仅个人登录和 Key 条目，禁止共享 |
| ☑ | DELETE | `ORG/vault/items/{x}` | `vault.remove` | 只允许条目本人删除 |
| ☑ | GET | `ORG/vault/items/{x}` | `vault.item` | 仅本人元数据，无原值 |
| ☑ | PATCH | `ORG/vault/items/{x}` | `vault.update` | 只允许本人更新或换值，禁止共享 |
| ☐ | POST | `ORG/vault/items/{x}/move` | `vault.move` | 界面代码里没调用 |
| ☑ | POST | `ORG/vault/keys` | `vault.importKeys` | 「导入 .env」仅本人批量新增或显式替换，返回统计 |

### org/me（12 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | PATCH | `ORG/me` | `orgs.updateMe` | 已接个人时区（清空后跟随空间默认）；用户名仍待接，接受原样传回。 |
| ☑ | PUT | `ORG/me/activity` | `orgs.setShowActivity` |  |
| ☐ | PUT | `ORG/me/agent-style` | `orgs.setAgentStyle` |  |
| ☑ | GET | `ORG/me/approval-cover` | `approvals.covers` | 所有者托付一个成员替自己批主要聊天里的确认：一直或外出时，可设到期；默认只代批低风险的。读过外部内容的、网页登录、文件传输、设备钥匙、专员提议永远留给所有者；每次代批进审计 |
| ☑ | PUT | `ORG/me/approval-cover` | `approvals.setCover` | 只有所有者能设；成员回 409 |
| ☑ | DELETE | `ORG/me/avatar` | `orgs.removeAvatar` | 仅移除本人头像。 |
| ☑ | PUT | `ORG/me/avatar` | `orgs.setAvatar` | 本人上传正方形 PNG 或 JPEG，支持头像绘制配方。仅当前空间的在册成员能读取，安全拒绝其他格式。 |
| ☑ | PUT | `ORG/me/chat` | `orgs.setChatPrefs` |  |
| ☑ | GET | `ORG/me/right-hand-approvals` | `approvals.rightHand` | 如实是关着的：会提议的只有助理自己，代批等于自己批自己的提议；打开回 409。等专员也能提议再接 |
| ☑ | PUT | `ORG/me/right-hand-approvals` | `approvals.setRightHand` | 只接受保持关着，要打开回 409 |
| ☑ | PUT | `ORG/me/start` | `orgs.setStart` |  |
| ☑ | PUT | `ORG/me/status` | `orgs.setStatus` | 状态按人保存，隐身只对本人可见；跨端更新需重新读取列表 |

### org/artifacts（11 个，要做 5）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/artifacts` | `artifacts.create` | 已接所有者通过 JSON 创建私有 HTML，与 Drive HTML 上传使用同一存储和隔离预览。其它种类回 415，附到频道回 422。界面代码里没调用。 |
| ☐ | POST | `ORG/artifacts/files` | `artifacts.upload` | 所有者可私有上传 UTF-8 HTML、TXT、Markdown、CSV 和 JSON。非 HTML 原样下载，小文本在 Drive 以纯文本预览。其它格式返回 415，频道或话题挂载返回 422，通用文件上传仍待接。 |
| ☑ | GET | `ORG/artifacts/{x}/download` | `artifacts.download` | 仅所有者的原始 HTML、已存历史版本及私有文本原件 |
| ☑ | GET | `ORG/artifacts/{x}/file` | `artifacts.fileBlob` | 仅所有者：原始 HTML、已存历史版本和私有文本原件 |
| ☐ | POST | `ORG/artifacts/{x}/files` | `artifacts.uploadVersion` |  |
| ☐ | POST | `ORG/artifacts/{x}/keep` | `artifacts.keep` |  |
| ☐ | POST | `ORG/artifacts/{x}/reply` | `artifacts.reply` |  |
| ☑ | POST | `ORG/artifacts/{x}/restore` | `artifacts.restore` | 所有者可还原 Drive 删除的助理页面、已存历史版本和私有上传；到期回 `409 artifact.purged`，提前永久清除回 `410`。 |
| ☐ | PUT | `ORG/artifacts/{x}/sharing` | `artifacts.setSharing` |  |
| ☑ | GET | `ORG/artifacts/{x}/versions` | `artifacts.versions` | 仅已认证所有者。倒序列出可打开的 HTML 快照，不推断逐版作者或备注。 |
| ☑ | POST | `ORG/artifacts/{x}/versions` | `artifacts.update` | 经认证的所有者可给私有文档和表格添加版本及备注。对照原版本编号，过时返回 409。旧版本可查看、下载；资料不共享到频道。 |

### org/threads（10 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/threads` | `threads.list` |  |
| ☐ | POST | `ORG/threads` | `threads.create` |  |
| ☐ | DELETE | `ORG/threads/{x}` | `threads.remove` | 读已接、写没接 |
| ☑ | PATCH | `ORG/threads/{x}` | `threads.update` | 已接现有团队频道话题的改名，调整选段待接 |
| ☑ | DELETE | `ORG/threads/{x}/follow` | `threadFollows.unfollow` | 取消自己在已加入频道中的话题关注，保留已读位置。当前界面未接入口。 |
| ☑ | PUT | `ORG/threads/{x}/follow` | `threadFollows.follow` | 关注自己能读的团队频道话题并计入个人未读。归档话题始终不计未读。当前界面未接入口。 |
| ☑ | GET | `ORG/threads/{x}/mission` | `missions.ofThread` |  |
| ☑ | POST | `ORG/threads/{x}/read` | `reads.markThreadRead` | Desktop 打开话题时调用。已接团队频道话题的个人已读位置及频道、工作区两处列表的未读状态。侧栏只计有权限、已关注且未归档的话题。 |
| ☑ | GET | `ORG/threads/{x}/tasks` | `missions.threadTasks` | 所有者读取已绑定草案的空执行列表。草案不创建执行会话。 |
| ☐ | POST | `ORG/threads/{x}/{x}` | `threads.act` | 已接关闭、重新打开、归档和解除归档，移动及归还选段待接 |

### org/channels（10 个，要做 4）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/channels/{x}/consultations` | `agents.consultations` |  |
| ☐ | GET | `ORG/channels/{x}/consultations/{x}` | `agents.consultation` |  |
| ☐ | POST | `ORG/channels/{x}/delete` | `chat.deleteChannel` |  |
| ☐ | GET | `ORG/channels/{x}/follow-ups` | `chat.followUps` |  |
| ☑ | DELETE | `ORG/channels/{x}/members/agents/{x}` | `chat.removeMember` | 所有者把 agent 拿出频道（删掉的 agent 也能拿出来） |
| ☑ | PATCH | `ORG/channels/{x}/members/agents/{x}` | `chat.setAutoReply` | 只认「被 @ 才回答」；开不 @ 也回答第一期如实回 409（主人 2026-11-22 定） |
| ☑ | DELETE | `ORG/channels/{x}/mute` | `reads.mute` | 解除自己已加入的团队频道静音，保留未读进度。私聊和其他非团队频道不支持。 |
| ☑ | PUT | `ORG/channels/{x}/mute` | `reads.mute` | 静音自己已加入的团队频道，频道列表隐藏普通未读提示。私聊和其他非团队频道不支持。 |
| ☑ | PUT | `ORG/channels/{x}/place` | `chat.place` |  |
| ☑ | GET | `ORG/channels/{x}/reads` | `dms.reads` | 仅两名当前在册参与人可读，返回对方已确认的持久游标，并向其私聊订阅推送 `channel.read`。 |

### org/usage（10 个，要做 10）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/usage/composio` | `connectors.composioUsage` |  |
| ☐ | GET | `ORG/usage/limits` | `orgs.personLimits` |  |
| ☐ | PUT | `ORG/usage/limits` | `orgs.setPersonLimits` |  |
| ☐ | POST | `ORG/usage/limits/request` | `orgs.requestMoreUsage` |  |
| ☐ | GET | `ORG/usage/people` | `orgs.peopleUsage` |  |
| ☐ | GET | `ORG/usage/people/{x}` | `orgs.personUsage` |  |
| ☐ | POST | `ORG/usage/people/{x}/raise` | `orgs.raisePersonLimit` |  |
| ☐ | POST | `ORG/usage/people/{x}/refuse` | `orgs.refuseMoreUsage` |  |
| ☐ | GET | `ORG/usage/plans` | `orgs.planUsage` |  |
| ☐ | GET | `ORG/usage/report` | `orgs.usageReport` |  |

### org/routines（10 个，要做 6）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/routines/{x}/continue` | `routines.continueUsage` |  |
| ☑ | POST | `ORG/routines/{x}/duplicate` | `routines.duplicate` | 所有者可复制现有本人例行定义；先按摘要审核，副本默认暂停 |
| ☐ | GET | `ORG/routines/{x}/hook` | `routines.hook` |  |
| ☐ | POST | `ORG/routines/{x}/hook/address` | `routines.newHookAddress` |  |
| ☐ | DELETE | `ORG/routines/{x}/hook/secret` | `routines.removeHookSecret` |  |
| ☐ | POST | `ORG/routines/{x}/hook/secret` | `routines.newHookSecret` |  |
| ☐ | POST | `ORG/routines/{x}/hook/test` | `routines.testHook` |  |
| ☑ | POST | `ORG/routines/{x}/run` | `routines.runNow` | 立即运行一次，不改排期和启停；在跑、等验收、等核对时回 409 说明 |
| ☑ | GET | `ORG/routines/{x}/runs` | `routines.runs` | 所有者可读取任务台账中该例行工作最近 30 条运行记录 |
| ☑ | POST | `ORG/routines/{x}/stop` | `routines.stop` | 所有者按当前认领停止本次执行，复用专员停止审计和执行池。没有运行、登记不匹配或取消未受理时拒绝；不改排期。 |

### org/credentials（9 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/credentials` | `agents.addCredential` | 只接 OpenAI 兼容的自定义端点；逐个模型真调一次，全失败不存；同一把 key 同一个地址更新原来那个 |
| ☑ | POST | `ORG/credentials/endpoint-models` | `agents.endpointModels` | 读端点的模型列表，不保存 |
| ☑ | GET | `ORG/credentials/platform` | `agents.platformKeyAvailable` | 服务有没有接上模型 |
| ☑ | DELETE | `ORG/credentials/{x}` | `agents.removeCredential` | 停用或 purge 删掉；是默认的或有 agent 在用要 release |
| ☑ | POST | `ORG/credentials/{x}/check` | `agents.checkCredential` | 用存的 key 真调一次 |
| ☑ | POST | `ORG/credentials/{x}/make-default` | `agents.makeDefaultCredential` | 换掉这个家默认的模型端点；对 server 那一行就是换回服务配置 |
| ☑ | PUT | `ORG/credentials/{x}/models` | `agents.setEndpointModels` | 改模型和价格，逐个再测 |
| ☑ | POST | `ORG/credentials/{x}/remove-copies` | `agents.removeCredentialCopies` | 保存时就合并同一把 key，没有副本，回 0 |
| ☑ | POST | `ORG/credentials/{x}/rotate` | `agents.rotateCredential` | 先用新 key 真调一次，通过才换 |

### org/connectors（9 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/connectors` | `connectors.connectWithKey` | 官方目录是空的：回 409，请用自定义连接器 |
| ☑ | POST | `ORG/connectors/custom` | `customConnectors.connect` | 只接 HTTPS + key 的 MCP：读得到工具目录才存；没给 key 回 needs_key（不做 OAuth） |
| ☑ | DELETE | `ORG/connectors/custom/{x}` | `customConnectors.disconnect` | 先除名再删 key，正在跑的会话立刻用不了 |
| ☑ | POST | `ORG/connectors/custom/{x}/refresh` | `customConnectors.refresh` | 新出现或被对方改过的工具回到待审 |
| ☑ | PUT | `ORG/connectors/custom/{x}/tools` | `customConnectors.saveTools` | 只读要对方也标了只读；其余只能每次问或关；保存过才给会话和 agent |
| ☑ | POST | `ORG/connectors/oauth/complete` | `connectors.completeOAuth` | 不做 OAuth：回 409 |
| ☑ | DELETE | `ORG/connectors/{x}` | `connectors.disconnect` | 自定义连接器走同一条断开 |
| ☑ | PATCH | `ORG/connectors/{x}` | `connectors.rename` | 改自定义连接器的名字 |
| ☑ | POST | `ORG/connectors/{x}/oauth/start` | `connectors.startOAuth` | 不做 OAuth：回 409 |

### org/skills（9 个，要做 8）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/skills/import` | `skills.import` | 所有者可导入只含单个 SKILL.md 的个人 ZIP，需审阅后启用；链接和附件待接 |
| ☐ | POST | `ORG/skills/{x}/reimport` | `skills.reimport` |  |
| ☐ | POST | `ORG/skills/{x}/restore` | `skills.restore` |  |
| ☐ | POST | `ORG/skills/{x}/revert` | `skills.revert` |  |
| ☐ | POST | `ORG/skills/{x}/update` | `skills.updateFromSource` | 界面代码里没调用 |
| ☐ | POST | `ORG/skills/{x}/update/approve` | `skills.approveUpdate` | 界面代码里没调用 |
| ☐ | POST | `ORG/skills/{x}/update/dismiss` | `skills.dismissUpdate` | 界面代码里没调用 |
| ☐ | POST | `ORG/skills/{x}/update/reopen` | `skills.reopenUpdate` | 界面代码里没调用 |
| ☐ | POST | `ORG/skills/{x}/update/undo` | `skills.undoUpdate` | 界面代码里没调用 |

### org/messages（8 个，要做 5）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/messages/{x}/actions/undo` | `actions.undoMessage` | 界面代码里没调用 |
| ☐ | POST | `ORG/messages/{x}/answer-now` | `chat.answerNow` |  |
| ☐ | POST | `ORG/messages/{x}/cancel-follow-up` | `chat.cancelFollowUp` |  |
| ☐ | POST | `ORG/messages/{x}/correction` | `chat.correct` |  |
| ☐ | POST | `ORG/messages/{x}/github-answer` | `chat.answerGitHub` | 界面代码里没调用 |
| ☑ | DELETE | `ORG/messages/{x}/reactions` | `chat.unreact` |  |
| ☑ | GET | `ORG/messages/{x}/reactions` | `chat.reactions` |  |
| ☑ | POST | `ORG/messages/{x}/reactions` | `chat.react` |  |

### org/mail（7 个，要做 7）

已定：邮件只读，保存草稿须用户确认，由用户在自己的邮件客户端发送。
现有本机 IMAP 插件已支持这条流程。它使用操作系统用户的凭据，组织内的邮箱账户和授权仍待接入。
组织连接目录目前明确不提供新增邮箱的入口。

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/mail/accounts/composio` | `mail.connectComposio` |  |
| ☐ | POST | `ORG/mail/accounts/imap` | `mail.connectImap` |  |
| ☐ | DELETE | `ORG/mail/accounts/{x}` | `mail.disconnect` |  |
| ☐ | PATCH | `ORG/mail/accounts/{x}` | `mail.update` |  |
| ☐ | GET | `ORG/mail/accounts/{x}/folders` | `mail.folders` |  |
| ☐ | GET | `ORG/mail/accounts/{x}/suggested-addresses` | `mail.suggestedAddresses` |  |
| ☐ | GET | `ORG/mail/providers` | `mail.providers` | 已接能力查询，两种连接方式暂不可用；组织 IMAP 接通后再开放 |

### org/browser-profiles（7 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `ORG/browser-profiles` | `browserProfiles.list` | Codex 连接电脑线；界面代码里没调用 |
| — | POST | `ORG/browser-profiles` | `browserProfiles.create` | Codex 连接电脑线；界面代码里没调用 |
| — | DELETE | `ORG/browser-profiles/{x}` | `browserProfiles.remove` | Codex 连接电脑线；界面代码里没调用 |
| — | PATCH | `ORG/browser-profiles/{x}` | `browserProfiles.update` | Codex 连接电脑线；界面代码里没调用 |
| — | POST | `ORG/browser-profiles/{x}/handback` | `browserProfiles.handback` | Codex 连接电脑线；界面代码里没调用 |
| — | POST | `ORG/browser-profiles/{x}/open` | `browserProfiles.open` | Codex 连接电脑线；界面代码里没调用 |
| — | POST | `ORG/browser-profiles/{x}/takeover` | `browserProfiles.takeover` | Codex 连接电脑线；界面代码里没调用 |

### /api/app/invitations（7 个，要做 5）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `/api/app/invitations/{x}/accept` | `orgs.acceptInvitation` | 已接密码账号接受指定邮箱的邀请。可选的 `mail` 查询参数不参与当前服务的认证或邮箱确认 |
| ☐ | POST | `/api/app/invitations/{x}/code` | `invites.sendCode` |  |
| ☐ | POST | `/api/app/invitations/{x}/code/check` | `invites.checkCode` |  |
| ☐ | POST | `/api/app/invitations/{x}/request-new` | `invites.requestNew` |  |
| ☐ | POST | `/api/app/invitations/{x}/requests` | `invites.ask` |  |
| ☐ | POST | `/api/app/invitations/{x}/requests/claim` | `invites.claim` |  |
| ☑ | GET | `/api/app/invitations/{x}/state` | `orgs.invitationState` | 保留尚未清理的过期邮箱邀请提示。原有效期内，仅接受该邀请且仍在册的登录成员显式请求 `receipt=1` 时可查看回执。旧客户端和其他人查询已消费的令牌返回 404。 |

### org/databases（6 个，要做 6）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/databases` | `databases.list` |  |
| ☐ | POST | `ORG/databases` | `databases.connect` |  |
| ☐ | DELETE | `ORG/databases/{x}` | `databases.disconnect` |  |
| ☐ | PATCH | `ORG/databases/{x}` | `databases.update` |  |
| ☐ | GET | `ORG/databases/{x}/databases{x}` | `databases.serverDatabases` |  |
| ☐ | GET | `ORG/databases/{x}/tables${database ` | `databases.tables` |  |

### org/join-requests（6 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/join-requests` | `joinRequests.list` | 仅所有者查看在册成员推荐的待核对建议。 |
| ☑ | GET | `ORG/join-requests/mine` | `joinRequests.mine` | 只看自己的待核对建议。 |
| ☑ | POST | `ORG/join-requests/suggest` | `joinRequests.suggest` | 无邀请权限的在册成员可提交邮箱与可选备注。 |
| ☑ | DELETE | `ORG/join-requests/{x}` | `joinRequests.cancel` | 在册成员撤回自己的建议。 |
| ☑ | POST | `ORG/join-requests/{x}/approve` | `joinRequests.approve` | 仅所有者批准，创建默认成员的一次性邮箱邀请。链接由人发送。 |
| ☑ | POST | `ORG/join-requests/{x}/deny` | `joinRequests.deny` | 仅所有者拒绝。当前无法转告拒绝理由，因此非空理由返回 4xx。 |

### org/calendar（6 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/calendar/events` | `calendar.create` | 已接入仅本人可见的单次本地日程 |
| ☑ | DELETE | `ORG/calendar/events/{x}` | `calendar.remove` | 仅删除自己的单次日程 |
| ☑ | PATCH | `ORG/calendar/events/{x}` | `calendar.update` | 仅编辑自己的单次日程 |
| ☐ | POST | `ORG/calendar/events/{x}/rsvp` | `calendar.respond` |  |
| ☐ | POST | `ORG/calendar/proposals/{x}/confirm` | `calendar.confirmProposal` |  |
| ☐ | POST | `ORG/calendar/proposals/{x}/dismiss` | `calendar.dismissProposal` |  |

### org/browser（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `ORG/browser/handoffs/{x}` | `browser.handoff` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/cancel` | `browser.cancel` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/control` | `browser.takeControl` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/done` | `browser.done` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/failed` | `browser.failed` | Codex 连接电脑线 |

### /api/app/account（5 个，要做 5）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | DELETE | `/api/app/account` | `account.delete` |  |
| ☐ | DELETE | `/api/app/account/deletion` | `account.restore` |  |
| ☐ | GET | `/api/app/account/deletion` | `account.deletionPlan` |  |
| ☐ | POST | `/api/app/account/deletion/code` | `account.deletionCode` |  |
| ☐ | POST | `/api/app/account/email` | `account.changeEmail` |  |

### org/agent-proposals（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/agent-proposals/{x}` | `agents.proposal` | 助理在主要聊天里提的新建 agent 以卡片出现在对话里（和审批列表是同一份提议）；只有所有者能看 |
| ☑ | POST | `ORG/agent-proposals/{x}/ask` | `agents.askProposal` | 只有所有者能决定，回 409 |
| ☑ | POST | `ORG/agent-proposals/{x}/create` | `agents.createProposed` | 按卡片上这一版批准，登记处照常核对 |
| ☑ | POST | `ORG/agent-proposals/{x}/deny` | `agents.denyProposal` | 拒绝 |
| ☑ | POST | `ORG/agent-proposals/{x}/routine` | `agents.proposalRoutine` | 提议不附带例行工作，回 409 |

### org/invite-links（5 个，要做 5）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/invite-links` | `invites.links` |  |
| ☐ | POST | `ORG/invite-links` | `invites.createLink` |  |
| ☐ | DELETE | `ORG/invite-links/{x}` | `invites.turnOff` |  |
| ☐ | PATCH | `ORG/invite-links/{x}` | `invites.editLink` |  |
| ☐ | POST | `ORG/invite-links/{x}/renew` | `invites.renewLink` |  |

### org/storage（5 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/storage` | `storage.get` | 显示当前服务器磁盘状态，仅所有者可读 |
| ☐ | PUT | `ORG/storage` | `storage.save` | 当前仅用服务器磁盘，接口明确拒绝 |
| ☐ | POST | `ORG/storage/migrate` | `storage.migrate` | 当前仅用服务器磁盘，接口明确拒绝 |
| ☑ | GET | `ORG/storage/migration` | `storage.migration` | 本地磁盘无迁移任务时返回 null，仅所有者可读 |
| ☐ | POST | `ORG/storage/test` | `storage.test` | 当前仅用服务器磁盘，接口明确拒绝 |

### org/notifications（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/notifications/done-all` | `notifications.doneAll` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/read-all` | `notifications.readAll` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/{x}/done` | `notifications.done` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/{x}/read` | `notifications.read` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/{x}/snooze` | `notifications.snooze` | 仅所有者的后台通知 |

### /api/app/feedback（4 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `/api/app/feedback` | `system.sendFeedback` | 已认证成员或所有者提交本地文字反馈，另有仅所有者可读的本地收件箱 |
| ☐ | POST | `/api/app/feedback/files` | `system.uploadFeedbackFile` |  |
| ☐ | DELETE | `/api/app/feedback/files/{x}` | `system.dropFeedbackFile` |  |
| ☐ | POST | `/api/app/feedback/offers/{x}` | `system.decideFeedbackOffer` |  |

### /api/app/jev（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | DELETE | `/api/app/jev` | `system.removeJev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |
| — | GET | `/api/app/jev` | `system.jev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |
| — | PUT | `/api/app/jev` | `system.saveJev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |
| — | POST | `/api/app/jev/check` | `system.checkJev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |

### /api/app/push（4 个，要做 4）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `/api/app/push/devices` | `push.register` |  |
| ☐ | POST | `/api/app/push/devices/forget` | `push.unregister` |  |
| ☐ | POST | `/api/app/push/stale` | `push.stale` |  |
| ☐ | GET | `/api/app/push/web` | `push.webKey` |  |

### org/team-proposals（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | PATCH | `ORG/team-proposals/{x}/agents/{x}` | `agents.adjustTeamAgent` | 批的是卡片上这一版，要改请助理重新提，回 409 |
| ☑ | POST | `ORG/team-proposals/{x}/create` | `agents.createTeam` | 助理提的开通单（新 agent 连同岗位、例行工作）以团队卡片出现在主要聊天里；按这一版批准整张开通单 |
| ☑ | POST | `ORG/team-proposals/{x}/deny` | `agents.denyTeam` | 拒绝 |
| ☑ | POST | `ORG/team-proposals/{x}/undo` | `agents.undoTeam` | 登记了不能一键撤回，回 409 |

### org/runs（4 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/runs/{x}` | `agents.run` | 与列表同一行；没有逐步记录，`steps` 为空（进度尾部在主人可看的任务输出里） |
| — | POST | `ORG/runs/{x}/continue` | `agents.continueRun` | 不做（2026-11-22 主人定）：停下的事重新交代 |
| ☐ | POST | `ORG/runs/{x}/device-wait` | `devices.deviceWait` |  |
| ☐ | POST | `ORG/runs/{x}/stop-reason` | `agents.stopReason` |  |

### org/channel-categories（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/channel-categories` | `chat.createCategory` | 读已接、写没接 |
| ☑ | DELETE | `ORG/channel-categories/{x}` | `chat.deleteCategory` |  |
| ☑ | PATCH | `ORG/channel-categories/{x}` | `chat.renameCategory` |  |
| ☑ | PUT | `ORG/channel-categories/{x}/place` | `chat.placeCategory` |  |

### org/github-app（4 个，要做 4）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/github-app` | `githubApp.info` |  |
| ☐ | POST | `ORG/github-app/complete` | `githubApp.complete` |  |
| ☐ | POST | `ORG/github-app/install` | `githubApp.install` |  |
| ☐ | DELETE | `ORG/github-app/installations/{x}` | `githubApp.remove` |  |

### org/ai-accounts（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `ORG/ai-accounts` | `machines.aiAccounts` | Codex 连接电脑线 |
| — | POST | `ORG/ai-accounts/refresh` | `machines.refreshAccountUsage` | Codex 连接电脑线 |
| — | PUT | `ORG/ai-accounts/{x}/serves` | `machines.setAccountServes` | Codex 连接电脑线 |
| — | POST | `ORG/ai-accounts/{x}/take-back` | `machines.takeBackPlan` | Codex 连接电脑线 |

### org/webhooks（4 个，要做 4）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/webhooks/github/repository` | `routines.addWebhookToRepo` |  |
| ☐ | GET | `ORG/webhooks/{x}` | `routines.webhookSetup` |  |
| ☐ | PUT | `ORG/webhooks/{x}` | `routines.ensureWebhook` |  |
| ☐ | PUT | `ORG/webhooks/{x}/secret` | `routines.setWebhookSecret` |  |

### org/secret-requests（4 个，要做 4）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/secret-requests/{x}` | `secrets.get` |  |
| ☐ | POST | `ORG/secret-requests/{x}/decline` | `secrets.decline` |  |
| ☐ | POST | `ORG/secret-requests/{x}/deliver` | `secrets.deliver` |  |
| ☐ | POST | `ORG/secret-requests/{x}/vault` | `secrets.saveToVault` |  |

### org/reminders（3 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/reminders` | `reminders.list` | 界面代码里没调用 |
| ☐ | POST | `ORG/reminders` | `reminders.create` | 界面代码里没调用 |
| ☐ | DELETE | `ORG/reminders/{x}` | `reminders.cancel` | 界面代码里没调用 |

### /api/app/me（3 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `/api/app/me/password` | `account.addPassword` |  |
| ☑ | GET | `/api/app/me/unread` | `account.unread` | 按当前账号汇总未静音频道的新消息和已关注话题。所有者另外计入自己的通知；等待数沿用通知收件箱口径。 |
| ☑ | PUT | `/api/app/me/work-area` | `account.setWorkArea` | 引导答案随本人账号保存，只在本人的 /me 中读取，支持跳过 |

### org/deletion（3 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | DELETE | `ORG/deletion` | `orgs.restore` |  |
| ☐ | POST | `ORG/deletion` | `orgs.deleteWorkspace` |  |
| ☐ | POST | `ORG/deletion/code` | `orgs.deletionCode` |  |

### org/team-week（3 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/team-week` | `orgs.teamWeek` |  |
| ☐ | PUT | `ORG/team-week` | `orgs.setTeamWeek` |  |
| ☐ | POST | `ORG/team-week/{x}` | `orgs.actOnTeamWeek` |  |

### org/agent-groups（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/agent-groups` | `agentGroups.create` | 列表读已登记的部门；新建、改、删部门要经提议，回 409 |
| ☑ | DELETE | `ORG/agent-groups/{x}` | `agentGroups.remove` | 同上 |
| ☑ | PATCH | `ORG/agent-groups/{x}` | `agentGroups.update` | 同上 |

### org/imports（3 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/imports` | `imports.sources` | 界面代码里没调用 |
| ☐ | POST | `ORG/imports` | `imports.apply` |  |
| ☐ | POST | `ORG/imports/files` | `imports.copyFile` |  |

### org/blocks（3 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/blocks/{x}/draw` | `artifacts.drawBlock` |  |
| ☐ | GET | `ORG/blocks/{x}/state` | `artifacts.blockState` |  |
| ☐ | PUT | `ORG/blocks/{x}/state` | `artifacts.setBlockState` |  |

### org/proposals（3 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/proposals/{x}/accept` | `proposals.accept` |  |
| ☐ | POST | `ORG/proposals/{x}/ask` | `proposals.ask` |  |
| ☐ | POST | `ORG/proposals/{x}/refuse` | `proposals.refuse` |  |

### org/actions（2 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/actions/{x}/redo` | `actions.redo` | 界面代码里没调用 |
| ☐ | POST | `ORG/actions/{x}/undo` | `actions.undo` | 界面代码里没调用 |

### org/action-cards（2 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/action-cards/{x}` | `actionCards.get` | 界面代码里没调用 |
| ☐ | POST | `ORG/action-cards/{x}/{x}` | `actionCards.decide` | 界面代码里没调用 |

### org/task-manager（2 个，要做 1）

既有 `GET ORG/task-manager` 向已认证所有者显示 `since` 窗口内主聊天明确记录的完成、失败和取消。旧记录缺少终态时不推断。

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/task-manager/continue` | `working.continueTasks` | 暂无跳过预算继续同一回合的能力。 |
| ☑ | GET | `ORG/task-manager/{x}/steps` | `working.taskSteps` | 所有者的任务详情读取主聊天已记录的工具名称、调用开始与结果状态。专员无逐步记录时返回 404。 |

### /api/app/telemetry（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `/api/app/telemetry` | `system.telemetry` | 不做：依赖原产品云端（使用报告发给他们）；404 时界面藏起入口 |
| — | PUT | `/api/app/telemetry` | `system.setTelemetry` | 不做：依赖原产品云端（使用报告发给他们）；404 时界面藏起入口 |

### /api/app/device-accounts（2 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `/api/app/device-accounts` | `account.deviceAccounts` | 只列当前会话的账号和可见空间。第二个会话和未读汇总独立处理。 |
| ☐ | POST | `/api/app/device-accounts/switch` | `account.switchDeviceAccount` |  |

### org/logo（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | DELETE | `ORG/logo` | `orgs.removeLogo` | 所有者可删除，本机工作区恢复默认标识 |
| ☑ | PUT | `ORG/logo` | `orgs.setLogo` | 所有者可上传 ≤768 KiB、最长边 ≤512 像素的 8 位 RGB/RGBA 非隔行 PNG |

### org/team（2 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/team` | `agents.team` | 只读名册，活动沿用自己的会话。团队图的汇报关系只显示双方已可见的连线。 |
| ☐ | POST | `ORG/team/channel-move` | `agents.proposeChannelMove` |  |

### org/team-templates（2 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/team-templates` | `agents.teamTemplates` |  |
| ☐ | POST | `ORG/team-templates/{x}/use` | `agents.applyTeamTemplate` |  |

### org/agent-setup（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/agent-setup` | `agents.newAgentSetup` | 不存上一次的设置，新 agent 用默认模型，回 null |
| ☑ | POST | `ORG/agent-setup` | `agents.applySetup` | 换模型要经提议，回 409 |

### /api/app/github-app（2 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `/api/app/github-app/manifest` | `githubApp.startManifest` |  |
| ☐ | DELETE | `/api/app/github-app/settings` | `githubApp.removeSettings` |  |

### org/github（2 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/github/access` | `githubApp.access` |  |
| ☐ | GET | `ORG/github/repositories` | `githubApp.repositories` |  |

### org/memories（2 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/memories/{x}/revert` | `memory.revert` | 界面代码里没调用 |
| ☑ | POST | `ORG/memories/{x}/team` | `memory.makeTeam` | 只有所有者能把自己的记忆设成团队记忆、能改文字；成员有 `review_memory` 只能移除、恢复。进所有 agent 的系统提示（对外发布的岗位除外），标明是资料不是指令 |

### org/sandbox-settings（2 个，要做 2）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/sandbox-settings` | `sandbox.settings` |  |
| ☐ | PUT | `ORG/sandbox-settings` | `sandbox.saveSettings` |  |

### machines（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `/api/app/machines/device/{x}` | `machines.device` | Codex 连接电脑线 |
| — | POST | `/api/app/machines/device/{x}/deny` | `machines.denyDevice` | Codex 连接电脑线 |

### org/polls（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/polls/{x}` | `artifacts.poll` | 频道里 agent 发的投票：加入频道的人能看（主人 2026-11-22 定） |
| ☑ | PUT | `ORG/polls/{x}/vote` | `artifacts.vote` | 替换自己的选择，空列表是收回；单选只能一项；截止后 409 poll.closed |

### org/choices（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/choices/{x}` | `artifacts.choices` | 频道里 agent 发的选择卡和答案 |
| ☑ | POST | `ORG/choices/{x}/answer` | `artifacts.answerChoices` | 先答的算（之后 409 choices.answered）；答案作为这个人的消息发在话题里，再交给发卡的 agent |

### org/artifacts-trash（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/artifacts-trash` | `artifacts.trash` | 列出所有者从 Drive 删除的助理页面，成员返回空。30 天到期即无法还原，打开回收站时清理到期文件。工作区维护占锁时回 `423 unavailable`。旧工具与旧 API 删除仍直接清除。 |
| ☑ | DELETE | `ORG/artifacts-trash/{x}` | `artifacts.purge` | 只有实际所有者可以清除回收站中的文件和所有历史版本。 |

### org/notification-prefs（2 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/notification-prefs` | `notifications.prefs` | 当前登录人的手机通知状态。仅所有者有已配置的 APNs 目标，按实际投递登记去重。分类投递开关待接。 |
| ☐ | PUT | `ORG/notification-prefs` | `notifications.setPref` |  |

### org/questions（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/questions/{x}/answer` | `questions.answer` | 私聊里交代的事，专员用 `ask_person` 问交代的人；只有被问的人能答，答完按返工路径接着做（最多 3 轮），回答按资料交给专员 |
| ☑ | POST | `ORG/questions/{x}/dismiss` | `questions.dismiss` | 跳过后专员按自己的判断接着做 |

### /api/app/email-undo（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `/api/app/email-undo` | `account.undoEmailChange` |  |

### /api/app/organizations（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `/api/app/organizations` | `orgs.create` |  |

### org/onboarding（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | PATCH | `ORG/onboarding` | `orgs.saveOnboarding` | 随包本机安装的引导使用独立的 `/api/app/desktop-setup`，创建其他工作区的流程仍待接入。 |

### org/billing-link（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | POST | `ORG/billing-link` | `orgs.billingLink` | 不做：没有收费体系（D07 未定） |

### org/limits（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `ORG/limits` | `orgs.limits` | 不做：没有收费体系（D07 未定） |

### org/members（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/members/{x}/removal` | `orgs.removal` | 仅已认证所有者可预览在册成员。移除不转交智能体、不更改模型或暂停例行工作。成员电脑随成员身份失去访问权，预览不列出其私有登记。 |

### org/invitations（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/invitations/{x}/resend` | `orgs.resendInvitation` | 所有者可为有效的一次性邮箱邀请更换链接，保留原到期时间和权限；新链接当次返回，由人转交。 |

### org/audit（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/audit` | `orgs.audit` | 只给所有者。工具调用与确认门决定来自共享审计台账；台账不记成败与审批卡，`outcome`、`approval` 为 null，「等待中」为空 |

### org/permissions（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | PUT | `ORG/permissions/{x}` | `roles.setRow` | 与单组编辑一样限所有者。一个请求原子更新所有人和普通身份组的现有权限项或电脑阶梯。管理员组、其他权限项和办事级别保留。 |

### org/roles（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/roles/{x}/members/preview` | `roles.mailLoss` | 所有者在授予身份组前预查在册成员与授权。工作区无智能体邮箱绑定，预查不写入。 |

### org/agent-lines（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | PUT | `ORG/agent-lines` | `agents.setLines` | 所有者可批量保存团队图中最多三层的汇报关系并撤销，包括名册里的主助理。改动只用于界面展示。 |

### org/autonomy-proposals（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/autonomy-proposals/{x}` | `agents.decideCostly` |  |

### org/agent-imports（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/agent-imports/github` | `agents.importFromGithub` |  |

### org/brain-machines（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `ORG/brain-machines` | `agents.brainMachines` | Codex 连接电脑线 |

### org/refs（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `ORG/refs/{x}` | `chat.idRef` |  |

### org/approvals（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/approvals/{x}/cancel` | `approvals.cancel` |  |

### org/approval-covers（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | DELETE | `ORG/approval-covers/{x}` | `approvals.endCover` | 所有者收回，或被托付的成员自己不再代批 |

### org/right-hand-decisions（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/right-hand-decisions/{x}/undo` | `approvals.undoRightHand` | 没有代批过，一律 404 |

### org/search（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/search` | `search.query` | 团队频道消息（本人已加入的）与任务卡片（本人可见的），按子串找、新的在前。主要聊天、agent 私聊、Wiki、文件还没接；语音、提到、文件、卡片筛选没有对应标记，带上就不返回消息 |

### /api/desktop/latest（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | GET | `/api/desktop/latest` | `machines.desktopLatest` |  |

### org/tasks（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/tasks/{x}/output` | `missions.taskOutput` | 已接入现有任务台账保存的输出，仅所有者可读 |

### org/admin-asks（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/admin-asks` | `notifications.askAdmins` |  |

### org/proposals${filter（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/proposals${filter` | `proposals.list` | 就是已接的 `GET proposals`，带查询参数 |

### org/(空间本身)（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG` | `orgs.get` | 返回调用者在当前空间的摘要、权限及个人显示设置。空间背景仅在已认证的所有者响应中返回。客户端有 `useOrganization` 查询，目前没有页面调用。 |

### org/issues（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/issues/{x}/conversation` | `issues.conversation` | 当前可见范围内的人可打开实际任务对话，首次创建和回复沿用评论权限。支持文字和同一话题的引用，不派活，不进入普通频道列表或搜索。 |
