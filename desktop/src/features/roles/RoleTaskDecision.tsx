import { useRef, useState } from "react";
import { X } from "lucide-react";
import { useDialogSurface } from "../../hooks/useDialogSurface";
import { MarkdownMessage } from "../../components/MarkdownMessage";
import { Button, IconButton } from "../../components/ui";
import type { RoleTask, RoleVerdict } from "./types";

export function RoleTaskDecision({ task, verdict, busy, error, current, onClose, onConfirm }: {
  task: RoleTask; verdict: RoleVerdict; busy: boolean; error: string; current: boolean;
  onClose: () => void; onConfirm: (note: string) => Promise<void>;
}) {
  const dialog = useDialogSurface(), submitting = useRef(false);
  const [note, setNote] = useState(verdict === "accept" ? "已核对交付，验收通过" : verdict === "stop" ? "主人请求停止这次处理" : "");
  const title = verdict === "accept" ? "核对这次交付" : verdict === "rework" ? "补充要求后继续处理" : "停止这次处理？";
  return <dialog ref={dialog} className="role-decision-dialog" aria-label={title}
    onCancel={event => { event.preventDefault(); if (!busy && !submitting.current) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); if (!current || busy || submitting.current || !note.trim()) return;
      submitting.current = true; void onConfirm(note).finally(() => { submitting.current = false; }); }}>
      <header><h2>{title}</h2><IconButton label="关闭任务核对" icon={<X aria-hidden="true" />} disabled={busy} onClick={onClose} /></header>
      <div className="role-decision-body"><p className="role-decision-request">{task.prompt}</p>
        {verdict === "accept" && <><MarkdownMessage text={task.result || ""} /><p>确认后记录为你已验收。对外发送仍需另行确认。</p></>}
        {verdict === "stop" && <p>只请求停止当前这件任务，结果以执行池回执为准。原定时安排保持现有设置。</p>}
        {verdict === "rework" && <><p>继续使用原来的岗位权限，新一轮仍按原规则执行和等待确认。</p>
          <label>补充的要求<textarea autoFocus rows={4} value={note} maxLength={2000} disabled={busy} onChange={event => setNote(event.target.value)} /></label></>}
        {!current && <p role="alert">当前记录暂不能操作，请关闭后刷新核对。</p>}
        {error && <p role="alert" className="role-task-error">{error}</p>}
      </div>
      <footer><Button disabled={busy} onClick={onClose}>返回</Button><Button type="submit" variant="primary" disabled={busy || !current || !note.trim()}>{busy ? "正在保存…" : verdict === "accept" ? "确认验收通过" : verdict === "rework" ? "交回角色继续处理" : "请求停止"}</Button></footer>
    </form>
  </dialog>;
}
