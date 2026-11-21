// 对话里的一条消息：主要聊天、频道和角色会话共用同一种呈现。
// 只负责排版（头像、作者、状态、正文、正文下的过程与卡片）；内容和动作由调用方传入。
import type { HTMLAttributes, ReactNode } from "react";
import "./messages.css";

export interface MessageRowProps extends Omit<HTMLAttributes<HTMLElement>, "children"> {
  /** 主人自己发的：靠右的中性气泡，不重复头像；作者和时间只留给读屏。 */
  own?: boolean;
  /** 紧接同一作者的上一条：省掉头像和作者行。 */
  grouped?: boolean;
  avatar?: ReactNode;
  name: string;
  /** 作者行上的状态、来源或时间。 */
  meta?: ReactNode;
  /** 气泡上方的来源标记（如来自 Telegram）。 */
  origin?: ReactNode;
  /** 正文之前的执行过程。 */
  process?: ReactNode;
  /** 正文之后的卡片、结果文档和按钮。 */
  attachments?: ReactNode;
  /** 悬停或聚焦时出现的消息操作。 */
  actions?: ReactNode;
  streaming?: boolean;
  children?: ReactNode;
}

export function MessageRow({ own = false, grouped = false, avatar, name, meta, origin, process, attachments,
  actions, streaming = false, className, children, ...rest }: MessageRowProps) {
  const classes = ["vc-msg", own ? "vc-msg-own" : "vc-msg-other", grouped ? "vc-msg-grouped" : "",
    streaming ? "vc-msg-streaming" : "", className ?? ""].filter(Boolean).join(" ");
  return <article className={classes} {...rest}>
    {!own && <div className="vc-msg-avatar" aria-hidden="true">{grouped ? null : avatar}</div>}
    <div className="vc-msg-main">
      {(own || !grouped) && <header className={own ? "vc-msg-head sr-only" : "vc-msg-head"}>
        <strong>{name}</strong>{meta}
      </header>}
      {origin && <div className="vc-msg-origin">{origin}</div>}
      {process}
      {children != null && children !== false && children !== "" && <div className="vc-msg-body">{children}</div>}
      {attachments}
      {actions}
    </div>
  </article>;
}
