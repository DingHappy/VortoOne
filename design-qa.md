# 角色工作主线验收（2026-11-15）

范围：侧栏角色 → 工作会话 → 处理与交付 → 主人验收，以及添加角色 → 原提议批准 → 打开会话。
这是整体前端改版的一批，不代表安装、设置、资料库、例行工作和手机已完整复刻。
此前连接 AI 的验收归档在 `desktop/qa/design-qa-before-role-conversations.md`。

## 依据与视觉对照

- 固定参考：[agentes.chat 50ca784](https://github.com/agentes-chat/agentes-chat/tree/50ca784dd4c83efbc7ca916a6f07faa8accfe7f6)。
  研究 `DmPage` / `AgentPage` / `Sidebar` / `NewAgentDialog`，工作会话参考 Message / Composer。
  官方组件只在仓库外的隔离合成参考页运行，没有进入生产代码、依赖或发布产物。继续 Apache-2.0。
- Source visual truth：本机 `/private/tmp/agentes-role-reference.png`（整体组件参考）与
  `/private/tmp/agentes-role-panel-final.png`（960×852 的角色区参考）。这是官方组件加自编合成容器，
  没有运行其完整服务；不能据此声称整个应用已逐像素一致。
- 实现为生产组件、真实 GatewayClient、真实 owner API / StaffService / TaskRunner，资料位于独立临时目录。
  执行器显式放行后返回离线示例，不连接模型、不读私人资料、不发外部通知。
- 实现截图：[浅色会话](docs/design/personal-roles/conversation-light.png)、[深色会话](docs/design/personal-roles/conversation-dark.png)、
  [空状态](docs/design/personal-roles/empty.png)、[创建](docs/design/personal-roles/create.png)、
  [400 宽深色核对](docs/design/personal-roles/create-narrow-dark.png)。
- 完整实现视口 1240×900 CSS，输出 1240×900 PNG；窄窗口 400×900 CSS，输出 400×900 PNG。
  浏览器实际 DPR=2，捕获使用 CSS 像素归一化，没有放大旧图片。
  宿主浏览器锁定窗口尺寸，因此用真实 iframe 设置子页面视口，再按子页面原始 CSS 范围捕获。
- 完整组件参考 1716×907；聚焦比较采用 960×852 CSS/PNG 参考图，与实现相同大小的角色区域截取
  `/private/tmp/vortoone-role-panel-final.png` 一起查看（实现主区本身 952×844，余下 8px 为壳留白）。
  比较输入包含参考和实现两张图；没有把两次独立观察称作并排比较。
  内容均为同一份合成请求和交付。额外的过程、验收与岗位信息是 VortoOne 保留的合同。

## 对照结果与修正记录

1. [P1，已修] 角色首页直接显示旧岗位的模型执行指令，冗长且混入内部工具名。
   初次图 `/private/tmp/vortoone-role-empty.png`；改为从已登记权限生成简短能力说明，
   完整工具和权限仍可展开。修正后见 `empty.png` / `create.png`。
2. [P2，已修] 初次主人消息靠左，和角色交付混在同一列；官方 Message own 为靠右气泡。
   初次图 `/private/tmp/vortoone-role-result-light.png`；独立实现靠右的中性气泡，
   身份与时间保留给辅助阅读。修正后见 `conversation-light.png` 与最终角色区聚焦图。
3. 字体：沿用既有 Inter / Noto Sans SC / PingFang 回退；正文 15、身份 14、状态 12，
   使用 canonical token。官方组件参考正文更紧凑；本批保留中文阅读字号，标题、正文和操作层级可辨认。
4. 间距：紧凑侧栏、身份顶栏、居中单栏、右侧主人气泡和底部固定输入区。
   输入区保留交付方式与模型提示，因此比裸 Composer 参考高；不是把权限选项混进聊天正文。
5. 颜色：中性灰表面、细边线，状态使用共同变量。浅/深色均检查文本、输入边框、禁用与焦点状态。
6. 图像与图标：角色使用已选史莱姆 PNG，保持比例和透明边缘；没有用 CSS/SVG 重画角色。
   工具控件用现有 Lucide。未选择岗位时用通用身份占位，选定后显示对应角色。
7. 文案：已受理、正在处理、结果待验收、主人已验收分别显示；旧记录、读取不完整、来源变化和停止请求均明确。
   不把保存模型、执行结束、发出停止请求或角色登记写成任务已经完成。

最终复看完整浅/深色、聚焦消息区和窄窗口，未发现本批范围内仍需修复的 P0/P1/P2。
这些结果不代表全应用相对上游的差异清单已清零。

## 实际走通的交互

- 创建“资料助手”：下一步只核对；保存只产生待批提议；第二次明确批准才登记并打开空会话。
  批准前后任务数和执行数保持 1，没有自动派活。
- 发送示例任务：现有任务池实际进入 running；显式放行示例执行器后，回到 done / pending。
  点击验收通过仍先打开交付核对，第二次确认后原台账才变为 accepted。
- 已受理但返回 503：原文和原请求标识保留；只读刷新找到同一任务后清空原草稿，任务数/执行数均由 1 到 2，
  刷新没有增加到 3。没有自动重发。
- 添加第三件示例交付后，给资料助手留草稿、切到日程助手为空、切回原角色恢复同一草稿。
- 合成 composing Enter 事件前后任务数均为 3；没有误发。断连后发送按钮禁用、旧记录仅供回查。
- 400 CSS 宽下创建核对窗 x=12、宽 376，没有窗口内横向溢出；底部按钮可点击到达。
  只核对本次弹窗，不声称背景 Desktop 壳已变为手机导航。
- 直接页面的原生 Escape 经 CDP 输入事件关闭添加窗口，焦点回到“添加角色”。
  浏览器辅助 press 在该环境没有触发原生 cancel，因此未用它作为通过证据。
- 启用 Runtime / Log 事件后检查主线期间，未发现未捕获异常或 error 日志；人为制造的 503 是预期失败路径。

## 自动检查

- `./scripts/ci-local.sh` 完整模式最终退出 0：Python 7300 passed、30 条 canary passed；
  Desktop TypeScript、1688 条 Vitest、Rust 205 条与 cargo check、文档/合同检查通过。
- 跳过不算通过：Python 11 条（缺 textual / tool_sandbox 各 1；需显式启用的模型/浏览器 live 共 9）；
  Rust 2 条 ignored（真实账号登录、真实远端 Desktop）。没有声称运行这些外部场景。
- 新增后端 26 条、前端 28 条。断言真实行为：身份、权限字段拒绝、重复请求、摘要/轮次/实例、审计失败、
  旧空间与迟到回包、提议批准、草稿隔离、交付状态和分页。不检查源码字符串。
- `vite build` 通过；保留现有大 chunk 提示。没有原生安装、真实模型、手机真机或线上服务验收。

Final result: passed
