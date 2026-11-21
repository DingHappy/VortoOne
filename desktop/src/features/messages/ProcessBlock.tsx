// 消息里的一段过程：一行摘要，按需展开。工具调用、角色任务的处理记录都用它。
import { ChevronRight } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import "./messages.css";

export function ProcessBlock({ label, summary, attention, icon, active = false, open, defaultOpen = false, onToggle,
  ariaLabel, className, children }: {
  label: ReactNode;
  /** 摘要行右侧的次要信息：耗时、数量、回顾。 */
  summary?: ReactNode;
  /** 需要主人留意的提示；折叠时也保持可见。 */
  attention?: ReactNode;
  icon?: ReactNode;
  active?: boolean;
  /** 传入则由调用方控制；不传则自己记展开状态。 */
  open?: boolean;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
  ariaLabel?: string;
  className?: string;
  children?: ReactNode;
}) {
  const [own, setOwn] = useState(defaultOpen);
  const shown = open ?? own;
  const regionId = useId();
  return <section className={["vc-process", active ? "vc-process-active" : "", shown ? "vc-process-open" : "", className ?? ""]
    .filter(Boolean).join(" ")} aria-label={ariaLabel} aria-live={active ? "polite" : undefined}>
    <button type="button" className="vc-process-head" aria-expanded={shown} aria-controls={shown ? regionId : undefined}
      onClick={() => { if (open === undefined) setOwn(!shown); onToggle?.(!shown); }}>
      {icon && <span className="vc-process-icon" aria-hidden="true">{icon}</span>}
      <span className="vc-process-label">{label}</span>
      {attention && <strong className="vc-process-attention">{attention}</strong>}
      {summary && <small className="vc-process-summary">{summary}</small>}
      <ChevronRight className="vc-process-chevron" size={16} aria-hidden="true" />
    </button>
    {shown && <div className="vc-process-body" id={regionId}>{children}</div>}
  </section>;
}
