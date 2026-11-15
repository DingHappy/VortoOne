# Desktop 模型连接

2026-11-14，主人再次确认：保留 Apache-2.0，独立实现 agentes.chat 的连接操作流程。
本次参考其 [ConnectAi](https://github.com/agentes-chat/agentes-chat/blob/745d26cde30d6acff732c46c652ed1a385a72a80/apps/web/src/components/ConnectAi.tsx) 与
[EndpointForm](https://github.com/agentes-chat/agentes-chat/blob/745d26cde30d6acff732c46c652ed1a385a72a80/apps/web/src/components/ai/EndpointForm.tsx)，
不迁入上游代码、样式、翻译、图标、依赖或服务端；仓库许可不变。

## 操作流程

在「设置 → 模型与执行」点击「连接 AI」：

1. 选择 API 密钥或本机模型，再选择已有预设或自定义 OpenAI 兼容地址。
2. 填写连接，主动获取模型列表，或手动填写模型编号。
   首次配置选一个默认模型；已有默认配置时，选择加入菜单的模型。
3. 核对保存位置、地址和模型，再经系统确认保存。取消或失败保留草稿。

现有连接的「模型」按钮可修改清单；「编辑」仍可修改原服务字段。
地址和密钥变化后会清空旧候选，迟到的旧查询不能修改新草稿。
切换服务会先询问是否放弃已填密钥和模型。关闭向导继续使用原未保存检查。

此入口只管理这台 Mac 的 Desktop 模型配置。常驻助手独立管理，复制配置仍须主人明确确认。
会员入口保留；其它订阅登录仍由已有执行器管理。本向导不新增订阅登录，也未接入 Anthropic 原生 Messages 协议。
保存、模型列表可达、真实推理、工具调用分别是不同结果；界面不把它们合并成「已验证」。

## 原生存储与运行合同

- `ConnectAiDialog` 复用 `test_llm_connection`、`set_llm_profile`、`save_llm_provider`。
  网页层不直接请求模型地址，密钥仍由原生命令写入原配置文件。
  查询始终携带本连接 ID，新连接不能借用默认连接密钥，即使地址相同。
- `set_llm_profile` 继续处理首次默认模型；新增其它连接不替换当前默认。
  新的 `selectedModels` 是 `save_llm_provider` 的可选参数，原调用者可省略。
- 原生层验证显式清单：1～200 项、模型编号最长 160 字节，不能含空白、逗号或控制字符；
  重复项合并且保留顺序。系统确认显示地址与所选模型，未确认不写入。
- 显式清单保存在已有 provider 记录的可选 `selectedModels` 字段，刷新发现不会覆盖它。
  旧记录没有该字段，继续跟随服务发现；发现失败保留旧清单。
  此清单供模型菜单使用，不是岗位权限白名单。
- 不改变配置版本与原子提交规则。冲突后禁止再次保存，关闭向导后通过原冲突处理核对最新配置。
  系统确认仍在 WebView 外，保存成功后的引擎处理复用原逻辑。
- 状态回执不含密钥；主人确认、污点、岗位与无人值守规则均未修改。没有新增 HTTP 路由。

## 验证

- `desktop/src/lib/connectAi.test.ts`：新旧连接的命令参数、版本、密钥边界、模型校验与保存回执；
  通过实际 `modelChoices` 验证手动模型进入聊天菜单。
- `desktop/src/lib/llmConnectionCheck.test.ts`：查询代次与当前草稿保护，向导直接复用该实现。
- Rust：`selected_provider_models_survive_normalization_and_old_data_stays_readable`、
  `invalid_explicit_provider_models_are_rejected_instead_of_silently_dropped` 先在旧实现失败；
  另覆盖显式清单刷新、配置文件读写和老清单发现行为。
- `desktop/qa/settings-redesign.html` 使用真实组件与合成 IPC，覆盖首次连接、取消、冲突、迟到查询、
  已有连接编辑和长列表搜索。截图与结果见根目录 `design-qa.md`。

真实账号、真实推理与工具调用、安装版系统确认、常驻服务和手机端不由这些离线验证证明。
