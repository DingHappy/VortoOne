# 新版 Desktop 后端接口：剩余清单

核对日期 2026-11-22，基于 main（已含 #971–#978）。
客户端合同里一共 631 个不重复接口，后端已接 515 个，剩 **116** 个：
- **要做：45**（其中 6 个在界面代码里没有调用，可以往后排）
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
- vault 里的密钥归谁、怎么撤销

## 清单

### org/missions（40 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/missions` | `missions.create` | 仅所有者在已加入的团队频道话题新建草案。话题归档后新建返回 409，既有草案仍可读取并按原参数重试。 |
| ☑ | GET | `ORG/missions/eligible-agents` | `missions.eligibleAgents` | 仅所有者。原生、能在频道里办事、岗位不写代码的可选；写代码或本机 CLI 的标 `no_computer`，暂停停用的标 `exec_off`。没有电脑可选。 |
| ☑ | GET | `ORG/missions/{x}` | `missions.get` |  |
| ☑ | PATCH | `ORG/missions/{x}` | `missions.setResumeTasks` | 仅所有者，开始以后才能设。开着时服务重启打断的一步自动重做；关着就暂停，在话题里说明。 |
| ☑ | GET | `ORG/missions/{x}/brain` | `missions.brain` | 仅所有者读取草案绑定专员的模型。专员或岗位的登记版本变化、岗位停用时返回 409。只读，不探测模型或启动执行。 |
| ☑ | PUT | `ORG/missions/{x}/brain` | `missions.setBrain` | 如实 409 `mission.brain_fixed`：用 agent 登记的模型，去 agent 设置里改。 |
| ☑ | POST | `ORG/missions/{x}/close` | `missions.close` | 仅所有者。草案只能取消；开始以后可标为完成或取消，正在做的一步一起停下。生成 Wiki 总结、删分支返回 409。 |
| ☑ | PUT | `ORG/missions/{x}/command-approval` | `missions.setCommandApproval` | 只接受 `always`；其它 409 `mission.approval_fixed`，Mission 里不新增免确认。 |
| ☑ | DELETE | `ORG/missions/{x}/command-rules/{x}` | `missions.removeCommandRule` | 没有自动放行规则，404。 |
| ☑ | GET | `ORG/missions/{x}/commands` | `missions.commands` | 原生 agent 只用团队安全工具、不跑命令，回空。写代码的 Mission 等连接电脑线。 |
| ☑ | POST | `ORG/missions/{x}/commands/retry` | `missions.retryCommand` | 如实 409 `mission.no_commands`。 |
| ☑ | POST | `ORG/missions/{x}/commands/{x}/decide` | `missions.decideCommand` | 没有命令卡，404。 |
| ☑ | GET | `ORG/missions/{x}/decisions` | `missions.decisions` | 仅所有者。agent 在一步里用 `mission_decision` 提出，跟着回答发在话题里（消息带 `meta.missionDecision`）；没做完的一步提的不算。 |
| ☑ | POST | `ORG/missions/{x}/decisions/approve-provisional` | `missions.approveProvisional` | 仅所有者，只确认 agent 按建议先做了的（provisional），等人选的不动。 |
| ☑ | POST | `ORG/missions/{x}/decisions/{x}/choose` | `missions.choose` | 仅所有者。阻塞的问题都有答案、没有在做的一步时，agent 接着做，结果写进下一步提示（数据边界内）。已拍板的不能改选。 |
| ☑ | POST | `ORG/missions/{x}/decisions/{x}/opinion` | `missions.opinion` | 仅所有者（参与者在后续 PR），只记倾向、不拍板。 |
| ☑ | GET | `ORG/missions/{x}/deliveries` | `missions.deliveries` | 仅所有者。agent 用 `mission_deliver` 交付，这一步后 Mission 等验收（消息带 `meta.missionDelivery`）。 |
| ☑ | POST | `ORG/missions/{x}/deliveries/{x}/accept` | `missions.acceptDelivery` | 所有者和「决定」成员，Mission 完成。`saveToWiki: true` 把目标、交付、核对清单和验收意见写成「Mission」文件夹里的一页（要有编辑 Wiki 的权限，不然 403、不收尾）；私有频道里的只给频道里的人看。 |
| ☑ | POST | `ORG/missions/{x}/deliveries/{x}/changes` | `missions.requestDeliveryChanges` | 仅所有者，要写意见。Mission 回到进行中，agent 带着意见接着做、重新交付。 |
| ☑ | PUT | `ORG/missions/{x}/deliveries/{x}/checks/{x}` | `missions.checkDelivery` | 仅所有者，每人各勾各的；有结论后不能再勾。 |
| ☑ | PUT | `ORG/missions/{x}/executor` | `missions.setExecutor` | 只有 `agent`（原生 agent 自己做）；Claude、Codex 要连电脑，409 `mission.no_computer`。 |
| ☑ | GET | `ORG/missions/{x}/files` | `missions.files` | 所有者可查看已有草案的空文件列表。草案尚无隔离目录或文件。 |
| ☑ | GET | `ORG/missions/{x}/join-requests` | `missions.joinRequests` | 所有者看全部待处理的，成员只看自己的。 |
| ☑ | POST | `ORG/missions/{x}/join-requests` | `missions.requestJoin` | 能读这个频道、还不在 Mission 里的成员申请；被谢绝后可以再申请。 |
| ☑ | POST | `ORG/missions/{x}/join-requests/{x}/{x}` | `missions.decideJoin` | 仅所有者，accept 时给「决定」或「评论」（默认评论）。「决定」要这个人能在频道里叫这个 agent 办事，否则 409。 |
| ☑ | PUT | `ORG/missions/{x}/machine` | `missions.setMachine` | 如实 409 `mission.no_computer`：Mission 在这个家的服务上跑。 |
| ☑ | POST | `ORG/missions/{x}/messages` | `missions.message` | 仅所有者，在 Mission 话题里发一条。进行中且没有正在做的一步时马上派下一步（`delivered: true`），否则下一步读到。 |
| ☑ | GET | `ORG/missions/{x}/participants` | `missions.participants` |  |
| ☑ | POST | `ORG/missions/{x}/participants` | `missions.json` | 仅所有者直接加人，这个人要在频道里；「决定」的条件同上。 |
| ☑ | DELETE | `ORG/missions/{x}/participants/{x}` | `missions.removeParticipant` | 仅所有者，移出后身份立刻失效。 |
| ☑ | PATCH | `ORG/missions/{x}/participants/{x}` | `missions.setParticipant` | 仅所有者改身份；「决定」的条件同上。 |
| ☑ | GET | `ORG/missions/{x}/plan` | `missions.plan` |  |
| ☑ | GET | `ORG/missions/{x}/reviews` | `missions.reviews` | 代码审查只来自写代码的 Mission，回空。 |
| ☑ | POST | `ORG/missions/{x}/reviews/{x}/{x}` | `missions.review` | 没有审查，404。 |
| ☑ | GET | `ORG/missions/{x}/settings` | `missions.settings` | 仅所有者读取已有草案；审批策略固定，草案没有规则或执行统计。 |
| ☑ | POST | `ORG/missions/{x}/suggestions/{x}/{x}` | `missions.suggestion` | 没有拍板权的人在 Mission 话题里说的话记成建议（`meta.missionSuggestion`），采纳前不进 agent 提示。所有者或「决定」成员采纳或谢绝，采纳后交给 agent。 |
| ☑ | GET | `ORG/missions/{x}/summary` | `missions.summary` |  |
| ☑ | POST | `ORG/missions/{x}/transfer` | `missions.transfer` | 如实 409 `mission.transfer_unavailable`：每一步以所有者的身份派活，Mission 只能归所有者；可以把对方设成「决定」成员。 |
| ☑ | POST | `ORG/missions/{x}/worktree/retry` | `missions.retryWorktree` | 没有项目副本，409 `mission.no_worktree`。 |
| ☑ | POST | `ORG/missions/{x}/{x}` | `missions.control` | 仅所有者，`start/pause/resume/stop`。绑定的 agent 在话题里一步接一步做，只用团队安全工具、按外部内容处理话题；失败、被打断、没列计划、一次累计 10 步时暂停并说明，计划做完等收尾。agent 要先加进频道。 |

