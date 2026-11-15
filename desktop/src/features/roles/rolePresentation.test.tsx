import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RoleTaskMessage, roleTaskStatus } from "./RoleTaskMessage";
import { RoleNavigation } from "../../components/RoleNavigation";
import { roleSummary } from "./roleSummary";
import { roleEngines } from "./createController";
import type { StaffRole } from "../../lib/staff";
import type { RoleTask } from "./types";

const task = { id: "task-one", status: "done", result: "Delivery", prompt: "Request", report: {}, created: "2026-11-15T09:00:00Z",
  confirmation: { digest: "one", round: 1, instance: "run" }, collaboration: {
    review: "pending", round: 1, acceptance: [], messages: [], dispatch: { member: "reader", role: "reader", delivery: "review" },
  } } as unknown as RoleTask;
const markup = (value: RoleTask, current = true) => renderToStaticMarkup(<RoleTaskMessage task={value} name="研究助手" current={current}
  busy={false} getClient={() => null} onRefresh={() => {}} onAction={() => {}} />);
describe("role presentation", () => {
  it("renders delivered work as awaiting the owner, rather than automatically accepted", () => {
    expect(roleTaskStatus(task)).toBe("已交付 · 等你验收");
    expect(markup(task)).toMatch(/<button[^>]*>验收通过<\/button>/);
    const accepted: RoleTask = { ...task, collaboration: { ...task.collaboration, review: "accepted" } };
    expect(roleTaskStatus(accepted)).toBe("你已验收"); expect(markup(accepted)).not.toMatch(/<button[^>]*>验收通过<\/button>/);
  });
  it("does not offer acceptance for notify-only deliveries and disables an old space's actions", () => {
    const notify = { ...task, collaboration: { ...task.collaboration, dispatch: { ...task.collaboration.dispatch, delivery: "notify" as const } } };
    expect(markup(notify)).not.toMatch(/<button[^>]*>验收通过<\/button>/);
    expect(markup(task, false)).toContain('disabled=""');
  });
  it("shows role rows as conversation navigation with a single selected identity", () => {
    const html = renderToStaticMarkup(<RoleNavigation name="VortoOne" connected currentAssistant directory={null}
      onAssistant={() => {}} onMember={() => {}} onAdd={() => {}} />);
    expect(html).toContain('aria-label="打开与VortoOne的对话"');
    expect(html.match(/aria-current="page"/g)).toHaveLength(1); expect(html).not.toContain('aria-haspopup="dialog"');
  });
  it("summarizes registered capabilities without exposing the model's internal prompt or offering forbidden engines", () => {
    const role = { id: "web-researcher", title: "研究员", brief: "internal execution prompt", engine: ["vortocode", "opencode"],
      grants: { project: false, web: true, sees: [], tools: ["web_search"], operations: ["read"] } } as unknown as StaffRole;
    expect(roleSummary(role)).toBe("查阅公开资料，整理内容并附上来源。");
    expect(roleEngines(role)).toEqual(["vortocode"]);
    expect(roleEngines({ ...role, id: "developer" })).toEqual(["vortocode", "opencode"]);
  });
});
