import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RoleCreateDialog, roleCapabilities } from "./RoleCreateDialog";
import type { StaffRole, StaffSnapshot } from "../../lib/staff";
import type { GatewayClient } from "../../gateway";
import type { SettingsTargetLease } from "../../lib/settingsTarget";

const role: StaffRole = { id: "reader", title: "资料整理", brief: "Private execution prompt", revision: 3, enabled: true,
  builtin: false, max_steps: 12, timeout_seconds: 600, engine: ["vortocode"],
  grants: { web: false, project: false, tools: [], sees: [], operations: ["read"] } };
const registry: StaffSnapshot = { roles: [{ ...role, id: "disabled", enabled: false }, role], members: [], proposals: [],
  conversation_version: 1, owner_instance: "instance", role_digests: { reader: "digest" } };
function render(snapshot = registry, current = true) {
  const readStaff = vi.fn(), proposeStaffMember = vi.fn(), decideStaff = vi.fn();
  const lease = { client: { readStaff, proposeStaffMember, decideStaff }, current: () => current,
    target: { runtimeLabel: "离线测试电脑" } } as unknown as SettingsTargetLease<GatewayClient>;
  return { html: renderToStaticMarkup(<RoleCreateDialog registry={snapshot} lease={lease} onRegistered={() => {}} onClose={() => {}} />),
    readStaff, proposeStaffMember, decideStaff };
}

describe("role card creation", () => {
  it("starts with an enabled registered role and an empty name, without proposing or approving on render", () => {
    const f = render();
    expect(f.html.match(/role="radio"/g)).toHaveLength(1);
    expect(f.html).toContain('aria-checked="true" tabindex="0"');
    expect(f.html).toContain('aria-label="角色名字" value=""');
    expect(f.html).toMatch(/<button[^>]*disabled=""[^>]*>保存角色提议<\/button>/);
    expect(f.html).toContain("修订 3");
    expect(f.html).not.toContain(role.brief);
    expect(f.readStaff).not.toHaveBeenCalled(); expect(f.proposeStaffMember).not.toHaveBeenCalled(); expect(f.decideStaff).not.toHaveBeenCalled();
  });
  it("does not offer an editable permission grant, and keeps all unavailable states explicit", () => {
    const f = render({ ...registry, roles: [] });
    expect(f.html).toContain("还没有启用的分工");
    expect(f.html).not.toContain('role="radio"');
    expect(f.html).toMatch(/<button[^>]*disabled=""[^>]*>保存角色提议<\/button>/);
    expect(render({ ...registry, conversation_version: undefined }).html).toContain("请先更新服务");
    expect(render(registry, false).html).toContain("个人空间或连接已变化");
  });
  it("describes the registered scope without inventing code-write or read authority", () => {
    expect(roleCapabilities(role)).toEqual(["不联网", "不读私人来源", "没有登记工具"]);
    expect(roleCapabilities({ ...role, grants: { ...role.grants, project: true }, project: "测试项目",
      project_binding: { name: "测试项目", repo_root: "/fixture/project", port: 1 } }))
      .toContain("仅在项目「测试项目」的一次性副本中工作");
  });
});