### org/agents（35 个，要做 13）

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
| ☑ | GET | `ORG/agents/{x}/for-you` | `agentForYou.get` | agent 主动替人整理的东西还没有：空。 |
| ☑ | PUT | `ORG/agents/{x}/group` | `agentGroups.move` | 分组就是部门，调整要经提议，回 409 |
| ☑ | DELETE | `ORG/agents/{x}/holds/{x}` | `desktop.stopHold` | agent 在连接的电脑上开着的程序：这里没有，404（等连接电脑线）。 |
| ☑ | GET | `ORG/agents/{x}/learned-skills` | `skills.learned` | agent 不自己写技能，是空的 |
| ☑ | GET | `ORG/agents/{x}/mail` | `mail.agent` | 这个 agent 在每个团队邮箱上的级别和规则（没授权是 none）。只给已认证的所有者；主助理用自己的邮件插件，404。 |
| ☑ | PUT | `ORG/agents/{x}/mail/{x}` | `mail.setAgent` | read：搜索、读信、看文件夹；read_write 另外能放草稿（每次都要人确认）。规则里的文件夹、发件人、最近几天在工具里执行。发信的规则（收件人、不问就发）422 `mail.send_unavailable`，删信 422 `mail.delete_unavailable`。只在所有者自己发起的事里给 agent 这些工具（主人 2026-11-24 定）。 |
| ☑ | GET | `ORG/agents/{x}/merge-repositories` | `repositories.merge` | 不经审批就合并的仓库：没有（空）。合并一律要人确认。 |
| ☑ | PUT | `ORG/agents/{x}/merge-repositories` | `repositories.setMerge` | 只接受空列表，否则 409 `repositories.merge_needs_approval`：不开免审批合并。 |
| ☑ | PUT | `ORG/agents/{x}/mission-command-approval` | `missions.setAgentDefault` | 只接受 `always`，和 Mission 自己的设置一致；其它 409。 |
| — | PUT | `ORG/agents/{x}/repositories` | `repositories.set` | 不做：改它等于改岗位授权，岗位改动要走提议核对，新界面没有核对页 |
| — | PUT | `ORG/agents/{x}/scopes` | `scopes.set` | 不做：改它等于改岗位授权，岗位改动要走提议核对，新界面没有核对页；界面代码里没调用 |
| ☐ | GET | `ORG/agents/{x}/secrets` | `secrets.list` |  |
| ☐ | DELETE | `ORG/agents/{x}/secrets/{x}${machineId ` | `secrets.remove` |  |
| ☑ | GET | `ORG/agents/{x}/usage` | `agents.planUsage` | 仅所有者。这个 agent 这个月的 token 和折算的美元（只有 API 一个来源）。 |
| ☑ | GET | `ORG/agents/{x}/usage-queue` | `agents.usageQueue` | 仅所有者。在等额度（专员额度用完）排着的事。 |
| ☑ | POST | `ORG/agents/{x}/usage-queue/cancel` | `agents.cancelUsageQueue` | 仅所有者。排着的全部停下，走主人的停止决定。 |
| ☐ | GET | `ORG/agents/{x}/{x}/control` | `browser.agentControl` | 读已接、写没接 |

### org/wiki（24 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | PUT | `ORG/wiki/bulk/agents` | `wiki.setAgents` | 有编辑权的人选哪些 agent 能读这几页（null 每个 agent，[] 都不能，默认 []）。只有对全体成员公开的页面能开放（`wiki.agents_need_everyone`）；之后收窄可见范围，agent 就读不到了。看不到的页面和不存在的答得一样。派活的 agent 用 `wiki_search` / `wiki_read` 读，结果按外部内容（主人 2026-11-24 定）。主助理读不到 Wiki。 |
| ☑ | POST | `ORG/wiki/bulk/delete` | `wiki.removeMany` |  |
| ☑ | GET | `ORG/wiki/folder-audience` | `wiki.folderAudience` | 仅所有者读取当前文件夹和最近有规则的上级。成员名单叠加祖先限制，只显示当前在册成员。 |
| ☑ | PUT | `ORG/wiki/folder-audience` | `wiki.setFolderAudience` | 仅所有者按在册成员收紧，页面与各级文件夹规则取交集。只有所有者能移动页面并收紧可见范围，移动不能扩大可见范围。角色、编辑权限和智能体授权返回 409。 |
| ☑ | GET | `ORG/wiki/graph` | `wiki.graph` | 节点与链接两端均按当前查看者的页面权限筛选 |
| ☑ | POST | `ORG/wiki/import` | `wiki.import` | 导入 Markdown、Obsidian 或所有者的 GitHub 笔记，保存来源、目录、标签、正文和别名。所有者可绑定本空间私有 Drive 文件，成员看不到绑定，文件回收后隐藏。附件原件在 Drive 追加版本后仍可读取。重发相同导入号及批次内容复用回执。 |
| ☑ | GET | `ORG/wiki/import/github` | `wiki.githubNotes` | 所有者预览公开 GitHub 仓库、分支目录或单个 Markdown 文件。仅返回笔记 ZIP，保存在后续导入确认时执行。私有仓库及附件暂不可用。 |
| ☑ | POST | `ORG/wiki/pages` | `wiki.create` | 所有者和有 Wiki 编辑权限的成员可以创建页面，继续遵守文件夹的可见规则。 |
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
| ☑ | GET | `ORG/wiki/reads` | `wiki.reads` | 供 Wiki 首页显示近 7 天的智能体阅读排行。Wiki 尚未开放智能体读取，因此排行为空；成员和所有者查看页面不计入。 |
| ☑ | GET | `ORG/wiki/search` | `wiki.search` | 搜索标题和导入别名，只返回可见、未删除的页面。别名也用于关系图和反向链接。Desktop 的 Wiki 侧栏搜索、正文链接及导入预检识别别名，多个页面共用的别名不会自动选页。 |
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

