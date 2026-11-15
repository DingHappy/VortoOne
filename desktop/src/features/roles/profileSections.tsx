import type { StaffMember, StaffRole } from "../../lib/staff";
import { COPY } from "../../lib/copy.generated";
import type { RoleProfileSection } from "../../components/RoleProfile";
import { PreferenceGroup, PreferenceRow } from "../../components/ui/Preferences";
import { Button } from "../../components/ui";
import { RoleGrants } from "../../components/settings/StaffPane";
import { ExecutionDetails } from "../../components/ExecutionDetails";

/** Read-only projection of the frozen registry record. Mutations stay in the original settings flow. */
export function registeredRoleSections(member: StaffMember, role: StaffRole | undefined, current: boolean,
  onManage: () => void): RoleProfileSection[] {
  return [
    { id: "settings", label: COPY.role.settings, content: <>
      <PreferenceGroup title="角色资料"><PreferenceRow title="名字">{member.name}</PreferenceRow>
        <PreferenceRow title="权限方案">{role?.title || COPY.role.unknown}</PreferenceRow>
        <PreferenceRow title="状态">{current ? ({ active: COPY.role.active, paused: COPY.role.paused, retired: COPY.role.retired }[member.status] || COPY.role.unverified) : COPY.role.unverified}</PreferenceRow>
        <PreferenceRow title="执行方式">{member.engine === "vortocode" ? "内置助理" : member.engine}</PreferenceRow>
        <PreferenceRow title="模型">{member.model || "沿用登记配置"}</PreferenceRow></PreferenceGroup>
      <Button onClick={onManage}>{COPY.role.manage}</Button>
      <p>在角色工作会话里交代任务；接活时仍核对已批准的权限方案、部门和额度。</p>
    </> },
    { id: "access", label: COPY.role.access, content: <>{role ? <RoleGrants role={role} /> : <p>{COPY.role.unknown}</p>}
      <Button onClick={onManage}>核对权限与待批提议</Button></> },
    { id: "activity", label: COPY.role.activity, content: <>{!member.recent_tasks.length && <p>还没有工作记录。</p>}
      {member.recent_tasks.map(task => <article className="vc-role-profile-task" key={task.id}>
        <strong>{task.prompt || task.id}</strong><span>任务状态：{({ queued: "待开始", running: "在忙", blocked: "等回答", done: "已交回", failed: "失败", interrupted: "中断", cancelled: "已取消" } as Record<string, string>)[task.status] ?? "状态待核对"} · 核对状态：{task.review === "accepted" ? task.delivery === "review" ? "主人已验收" : "已核对汇报" : ({ pending: "待核对", rework_requested: "待返工", not_submitted: "尚未交付" } as Record<string, string>)[task.review] ?? "审查待核对"}</span>
        <ExecutionDetails taskId={task.id} value={task.execution} current={current} /></article>)}</> },
  ];
}
