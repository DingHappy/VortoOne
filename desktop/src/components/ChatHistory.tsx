import { Fragment, memo, useMemo, type ReactNode } from "react";
import { sourceLabel } from "../lib/mainChat";
import type { AssistantAvatar, ConversationMessage, TurnActivity } from "../types";
import { MarkdownMessage } from "./MarkdownMessage";
import { RoleAvatar } from "./RoleAvatar";
import { TurnTimeline } from "./TurnTimeline";
import { MessageActions } from "./MessageActions";
import { MessageRow } from "../features/messages/MessageRow";

const authorName = (message: ConversationMessage, assistantName: string) => message.role === "user" ? "你"
  : message.role === "assistant" ? message.source === "team" ? "任务小组" : assistantName : "系统";
const authorKey = (message: ConversationMessage) => `${message.role}:${message.role === "assistant" && message.source === "team" ? "team" : ""}`;

function AssistantAvatarMark({ message, avatar }: { message: ConversationMessage; avatar?: AssistantAvatar | null }) {
  const own = message.role === "assistant" && message.source !== "team";
  return <RoleAvatar character={own ? "general" : undefined} avatar={own ? avatar : undefined} />;
}

/** 正在回复的那一条；与已保存消息同一种排版。回合的执行过程已另起一行时接在它下面，不重复头像。 */
export function StreamingMessage({ name = "VortoOne", avatar, text, rid, grouped = false }: {
  name?: string; avatar?: AssistantAvatar | null; text: string; rid?: string | null; grouped?: boolean;
}) {
  return <MessageRow streaming grouped={grouped} name={name} avatar={<RoleAvatar character="general" avatar={avatar} />}
    data-message-role="assistant">
    <MarkdownMessage text={text} /><i className="cursor" data-rid={rid ?? undefined} />
  </MessageRow>;
}

/** 回合的执行过程挂在哪条消息里：该回合第一条助理回复；还没有回复时，在主人那句话后面单起一行。 */
export function processHosts(messages: ConversationMessage[], activityGroups: Map<string, TurnActivity[]>) {
  const hosts = new Map<string, string>();
  for (const message of messages) {
    if (message.role === "assistant" && message.source !== "team" && message.rid
      && activityGroups.has(message.rid) && !hosts.has(message.rid)) hosts.set(message.rid, message.id);
  }
  return hosts;
}

/** 流式正文与输入草稿更新时，历史消息不重新解析；时间线状态仍由原回合资料驱动。 */
export const ChatHistory = memo(function ChatHistory({ messages, activityGroups, busy, activeTurnRid,
  assistant, assistantName = "VortoOne", assistantAvatar, focusedRid, renderCard }: {
  messages: ConversationMessage[];
  activityGroups: Map<string, TurnActivity[]>;
  busy: boolean;
  activeTurnRid: string | null;
  assistant: boolean;
  assistantName?: string;
  assistantAvatar?: AssistantAvatar | null;
  focusedRid?: string | null;
  /** 代码写进对话的状态消息（如任务小组交付了）下面的按钮 */
  renderCard?: (message: ConversationMessage) => ReactNode;
}) {
  const outputRids = useMemo(() => new Set(messages
    .filter(message => message.role === "assistant" && message.text.trim().length > 0)
    .map(message => message.rid)), [messages]);
  const hosts = useMemo(() => processHosts(messages, activityGroups), [messages, activityGroups]);
  const timeline = (rid: string) => <TurnTimeline items={activityGroups.get(rid)!} active={busy && activeTurnRid === rid}
    hasOutput={outputRids.has(rid)} />;
  if (!assistant) return <>{messages.map(message => <Fragment key={message.id}>
    <article className={`message ${message.role}${focusedRid && message.rid === focusedRid ? " located-turn" : ""}`}
      data-turn-rid={message.rid} data-message-role={message.role}
      tabIndex={focusedRid && message.rid === focusedRid ? -1 : undefined}>
      <div className="message-author">{message.role === "user" ? "你" : message.role === "assistant" ? "VortoOne" : "Runtime"}</div>
      <div className="message-body">
        {message.role === "user" && sourceLabel(message.source) && <div className="message-source">{sourceLabel(message.source)}</div>}
        {message.images && message.images.length > 0 && <div className="message-images">
          {message.images.map((src, index) => <img key={index} src={src} alt={`发送的图片 ${index + 1}`} />)}
        </div>}
        {message.role === "assistant" ? <MarkdownMessage text={message.text} /> : message.text ? <div className="message-text">{message.text}</div> : null}
        {message.card && renderCard?.(message)}
      </div>
    </article>
    {message.role === "user" && message.rid && activityGroups.has(message.rid) && timeline(message.rid)}
  </Fragment>)}</>;
  return <>{messages.map((message, index) => {
    const located = Boolean(focusedRid && message.rid === focusedRid);
    const previous = messages[index - 1];
    const grouped = message.role !== "user" && !!previous && authorKey(previous) === authorKey(message);
    const hosted = message.rid && hosts.get(message.rid) === message.id ? message.rid : null;
    const pending = message.role === "user" && message.rid && activityGroups.has(message.rid) && !hosts.has(message.rid)
      ? message.rid : null;
    const images = message.images && message.images.length > 0 ? <div className="vc-msg-images">
      {message.images.map((src, imageIndex) => <img key={imageIndex} src={src} alt={`发送的图片 ${imageIndex + 1}`} />)}
    </div> : null;
    const text = !message.text.trim() ? null : message.role === "assistant" ? <MarkdownMessage text={message.text} /> : message.text;
    return <Fragment key={message.id}>
      <MessageRow own={message.role === "user"} grouped={grouped}
        className={[message.role === "system" ? "vc-msg-system" : "", located ? "located-turn" : ""].filter(Boolean).join(" ") || undefined}
        name={authorName(message, assistantName)}
        avatar={<AssistantAvatarMark message={message} avatar={assistantAvatar} />}
        meta={message.role === "assistant" && message.source && message.source !== "team"
          ? <span className="vc-msg-meta">回复在手机上</span> : undefined}
        origin={message.role === "user" ? sourceLabel(message.source) : undefined}
        process={hosted ? timeline(hosted) : undefined}
        attachments={message.card ? renderCard?.(message) : undefined}
        actions={message.role !== "system" ? <MessageActions text={message.text} /> : undefined}
        data-turn-rid={message.rid} data-message-role={message.role} tabIndex={located ? -1 : undefined}>
        {images || text ? <>{images}{text}</> : null}
      </MessageRow>
      {pending && <MessageRow name={assistantName} avatar={<RoleAvatar character="general" avatar={assistantAvatar} />}
        process={timeline(pending)} data-turn-rid={pending} data-message-role="process" />}
    </Fragment>;
  })}</>;
});