### org/vault（12 个，要做 4）

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
| ☑ | POST | `ORG/vault/items/{x}/move` | `vault.move` | 保险库没有文件夹：409。 |
| ☑ | POST | `ORG/vault/keys` | `vault.importKeys` | 「导入 .env」仅本人批量新增或显式替换，返回统计 |

### org/me（12 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | PATCH | `ORG/me` | `orgs.updateMe` | 本人修改空间内用户名和个人时区。时区清空后跟随空间默认；用户名按大小写无关的提及规则去重，成员与智能体共用命名空间，历史用户名仍留给本人。每人最多登记 16 个不同的自选用户名。 |
| ☑ | PUT | `ORG/me/activity` | `orgs.setShowActivity` |  |
| ☑ | PUT | `ORG/me/agent-style` | `orgs.setAgentStyle` | 还不能按个人改 agent 说话的方式：409，口吻在 agent 自己的设置里。 |
| ☑ | GET | `ORG/me/approval-cover` | `approvals.covers` | 所有者托付一个成员替自己批主要聊天里的确认：一直或外出时，可设到期；默认只代批低风险的。读过外部内容的、网页登录、文件传输、设备钥匙、专员提议永远留给所有者；每次代批进审计 |
| ☑ | PUT | `ORG/me/approval-cover` | `approvals.setCover` | 只有所有者能设；成员回 409 |
| ☑ | DELETE | `ORG/me/avatar` | `orgs.removeAvatar` | 仅移除本人头像。 |
| ☑ | PUT | `ORG/me/avatar` | `orgs.setAvatar` | 本人上传正方形 PNG 或 JPEG，支持头像绘制配方。仅当前空间的在册成员能读取，安全拒绝其他格式。 |
| ☑ | PUT | `ORG/me/chat` | `orgs.setChatPrefs` |  |
| ☑ | GET | `ORG/me/right-hand-approvals` | `approvals.rightHand` | 如实是关着的：会提议的只有助理自己，代批等于自己批自己的提议；打开回 409。等专员也能提议再接 |
| ☑ | PUT | `ORG/me/right-hand-approvals` | `approvals.setRightHand` | 只接受保持关着，要打开回 409 |
| ☑ | PUT | `ORG/me/start` | `orgs.setStart` |  |
| ☑ | PUT | `ORG/me/status` | `orgs.setStatus` | 状态按人保存，隐身只对本人可见；跨端更新需重新读取列表 |

工作区最多保留 8,016 条自选用户名登记，包含已离开的成员。已登记的本人用户名和默认用户名仍可选用。保存个人资料与登记智能体共用进程内事务锁，同一个工作区由持有运行租约的一个后端进程提供服务。

### org/artifacts（11 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/artifacts` | `artifacts.create` | 所有者可通过 JSON 创建私有文档、表格和 HTML，与 Drive 上传使用同一私有存储。其它种类回 415，附到频道回 422。 |
| ☑ | POST | `ORG/artifacts/files` | `artifacts.upload` | 所有者可私有上传。UTF-8 的 HTML、TXT、Markdown、CSV、JSON 和静态 PNG、JPEG 照旧可预览（文本可追加版本）；别的格式（PDF、Office 文档、压缩包……）原样保存，只能按附件下载，不预览、不改，记下的类型不会是文本、图片或网页（接 Codex 半成品）。挂到频道或话题如实 422。 |
| ☑ | GET | `ORG/artifacts/{x}/download` | `artifacts.download` | 仅所有者可下载 HTML、已有历史版本和私有文件原件。 |
| ☑ | GET | `ORG/artifacts/{x}/file` | `artifacts.fileBlob` | 仅所有者可读取 HTML、历史版本和私有文件原件。私有文件核对存储的 SHA-256。 |
| ☑ | POST | `ORG/artifacts/{x}/files` | `artifacts.uploadVersion` | 所有者可为私有 TXT、Markdown、CSV、JSON 追加同格式文件及可选备注。原版本可单独预览或下载，回收和还原保留历史。其它制品或格式返回 415。 |
| ☑ | POST | `ORG/artifacts/{x}/keep` | `artifacts.keep` | 频道卡片跟着话题保存，不另存进 Drive：如实 409。 |
| ☑ | POST | `ORG/artifacts/{x}/reply` | `artifacts.reply` | 频道里 agent 发的投票、选择卡上回复：作为这个人在话题里的一条消息交给发卡的 agent 接着做（要能发言、能叫 agent）。别的制品 404。 |
| ☑ | POST | `ORG/artifacts/{x}/restore` | `artifacts.restore` | 所有者可还原 Drive 删除的助理页面、已存历史版本和私有上传；到期回 `409 artifact.purged`，提前永久清除回 `410`。 |
| ☑ | PUT | `ORG/artifacts/{x}/sharing` | `artifacts.setSharing` | 制品还不能另外共享（卡片跟着话题的人，Drive 里的只给所有者）：如实 409。 |
| ☑ | GET | `ORG/artifacts/{x}/versions` | `artifacts.versions` | 仅已认证所有者。倒序列出可打开的版本。私有文档、表格和文本版本显示已保存的备注，旧网页不推断逐版作者或备注。 |
| ☑ | POST | `ORG/artifacts/{x}/versions` | `artifacts.update` | 经认证的所有者可给私有文档和表格添加版本及备注。对照原版本编号，过时返回 409。旧版本可查看、下载；资料不共享到频道。 |

