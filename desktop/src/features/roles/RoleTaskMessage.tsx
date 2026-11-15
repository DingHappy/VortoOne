import { Check, Square } from "lucide-react";
import type { GatewayClient } from "../../gateway";
import { MarkdownMessage } from "../../components/MarkdownMessage";
import { ExecutionDetails } from "../../components/ExecutionDetails";
import { StaffActionCard } from "../../components/StaffActionCard";
import { RoleAvatar, type RoleCharacter } from "../../components/RoleAvatar";
import { Button } from "../../components/ui";
import type { RoleTask, RoleVerdict } from "./types";
import { COPY } from "../../lib/copy.generated";
import { MessageRow } from "../messages/MessageRow";
import { ProcessBlock } from "../messages/ProcessBlock";

export function roleTaskStatus(task: RoleTask) {
  if (task.collaboration.review === "accepted") return COPY.role.accepted;
  if (task.status === "done") return task.collaboration.dispatch.delivery === "review" ? COPY.role.awaiting_review : COPY.role.notify_only;
  if (task.status === "blocked") return task.collaboration.approval?.status === "pending" ? "等你确认这一步" : "等待继续";
  return ({ queued: "等待处理", running: "正在处理", failed: "处理未完成", interrupted: "中断，等你核对",
    cancelled: "已停止", paused: "已暂停" } as Record<string, string>)[task.status] || "状态待核对";
}

const at = (value: string) => {
  const time = new Date(value);
  return Number.isFinite(time.getTime()) ? time.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "时间待核对";
};

export function RoleTaskMessage({ task, name, character, current, busy, getClient, onRefresh, onAction }: {
  task: RoleTask; name: string; character?: RoleCharacter; current: boolean; busy: boolean;
  getClient: () => GatewayClient | null; onRefresh: () => void; onAction: (task: RoleTask, verdict: RoleVerdict) => void;
}) {
  const waiting = ["queued", "running", "blocked"].includes(task.status);
  const accepted = task.collaboration.review === "accepted";
  const canContinue = !waiting && !accepted && task.collaboration.round < 3 && !task.collaboration.dispatch.routine;
  const owner = (id: string, created: string, text: string, note = "") => <MessageRow key={id} own name="你"
    meta={<time dateTime={created}>{at(created)}{note}</time>}>{text}</MessageRow>;
  return <section className="role-task" aria-label={`交给${name}的任务`}>
    {owner(task.id, task.created, task.prompt ?? "")}
    {task.collaboration.messages.filter(message => message.kind === "followup")
      .map(message => owner(message.id, message.created, message.body, " · 补充要求"))}
    <MessageRow name={name} avatar={<RoleAvatar character={character} />}
      meta={<span className="vc-msg-meta role-task-state" data-state={accepted ? "accepted" : task.status}>{roleTaskStatus(task)}</span>}
      attachments={<>
        {task.error && <p className="vc-msg-error role-task-error">{task.error}</p>}
        <StaffActionCard task={task} getClient={getClient} onUpdated={onRefresh} />
        <ProcessBlock className="role-task-process" label="处理过程" summary={`第 ${task.collaboration.round} 轮`}>
          {task.collaboration.acceptance.length > 0 && <div className="role-task-criteria"><strong>交付要求</strong><ul>{task.collaboration.acceptance.map((item, i) => <li key={i}>{item}</li>)}</ul></div>}
          <ol>{task.collaboration.messages.map(message => <li key={message.id}><small>第 {message.round} 轮 · {at(message.created)}</small><p>{message.body}</p></li>)}</ol>
          <small>显示原任务台账保存的有限历史；旧消息可能经过截断。</small>
          <ExecutionDetails taskId={task.id} value={task.execution} current={current} />
        </ProcessBlock>
        <div className="role-task-actions">
          {accepted && <span className="role-task-accepted"><Check aria-hidden="true" />你的验收决定已保存</span>}
          {waiting && <Button size="md" variant="text" icon={<Square aria-hidden="true" />} disabled={!current || busy} onClick={() => onAction(task, "stop")}>停止这次处理</Button>}
          {canContinue && <Button variant="text" disabled={!current || busy} onClick={() => onAction(task, "rework")}>补充要求</Button>}
          {task.status === "done" && !accepted && task.collaboration.review === "pending" && task.collaboration.dispatch.delivery === "review"
            && <Button disabled={!current || busy} onClick={() => onAction(task, "accept")}>验收通过</Button>}
        </div>
      </>}>
      {task.result ? <MarkdownMessage text={task.result} />
        : <p className="vc-msg-pending">{waiting ? "处理过程和结果会在这里更新，你可以先做其它事。" : "这次没有完整结果，请先核对记录。"}</p>}
    </MessageRow>
  </section>;
}
