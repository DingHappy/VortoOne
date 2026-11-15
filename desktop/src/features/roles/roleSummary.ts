import type { StaffRole } from "../../lib/staff";

/** User-facing capabilities; the registered execution prompt stays in role details. */
export function roleSummary(role?: StaffRole) {
  if (!role) return "选择一份已登记的分工与权限方案。";
  if (role.grants.project) return "在已登记项目的一次性副本中处理开发任务。";
  if (role.grants.web) return "查阅公开资料，整理内容并附上来源。";
  if (role.grants.tools.includes("calendar_events")) return "读取日程，整理安排和需要你核对的事项。";
  if (role.grants.tools.includes("mail_search")) return "按已登记的权限整理邮件内容。";
  return `按「${role.title}」的已登记权限处理你交代的事情。`;
}