### org/threads（10 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/threads` | `threads.list` |  |
| ☑ | POST | `ORG/threads` | `threads.create` | 已加入且可发言的团队频道成员可从同一频道的原消息选段创建话题。只引用，不移动原消息；跨频道、私聊和任务对话拒绝。当前 Desktop 的消息菜单未提供创建选段话题入口。 |
| ☑ | DELETE | `ORG/threads/{x}` | `threads.remove` | 有管理权限的人可撤销未归档且尚无回复、引用、标记和任务草案的选段话题。只隐藏生成的标记，保留原消息和事件顺序。Desktop 仅在新建选段后的提示中接入撤销，当前无可触发的创建入口。 |
| ☑ | PATCH | `ORG/threads/{x}` | `threads.update` | 已接团队话题改名。选段型话题可在原频道调整范围和排除项，写入要求原有管理权限。General 原生验收已从接口预置的话题排除一条消息并保存，频道中的三条原消息仍在。 |
| ☑ | DELETE | `ORG/threads/{x}/follow` | `threadFollows.unfollow` | 取消自己在已加入频道中的话题关注，保留已读位置。当前界面未接入口。 |
| ☑ | PUT | `ORG/threads/{x}/follow` | `threadFollows.follow` | 关注自己能读的团队频道话题并计入个人未读。归档话题始终不计未读。当前界面未接入口。 |
| ☑ | GET | `ORG/threads/{x}/mission` | `missions.ofThread` |  |
| ☑ | POST | `ORG/threads/{x}/read` | `reads.markThreadRead` | Desktop 打开话题时调用。已接团队频道话题的个人已读位置及频道、工作区两处列表的未读状态。侧栏只计有权限、已关注且未归档的话题。 |
| ☑ | GET | `ORG/threads/{x}/tasks` | `missions.threadTasks` | 所有者读取已绑定草案的空执行列表。草案不创建执行会话。 |
| ☑ | POST | `ORG/threads/{x}/{x}` | `threads.act` | 关闭、重新打开、归档、解除归档；选段话题可「拿出频道」（原消息改挂到话题下，频道主时间线里看不到）和「放回频道」（只放回那几条，后来的回复不动）。拿出期间不能调整选段或撤销，归档的先解除归档（接 Codex 半成品）。 |

### org/channels（10 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/channels/{x}/consultations` | `agents.consultations` | agent 之间互相咨询还没有：能读这个频道的人拿到空列表。 |
| ☑ | GET | `ORG/channels/{x}/consultations/{x}` | `agents.consultation` | 同上，404。 |
| ☑ | POST | `ORG/channels/{x}/delete` | `chat.deleteChannel` | 仅所有者，要打一遍频道名。频道和消息、话题、话题上的 Mission、投票卡、已读、静音一起永久删除；频道里有 agent 在做事时 409；删前写持久审计（被拒的不记）。管理员暂不能删。 |
| ☑ | GET | `ORG/channels/{x}/follow-ups` | `chat.followUps` | agent 约好回来跟进还没有：能读这个频道的人拿到空列表。 |
| ☑ | DELETE | `ORG/channels/{x}/members/agents/{x}` | `chat.removeMember` | 所有者把 agent 拿出频道（删掉的 agent 也能拿出来） |
| ☑ | PATCH | `ORG/channels/{x}/members/agents/{x}` | `chat.setAutoReply` | 只认「被 @ 才回答」；开不 @ 也回答第一期如实回 409（主人 2026-11-22 定） |
| ☑ | DELETE | `ORG/channels/{x}/mute` | `reads.mute` | 解除自己已加入的团队频道静音，保留未读进度。私聊和其他非团队频道不支持。 |
| ☑ | PUT | `ORG/channels/{x}/mute` | `reads.mute` | 静音自己已加入的团队频道，频道列表隐藏普通未读提示。私聊和其他非团队频道不支持。 |
| ☑ | PUT | `ORG/channels/{x}/place` | `chat.place` |  |
| ☑ | GET | `ORG/channels/{x}/reads` | `dms.reads` | 仅两名当前在册参与人可读，返回对方已确认的持久游标，并向其私聊订阅推送 `channel.read`。 |

### org/usage（10 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/usage/composio` | `connectors.composioUsage` | 这里没接 Composio：0 次、不限。 |
| ☑ | GET | `ORG/usage/limits` | `orgs.personLimits` | 仅所有者。默认、身份组、个人的每月美元上限，到了上限 block（拒绝再派活）或 notify。 |
| ☑ | PUT | `ORG/usage/limits` | `orgs.setPersonLimits` | 仅所有者。成员到了 block 上限时，再让 agent 干活会被拒绝并说明（派活入口统一核对）。 |
| ☑ | POST | `ORG/usage/limits/request` | `orgs.requestMoreUsage` | 成员申请这个月加额度。 |
| ☑ | GET | `ORG/usage/people` | `orgs.peopleUsage` | 仅所有者。每个人这个月：成员交给 agent 的记在成员名下，其余记在所有者名下；带上限状态。 |
| ☑ | GET | `ORG/usage/people/{x}` | `orgs.personUsage` | 所有者看任何人（带金额）；成员只看自己（`money: false`，占上限的百分比）。 |
| ☑ | POST | `ORG/usage/people/{x}/raise` | `orgs.raisePersonLimit` | 仅所有者，把这个成员的上限调到给的数（标为「调高过」）。 |
| ☑ | POST | `ORG/usage/people/{x}/refuse` | `orgs.refuseMoreUsage` | 仅所有者，拒绝加额度的申请。 |
| ☑ | GET | `ORG/usage/plans` | `orgs.planUsage` | 仅所有者。这里没有订阅套餐账号（accounts 空），只有 API 用量。 |
| ☑ | GET | `ORG/usage/report` | `orgs.usageReport` | 仅所有者。从 since 起按 agent、按种类（agent/summary）。按所有者给模型填的价格折成美元（没填价格的只记 token，不算钱，`partialCalls` 标出）。agent 没有美元预算（额度按 token 记），预算填 0。 |

### org/routines（10 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/routines/{x}/continue` | `routines.continueUsage` | 不跳过额度接着做：409，调高额度后它会自己接着做。 |
| ☑ | POST | `ORG/routines/{x}/duplicate` | `routines.duplicate` | 所有者可复制现有本人例行定义；先按摘要审核，副本默认暂停 |
| ☑ | GET | `ORG/routines/{x}/hook` | `routines.hook` | webhook 触发的例行工作：触发地址 `/api/routine-hooks/<随机串>`（不认登录）、是否要签名、允许的方法、最近 20 次调用（只记结果、状态码、大小、进了哪次运行，不存内容）。只给已认证的所有者；不是 webhook 触发的 404。 |
| ☑ | POST | `ORG/routines/{x}/hook/address` | `routines.newHookAddress` | 换一个新地址，旧地址立刻 404。 |
| ☑ | DELETE | `ORG/routines/{x}/hook/secret` | `routines.removeHookSecret` | 去掉签名密钥，之后只凭地址。 |
| ☑ | POST | `ORG/routines/{x}/hook/secret` | `routines.newHookSecret` | 新的签名密钥只显示这一次。之后每次调用要带 `X-VortoOne-Signature: sha256=<HMAC-SHA256>`（POST 等签请求体，GET 签查询串），不对 401 `bad_signature`。 |
| ☑ | POST | `ORG/routines/{x}/hook/test` | `routines.testHook` | 用一份样例内容叫一次，agent 被告知这是测试；暂停着的也能试。 |
| ☑ | POST | `ORG/routines/{x}/run` | `routines.runNow` | 立即运行一次，不改排期和启停；在跑、等验收、等核对时回 409 说明 |
| ☑ | GET | `ORG/routines/{x}/runs` | `routines.runs` | 所有者可读取任务台账中该例行工作最近 30 条运行记录。列表显示最近 14 条，健康统计只计最近七天的已结束运行（取消除外）。 |
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

