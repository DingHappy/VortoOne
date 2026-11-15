/** 共用消息流验收：正式组件 + 合成数据；不连接运行端、模型或凭证。 */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ChatHistory, StreamingMessage } from "../src/components/ChatHistory";
import { RoleTaskMessage } from "../src/features/roles/RoleTaskMessage";
import type { RoleTask } from "../src/features/roles/types";
import type { ConversationMessage, TurnActivity } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "../src/features/roles/RoleConversation.css";
import "./message-stream.css";

const messages: ConversationMessage[] = [
  { id: "u1", role: "user", text: "把本周的资料整理成一份简短摘要，先列出需要我决定的部分。", rid: "r1" },
  { id: "a1", role: "assistant", rid: "r1", text: "资料已按主题整理。\n\n- **项目进展**：界面与使用流程的资料已经归在一起。\n- **待你决定**：先看本机运行，还是连接常驻电脑。\n- **下一步**：可从资料库回查完整记录。" },
  { id: "a2", role: "assistant", rid: "r1", text: "这是一段合成示例，没有读取你的资料或执行任务。" },
  { id: "u2", role: "user", text: "周报先不要发，给我看资料摘要。", rid: "r2", source: "telegram" },
  { id: "a3", role: "assistant", rid: "r2", source: "telegram", text: "摘要放在下面，周报保持草稿，没有发送。\n\n| 资料 | 状态 |\n|---|---|\n| 本周资料摘要 | 已整理 |\n| 周报 | 草稿，未发送 |" },
  { id: "u3", role: "user", text: "检查一下发布准备。", rid: "r3" },
  { id: "a4", role: "assistant", rid: "r3", text: "检查没有全部通过：有一项测试未过，原始回执在上面的过程里。" },
  { id: "u4", role: "user", text: "再核对一次来源和日期。", rid: "r4" },
];
const tool = (id: string, label: string, name: string, extra: Partial<TurnActivity> = {}): TurnActivity =>
  ({ id, kind: "tool", status: "completed", label, name, durationMs: 4200, result: "合成回执", ...extra });
const groups = new Map<string, TurnActivity[]>([
  ["r1", [{ id: "p1", kind: "phase", status: "completed", label: "思考", phase: "thinking", durationMs: 61000 },
    tool("t1", "读取 资料/本周.md", "read_file"), tool("t2", "搜索「待决定」", "grep"), tool("t3", "读取 记录/进展.md", "read_file")]],
  ["r2", [tool("t4", "读取 资料/摘要.md", "read_file")]],
  ["r3", [tool("t5", "运行检查", "run_tests", { status: "failed", result: "测试未过：合成失败", error: "exit 1" }),
    tool("t6", "读取 发布清单.md", "read_file")]],
  ["r4", [tool("t7", "读取 来源清单.md", "read_file"), tool("t8", "核对日期", "grep", { status: "running", result: undefined })]],
]);
const task = { id: "task-one", status: "done", prompt: "调研三家同类产品的引导流程，列出各自的步骤。", created: "2026-11-15T09:00:00Z",
  result: "三家产品的引导都先确定运行位置，再接模型。\n\n1. 甲：四步，带进度条。\n2. 乙：三步，可跳过。\n3. 丙：对话内清单。", report: {},
  confirmation: { digest: "one", round: 1, instance: "run" }, collaboration: { review: "pending", round: 1,
    acceptance: ["每家附上来源链接"], messages: [{ id: "m1", kind: "progress", round: 1, created: "2026-11-15T09:04:00Z", body: "已读取三份公开资料。" }],
    dispatch: { member: "reader", role: "reader", delivery: "review" } } } as unknown as RoleTask;
const running = { ...task, id: "task-two", status: "running", result: "", prompt: "把结论整理成一页对照表。", created: "2026-11-15T09:20:00Z",
  collaboration: { ...task.collaboration, acceptance: [], messages: [] } } as unknown as RoleTask;

function Fixture() {
  const query = new URLSearchParams(location.search);
  const [theme, setTheme] = useState(query.get("theme") === "dark" ? "dark" : "light");
  const [view, setView] = useState(query.get("view") === "role" ? "role" : "chat");
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  return <div className="qa-stream">
    <div className="qa-stream-controls"><span>合成内容，不连接服务</span>
      <button onClick={() => setView(view === "chat" ? "role" : "chat")}>切到{view === "chat" ? "角色会话" : "主要聊天"}</button>
      <button onClick={() => setTheme(theme === "light" ? "dark" : "light")}>切换主题</button></div>
    <div className="workbench vc-mode-assistant"><main className="conversation"><div className="conversation-scroll">
      {view === "chat" ? <>
        <ChatHistory messages={messages} activityGroups={groups} busy activeTurnRid="r4" assistant assistantName="VortoOne" />
        <StreamingMessage text="来源清单里有两项日期还没有核对到原文，" rid="r4" grouped />
      </> : <div className="role-conversation-reading">
        {[task, running].map(item => <RoleTaskMessage key={item.id} task={item} name="研究助手" character="research" current busy={false}
          getClient={() => null} onRefresh={() => {}} onAction={() => {}} />)}
      </div>}
    </div></main></div>
  </div>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