### org/skills（9 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/skills/import` | `skills.import` | 所有者可导入只含单个 SKILL.md 的个人 ZIP，需审阅后启用；链接和附件待接 |
| ☑ | POST | `ORG/skills/{x}/reimport` | `skills.reimport` | 技能是手写或上传 ZIP 的，没有网址来源：如实 409 `skill.no_source`，不改技能，也不把这一版记成看过。 |
| ☑ | POST | `ORG/skills/{x}/restore` | `skills.restore` | 没有因不用被归档的技能：409 `skill.not_archived`。 |
| ☑ | POST | `ORG/skills/{x}/revert` | `skills.revert` | 「agent 自己改进技能」固定关着，没有 agent 的改动可撤回：409 `skill.nothing_to_revert`。 |
| ☑ | POST | `ORG/skills/{x}/update` | `skills.updateFromSource` | 技能是手写或上传 ZIP 的，没有网址来源：如实 409 `skill.no_source`，不改技能，也不把这一版记成看过。 |
| ☑ | POST | `ORG/skills/{x}/update/approve` | `skills.approveUpdate` | 技能是手写或上传 ZIP 的，没有网址来源：如实 409 `skill.no_source`，不改技能，也不把这一版记成看过。 |
| ☑ | POST | `ORG/skills/{x}/update/dismiss` | `skills.dismissUpdate` | 技能是手写或上传 ZIP 的，没有网址来源：如实 409 `skill.no_source`，不改技能，也不把这一版记成看过。 |
| ☑ | POST | `ORG/skills/{x}/update/reopen` | `skills.reopenUpdate` | 技能是手写或上传 ZIP 的，没有网址来源：如实 409 `skill.no_source`，不改技能，也不把这一版记成看过。 |
| ☑ | POST | `ORG/skills/{x}/update/undo` | `skills.undoUpdate` | 技能是手写或上传 ZIP 的，没有网址来源：如实 409 `skill.no_source`，不改技能，也不把这一版记成看过。 |

### org/messages（8 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/messages/{x}/actions/undo` | `actions.undoMessage` | agent 的回答下面没有操作回执：回空（没有要撤的）。 |
| ☑ | POST | `ORG/messages/{x}/answer-now` | `chat.answerNow` | 没接模型时消息不会发出、不会存着等回答：409 `message.nothing_waiting`。 |
| ☑ | POST | `ORG/messages/{x}/cancel-follow-up` | `chat.cancelFollowUp` | 没有跟进：409 `follow_up.not_pending`。 |
| ☑ | POST | `ORG/messages/{x}/correction` | `chat.correct` | 纠正还不能存成 agent 的经验：409 `message.correction_unavailable`，不存。 |
| ☑ | POST | `ORG/messages/{x}/github-answer` | `chat.answerGitHub` | 没接 GitHub 事件：409 `github_event.unavailable`。 |
| ☑ | DELETE | `ORG/messages/{x}/reactions` | `chat.unreact` |  |
| ☑ | GET | `ORG/messages/{x}/reactions` | `chat.reactions` |  |
| ☑ | POST | `ORG/messages/{x}/reactions` | `chat.react` |  |

### org/mail（7 个，要做 0）

已定：邮件只读，保存草稿须用户确认，由用户在自己的邮件客户端发送。
现有本机 IMAP 插件已支持这条流程。它使用操作系统用户的凭据，组织内的邮箱账户和授权仍待接入。
组织连接目录目前明确不提供新增邮箱的入口。

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/mail/accounts/composio` | `mail.connectComposio` | 这里没有 Gmail / Outlook 的一键连接：409 `mail.composio_unavailable`。 |
| ☑ | POST | `ORG/mail/accounts/imap` | `mail.connectImap` | 所有者接团队 IMAP 邮箱（预设 iCloud、QQ、163、126、阿里、Yahoo，或自填服务器）：先真登录一次、打开收件箱，连不上 422 带 `auth` / `imap_unreachable` / `tls` / `blocked_host`，不留记录。授权码只存在服务器上，从不回给界面。 |
| ☑ | DELETE | `ORG/mail/accounts/{x}` | `mail.disconnect` | 断开邮箱，授权码和给 agent 的授权一起删掉。 |
| ☑ | PATCH | `ORG/mail/accounts/{x}` | `mail.update` | 名字、动作开关、别名、默认发件地址；换授权码先测，通过了才换（恢复「要重新连接」）。 |
| ☑ | GET | `ORG/mail/accounts/{x}/folders` | `mail.folders` | IMAP 的文件夹（名字按 UTF-7 解开，角色按服务器标记）。登录失败就把邮箱标成要重新连接。 |
| ☑ | GET | `ORG/mail/accounts/{x}/suggested-addresses` | `mail.suggestedAddresses` | 已发送文件夹最近 200 封用过的发件地址（不含主地址和已有别名），只读打开。 |
| ☑ | GET | `ORG/mail/providers` | `mail.providers` | 所有者：IMAP 可用、给预设；Composio 没有。成员：都不可用。 |

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

### /api/app/invitations（7 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `/api/app/invitations/{x}/accept` | `orgs.acceptInvitation` | 已接密码账号接受指定邮箱的邀请。可选的 `mail` 查询参数不参与当前服务的认证或邮箱确认 |
| ☑ | POST | `/api/app/invitations/{x}/code` | `invites.sendCode` | 邮箱验证码是开关（主人 2026-11-23 定），默认关：`VORTOONE_JOIN_EMAIL_CODES=1` 且配好 `VORTOONE_SMTP_URL`、`VORTOONE_MAIL_FROM` 才发；关着如实 409 `join.codes_off`。60 秒一封、每小时 5 封、全服务 10 分钟 30 封。 |
| ☑ | POST | `/api/app/invitations/{x}/code/check` | `invites.checkCode` | 6 位、10 分钟有效、输错 5 次作废；对了这个邮箱在这条链接上 30 分钟内算证实。 |
| ☑ | POST | `/api/app/invitations/{x}/request-new` | `invites.requestNew` | 这里没法替访客转告邀请人：如实 409 `join.ask_inviter`。 |
| ☑ | POST | `/api/app/invitations/{x}/requests` | `invites.ask` | 不登录就能申请（同源），进门要所有者批准。没证实邮箱的批准后生成写着他邮箱的邀请由所有者转交；开着验证码且证实了、设了密码的批准后直接建账号（只存密码摘要）。已经是成员的邮箱也照样回「等着」。 |
| ☑ | POST | `/api/app/invitations/{x}/requests/claim` | `invites.claim` | 凭申请时拿到的 key 看结果；直接加入的换一次性登录。 |
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

### org/calendar（6 个，要做 0）

日历查询包含所有者已批准且可用的例行工作的未来排期。时区、夏令时和一次性排期沿用调度器的规则。
仍有执行在途、结果待核对或接手后待恢复的例行工作，等当前状态允许调度才显示未来排期。
每次最多返回按时间排序的 4,096 条排期，超过时返回 `routinesTruncated: true`。已执行记录仍在例行工作的运行历史里。
成员只读自己的日程和已有权限可见的到期任务。

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/calendar/events` | `calendar.create` | 仅本人可见的本地单次日程及每日、每周重复，按本地时区保留时刻，支持按日期结束。高级规则、按次数结束、共享或外部邀请暂不可用。 |
| ☑ | DELETE | `ORG/calendar/events/{x}` | `calendar.remove` | 按界面的「仅本次」「此后」「整组」移除自己的日程。后两种范围包含各自被单独编辑的例外。 |
| ☑ | PATCH | `ORG/calendar/events/{x}` | `calendar.update` | 可编辑自己的日程和重复范围。单次例外保留各自的编辑。有例外时，批量更改时间或规则明确拒绝；修改整组时间或规则从首次日程操作。 |
| ☑ | POST | `ORG/calendar/events/{x}/rsvp` | `calendar.respond` | 日历只有自己的日程，没有邀请：404。 |
| ☑ | POST | `ORG/calendar/proposals/{x}/confirm` | `calendar.confirmProposal` | agent 还不会提日程提议：404。 |
| ☑ | POST | `ORG/calendar/proposals/{x}/dismiss` | `calendar.dismissProposal` | 同上，404。 |

### org/browser（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `ORG/browser/handoffs/{x}` | `browser.handoff` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/cancel` | `browser.cancel` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/control` | `browser.takeControl` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/done` | `browser.done` | Codex 连接电脑线 |
| — | POST | `ORG/browser/handoffs/{x}/failed` | `browser.failed` | Codex 连接电脑线 |

### /api/app/account（5 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | DELETE | `/api/app/account` | `account.delete` | 成员注销自己的账号：发信开着要邮件验证码（`reauth.code*`），没开要自己的密码（`reauth.password`）。所有登录立刻结束，7 天后移出这个家、删掉账号（每小时检查一次到期的）。所有者是唯一的所有者：409 `sole_owner`。 |
| ☑ | DELETE | `/api/app/account/deletion` | `account.restore` | 7 天内重新登录后可以恢复；到期了 409 `account.not_restorable`。注销中的会话只能看自己、看注销计划、恢复。 |
| ☑ | GET | `/api/app/account/deletion` | `account.deletionPlan` | 成员：离开这个家（`leaves`），别的都为空；所有者：`soleOwner` 是这个家、`serverOwner: true`。 |
| ☑ | POST | `/api/app/account/deletion/code` | `account.deletionCode` | 发信开着时把验证码发到自己的邮箱（和邀请验证码共用频率限制，`reauth.code_wait` 带 `retryIn`）；没开 409 `reauth.no_email`。 |
| ☐ | POST | `/api/app/account/email` | `account.changeEmail` |  |

### org/agent-proposals（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/agent-proposals/{x}` | `agents.proposal` | 助理在主要聊天里提的新建 agent 以卡片出现在对话里（和审批列表是同一份提议）；只有所有者能看 |
| ☑ | POST | `ORG/agent-proposals/{x}/ask` | `agents.askProposal` | 只有所有者能决定，回 409 |
| ☑ | POST | `ORG/agent-proposals/{x}/create` | `agents.createProposed` | 按卡片上这一版批准，登记处照常核对 |
| ☑ | POST | `ORG/agent-proposals/{x}/deny` | `agents.denyProposal` | 拒绝 |
| ☑ | POST | `ORG/agent-proposals/{x}/routine` | `agents.proposalRoutine` | 提议不附带例行工作，回 409 |

### org/invite-links（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/invite-links` | `invites.links` | 仅所有者。开着的链接，带地址、名额、已用、等着处理的申请数。 |
| ☑ | POST | `ORG/invite-links` | `invites.createLink` | 仅所有者。不带选项是这个家自己的链接（续成 30 天）；带身份组、名额（1/5/10/25/50/100）、有效期另开一条。批准后直接进频道、配 agent 还不支持（400）。 |
| ☑ | DELETE | `ORG/invite-links/{x}` | `invites.turnOff` | 关掉；经它来的、没处理的申请一起关。 |
| ☑ | PATCH | `ORG/invite-links/{x}` | `invites.editLink` | 改给的身份组、名额、有效期，地址不变。 |
| ☑ | POST | `ORG/invite-links/{x}/renew` | `invites.renewLink` | 再给 30 天。 |

### org/storage（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/storage` | `storage.get` | 显示当前服务器磁盘状态，仅所有者可读 |
| ☑ | PUT | `ORG/storage` | `storage.save` | 存储不接 S3（主人 2026-11-24 定）：文件存在服务器磁盘，如实 409 `storage.s3_unavailable`。 |
| ☑ | POST | `ORG/storage/migrate` | `storage.migrate` | 同上，409 `storage.s3_unavailable`。 |
| ☑ | GET | `ORG/storage/migration` | `storage.migration` | 本地磁盘无迁移任务时返回 null，仅所有者可读 |
| ☑ | POST | `ORG/storage/test` | `storage.test` | 同上，409 `storage.s3_unavailable`。 |

### org/notifications（5 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/notifications/done-all` | `notifications.doneAll` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/read-all` | `notifications.readAll` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/{x}/done` | `notifications.done` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/{x}/read` | `notifications.read` | 仅所有者的后台通知 |
| ☑ | POST | `ORG/notifications/{x}/snooze` | `notifications.snooze` | 仅所有者的后台通知 |

### /api/app/feedback（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `/api/app/feedback` | `system.sendFeedback` | 已认证成员或所有者提交本地文字反馈，另有仅所有者可读的本地收件箱 |
| ☑ | POST | `/api/app/feedback/files` | `system.uploadFeedbackFile` | 反馈暂时只收文字：409。 |
| ☑ | DELETE | `/api/app/feedback/files/{x}` | `system.dropFeedbackFile` | 没有附件：404。 |
| ☑ | POST | `/api/app/feedback/offers/{x}` | `system.decideFeedbackOffer` | agent 主动提出的反馈报告还没有：404。 |

### /api/app/jev（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | DELETE | `/api/app/jev` | `system.removeJev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |
| — | GET | `/api/app/jev` | `system.jev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |
| — | PUT | `/api/app/jev` | `system.saveJev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |
| — | POST | `/api/app/jev/check` | `system.checkJev` | 不做：第三方 TypeSafe 服务，接口不公开；404 时界面藏起入口 |

### /api/app/push（4 个，要做 3）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `/api/app/push/devices` | `push.register` |  |
| ☐ | POST | `/api/app/push/devices/forget` | `push.unregister` |  |
| ☐ | POST | `/api/app/push/stale` | `push.stale` |  |
| ☑ | GET | `/api/app/push/web` | `push.webKey` | 没有网页推送：按客户端约定回 404，界面据此隐藏入口（不另加路由）。 |

### org/team-proposals（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | PATCH | `ORG/team-proposals/{x}/agents/{x}` | `agents.adjustTeamAgent` | 批的是卡片上这一版，要改请助理重新提，回 409 |
| ☑ | POST | `ORG/team-proposals/{x}/create` | `agents.createTeam` | 助理提的开通单（新 agent 连同岗位、例行工作）以团队卡片出现在主要聊天里；按这一版批准整张开通单 |
| ☑ | POST | `ORG/team-proposals/{x}/deny` | `agents.denyTeam` | 拒绝 |
| ☑ | POST | `ORG/team-proposals/{x}/undo` | `agents.undoTeam` | 登记了不能一键撤回，回 409 |

### org/runs（4 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/runs/{x}` | `agents.run` | 与列表同一行；没有逐步记录，`steps` 为空（进度尾部在主人可看的任务输出里） |
| — | POST | `ORG/runs/{x}/continue` | `agents.continueRun` | 不做（2026-11-22 主人定）：停下的事重新交代 |
| ☑ | POST | `ORG/runs/{x}/device-wait` | `devices.deviceWait` | 这里没有在等设备的事：404。 |
| ☑ | POST | `ORG/runs/{x}/stop-reason` | `agents.stopReason` | 所有者停下 agent 手上的事后说一句为什么（只能一次）：记在任务上，这个人下次再找这个 agent 时它在「之前的对话」里读得到。 |

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
| ☐ | GET | `ORG/github-app` | `githubApp.info` | Settings 已能显示 App 未配置。仅已认证所有者看到服务器设置状态。安装绑定和可用状态仍待接，见 [接入草案](DESKTOP_GITHUB_APP.md)。 |
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

### org/reminders（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/reminders` | `reminders.list` | 单独的提醒还没有存储：空列表。 |
| ☑ | POST | `ORG/reminders` | `reminders.create` | 如实 409：请在日历里建日程或排例行工作。 |
| ☑ | DELETE | `ORG/reminders/{x}` | `reminders.cancel` | 404。 |

### /api/app/me（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `/api/app/me/password` | `account.addPassword` | 成员可凭 10 分钟内的一次密码登录更换本人的密码并撤销其它会话。再次更换需用新密码登录。所有者仍由本机认领流程管理，如实拒绝（Codex 已做完）。 |
| ☑ | GET | `/api/app/me/unread` | `account.unread` | 按当前账号汇总未静音频道的新消息和已关注话题。所有者另外计入自己的通知；等待数沿用通知收件箱口径。 |
| ☑ | PUT | `/api/app/me/work-area` | `account.setWorkArea` | 引导答案随本人账号保存，只在本人的 /me 中读取，支持跳过 |

### org/deletion（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | DELETE | `ORG/deletion` | `orgs.restore` | 这个家从不进入删除：所有者调用直接 204，没有要恢复的。 |
| ☑ | POST | `ORG/deletion` | `orgs.deleteWorkspace` | 删除整个家不做（主人 2026-11-24 定）：409 `org.delete_unsupported`，要删先在服务器上备份再手动删。 |
| ☑ | POST | `ORG/deletion/code` | `orgs.deletionCode` | 同上，409 `org.delete_unsupported`，不发验证码。 |

### org/team-week（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/team-week` | `orgs.teamWeek` | 照实：每日卡＝常驻助理的早报，每周卡＝周日晚报（带一周总结）；时间来自 `VORTOONE_ASSISTANT_REPORT_TIMES`，没有转发地址或 IM 时都不发。 |
| ☑ | PUT | `ORG/team-week` | `orgs.setTeamWeek` | 时间和开关在服务环境里设：和现在一样照原样回，否则 409 `team_week.fixed`。 |
| ☑ | POST | `ORG/team-week/{x}` | `orgs.actOnTeamWeek` | 早晚报里没有可以直接照做的条目：404。 |

### org/agent-groups（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/agent-groups` | `agentGroups.create` | 列表读已登记的部门；新建、改、删部门要经提议，回 409 |
| ☑ | DELETE | `ORG/agent-groups/{x}` | `agentGroups.remove` | 同上 |
| ☑ | PATCH | `ORG/agent-groups/{x}` | `agentGroups.update` | 同上 |

### org/imports（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/imports` | `imports.sources` | 整包导入还没有：空。 |
| ☑ | POST | `ORG/imports` | `imports.apply` | 如实 409：Wiki、技能各有自己的导入。 |
| ☑ | POST | `ORG/imports/files` | `imports.copyFile` | 如实 409。 |

### org/blocks（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/blocks/{x}/draw` | `artifacts.drawBlock` | 多人一起改的互动块还没有：404。 |
| ☑ | GET | `ORG/blocks/{x}/state` | `artifacts.blockState` | 同上，404。 |
| ☑ | PUT | `ORG/blocks/{x}/state` | `artifacts.setBlockState` | 同上，404。 |

### org/proposals（3 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/proposals/{x}/accept` | `proposals.accept` | 团队背景的提议：agent 用 `propose_team_context` 交一份完整的新版本，所有者看前后对比，可以改几个字（`text`）再采纳。背景在提议之后改过的 409 `proposal.stale`。只有已认证的所有者能决定。 |
| ☑ | POST | `ORG/proposals/{x}/ask` | `proposals.ask` | 让 agent 提这条的成员可以请所有者看看（记下 `askedAt`，所有者的列表本来就列着每条待决定的）。成员只看得到自己发起的。 |
| ☑ | POST | `ORG/proposals/{x}/refuse` | `proposals.refuse` | 所有者拒绝，团队背景不变。读过外部内容的那一轮提的，`evidence` 里注明。 |

### org/actions（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/actions/{x}/redo` | `actions.redo` | agent 的操作回执还没有：404。 |
| ☑ | POST | `ORG/actions/{x}/undo` | `actions.undo` | 同上，404。 |

### org/action-cards（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/action-cards/{x}` | `actionCards.get` | 操作卡还没有：404。 |
| ☑ | POST | `ORG/action-cards/{x}/{x}` | `actionCards.decide` | 同上，404。 |

### org/task-manager（2 个，要做 0）

既有 `GET ORG/task-manager` 向已认证所有者显示 `since` 窗口内主聊天明确记录的完成、失败和取消。旧记录缺少终态时不推断。

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/task-manager/continue` | `working.continueTasks` | 还不能跳过额度接着做：409。 |
| ☑ | GET | `ORG/task-manager/{x}/steps` | `working.taskSteps` | 所有者的任务详情读取主聊天已记录的工具名称、调用开始与结果状态。专员无逐步记录时返回 404。 |

### /api/app/telemetry（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `/api/app/telemetry` | `system.telemetry` | 不做：依赖原产品云端（使用报告发给他们）；404 时界面藏起入口 |
| — | PUT | `/api/app/telemetry` | `system.setTelemetry` | 不做：依赖原产品云端（使用报告发给他们）；404 时界面藏起入口 |

### /api/app/device-accounts（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `/api/app/device-accounts` | `account.deviceAccounts` | 只列当前账号、可见空间及未读汇总，沿用 `/me/unread` 的权限。未加入时无空间汇总，暂时读不到计数时仍可读取账号。 |
| ☑ | POST | `/api/app/device-accounts/switch` | `account.switchDeviceAccount` | 一个浏览器只登一个账号：没有可切换的，409 `account.single_session`，不动当前登录。退出接口另可指定账号，先核对是当前会话（接 Codex #1080）。 |

现有退出接口接收可选的账号编号。提供编号时仅接受当前会话对应的账号，旧页面指定其他账号时拒绝退出。

### org/logo（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | DELETE | `ORG/logo` | `orgs.removeLogo` | 所有者可删除，本机工作区恢复默认标识 |
| ☑ | PUT | `ORG/logo` | `orgs.setLogo` | 所有者可上传 ≤768 KiB、最长边 ≤512 像素的 8 位 RGB/RGBA 非隔行 PNG |

### org/team（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/team` | `agents.team` | 只读名册，活动沿用自己的会话。团队图的汇报关系只显示双方已可见的连线。 |
| ☑ | POST | `ORG/team/channel-move` | `agents.proposeChannelMove` | agent 还不会提议搬移频道：409。 |

### org/team-templates（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/team-templates` | `agents.teamTemplates` | 还没有现成的团队模板：空列表。 |
| ☑ | POST | `ORG/team-templates/{x}/use` | `agents.applyTeamTemplate` | 404。 |

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
| ☐ | GET | `ORG/github/access` | `githubApp.access` | 等待工作区绑定的 GitHub 用户授权和组织访问结果。 |
| ☐ | GET | `ORG/github/repositories` | `githubApp.repositories` | 等待向该工作区验证的 GitHub 用户授权或 App 安装所授予的仓库清单。 |

### org/memories（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/memories/{x}/revert` | `memory.revert` | 记忆没有留旧版本：409，请直接编辑或删掉。 |
| ☑ | POST | `ORG/memories/{x}/team` | `memory.makeTeam` | 只有所有者能把自己的记忆设成团队记忆、能改文字；成员有 `review_memory` 只能移除、恢复。进所有 agent 的系统提示（对外发布的岗位除外），标明是资料不是指令 |

### org/sandbox-settings（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/sandbox-settings` | `sandbox.settings` | 仅所有者，照实：命令执行开关只在服务环境里（`VORTOONE_ENABLE_SHELL`），超时 300 秒，跑命令前每次都确认。 |
| ☑ | PUT | `ORG/sandbox-settings` | `sandbox.saveSettings` | 这里改不了（开关在服务环境里，确认门由内核决定）：和现在一样照原样回，否则 409 `sandbox.settings_fixed`。 |

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

### org/notification-prefs（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/notification-prefs` | `notifications.prefs` | 所有者可查看已配置的 APNs 手机目标和待审批、回复开关。设备按实际投递登记去重。 |
| ☑ | PUT | `ORG/notification-prefs` | `notifications.setPref` | 手机推送按类别开关：待审批、回复、任务（专员任务的完成、失败、提问）、例行工作。台账、IM 和审批状态照常。提到、话题、表情、电脑这里不推，开关不会生效，如实 422。 |

### org/questions（2 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/questions/{x}/answer` | `questions.answer` | 私聊里交代的事，专员用 `ask_person` 问交代的人；只有被问的人能答，答完按返工路径接着做（最多 3 轮），回答按资料交给专员 |
| ☑ | POST | `ORG/questions/{x}/dismiss` | `questions.dismiss` | 跳过后专员按自己的判断接着做 |

### /api/app/email-undo（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `/api/app/email-undo` | `account.undoEmailChange` | 这里还不能改登录邮箱，没有要撤回的：404 `email.undo_gone`（不登录也能调，只回这一句）。 |

### /api/app/organizations（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `/api/app/organizations` | `orgs.create` | 一台服务只有一个家：409 `organization.single_home`。 |

### org/onboarding（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | PATCH | `ORG/onboarding` | `orgs.saveOnboarding` | 这个家只有一个、早已设好（本机安装走 `/api/app/desktop-setup`）：只有所有者能调，照实回「已经完成」并带上主助理和 /me，不另存不生效的进度。 |

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

### org/autonomy-proposals（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/autonomy-proposals/{x}` | `agents.decideCostly` | agent 主动提出的高花费工作还没有：404。 |

### org/agent-imports（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/agent-imports/github` | `agents.importFromGithub` |  |

### org/brain-machines（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| — | GET | `ORG/brain-machines` | `agents.brainMachines` | Codex 连接电脑线 |

### org/refs（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/refs/{x}` | `chat.idRef` | 文字里的编号显示成小标签：看板任务、频道消息、制品（制品只给所有者），各按原有可见性核对；看不到和不存在一律 404。 |

### org/approvals（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | POST | `ORG/approvals/{x}/cancel` | `approvals.cancel` | 这里没有提前批准（每一项当场问）：409 `approval.not_ahead`。 |

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

### /api/desktop/latest（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `/api/desktop/latest` | `machines.desktopLatest` | General 和默认工作区均可公开读取 Apple Silicon 正式 DMG、SHA-256 和发布时间。未发布平台与发布源暂不可用均返回 404，错误码各自区分。下载页仍需准确标注 Apple Silicon，并纠正无法可靠识别架构时的回退行为。 |

### org/tasks（1 个，要做 0）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☑ | GET | `ORG/tasks/{x}/output` | `missions.taskOutput` | 已接入现有任务台账保存的输出，仅所有者可读 |

### org/admin-asks（1 个，要做 1）

| | 方法 | 路径 | 客户端方法 | 备注 |
|---|---|---|---|---|
| ☐ | POST | `ORG/admin-asks` | `notifications.askAdmins` | 无创建角色权限的在册成员可向所有者申请创建角色。在已加入且可发言、呼叫智能体的频道，成员可以申请添加自己可见且可进频道的角色。每人最多 10 个频道申请，工作区最多 200 个。同一申请 24 小时内去重，七天过期。所有者通过原有入口添加角色后，对应申请从收件箱移除。Webhook 请求待接。 |

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
