/** Production components and HTTP client, real temporary owner APIs and task pool.
 * Start role-conversation-server.py first. This fixture never calls a model.
 */
import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { GatewayClient } from "../src/gateway";
import { desktopContext } from "../src/lib/desktopContext";
import { settingsTargetLease, type SettingsTargetLease } from "../src/lib/settingsTarget";
import type { StaffMember, StaffRole, StaffSnapshot } from "../src/lib/staff";
import { RoleNavigation } from "../src/components/RoleNavigation";
import { RoleProfileDialog } from "../src/components/RoleProfile";
import { RoleGrants } from "../src/components/settings/StaffPane";
import { staffCharacter } from "../src/components/RoleAvatar";
import { WorkspaceBar } from "../src/components/WorkspaceBar";
import { PersonalSpacePicker } from "../src/components/PersonalSpacePicker";
import { AssistantSidebar } from "../src/components/AssistantMode";
import { SidebarFooter } from "../src/components/SidebarFooter";
import { ConversationHome } from "../src/components/ConversationHome";
import { RoleConversation } from "../src/features/roles/RoleConversation";
import { RoleCreateDialog } from "../src/features/roles/RoleCreateDialog";
import { roleDrafts } from "../src/features/roles/conversationController";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./frontend-redesign.css";

const settings = { baseUrl: "http://127.0.0.1:4352", token: "offline-role-owner", expectedScope: "general" as const };
const client = new GatewayClient(settings);
const fixtureAction = (action: string) => fetch(`${settings.baseUrl}/qa/${action}`, {
  method: "POST", headers: { Authorization: `Bearer ${settings.token}` },
});
function Fixture() {
  const params = new URLSearchParams(location.search);
  const [theme, setTheme] = useState(params.get("theme") || "light"), [connected, setConnected] = useState(true);
  const [collapsed, setCollapsed] = useState(false), [registry, setRegistry] = useState<StaffSnapshot | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<{ id: string; lease: SettingsTargetLease<GatewayClient> } | null>(null);
  const [adding, setAdding] = useState<SettingsTargetLease<GatewayClient> | null>(null);
  const [profile, setProfile] = useState<{ member?: StaffMember; role?: StaffRole } | null>(null);
  const pool = useRef(roleDrafts()).current;
  const transport = client.desktopSource;
  const context = desktopContext({ registrationId: null, assistantId: null, scope: "general", repoRoot: "", sid: transport.sid,
    baseUrl: settings.baseUrl, runtimeId: "offline-fixture", connection: connected ? "connected" : "disconnected",
    profiles: [], localResidentId: null, source: transport });
  const source = useRef({ context, client }); source.current = { context, client };
  const refresh = async () => { try { setRegistry(await client.readStaff()); setError(""); } catch { setError("离线服务尚未启动，请启动夹具后刷新"); } };
  useEffect(() => { void refresh(); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const open = (id: string) => { setProfile(null); setSelected({ id, lease: settingsTargetLease(() => source.current) }); };
  return <div className="app-shell vc-product-shell qa-reference-shell">
    <div className="qa-role-controls"><header className="qa-redesign-controls" hidden={params.has("capture")}>
      <span>离线真实接口与任务池 · 临时资料 · 未调用模型</span>
      <button onClick={() => setTheme(theme === "light" ? "dark" : "light")}>切换主题</button>
      <button onClick={() => setConnected(value => !value)}>{connected ? "模拟断开" : "恢复连接"}</button>
      <button onClick={() => void fixtureAction("finish").then(() => setNotice("示例执行已放行；等待真实任务池写回结果。"))}>交回示例结果</button>
      <button onClick={() => void fixtureAction("lose-receipt").then(() => setNotice("下次派活会实际受理，但丢失发送回执。"))}>下次丢失回执</button>
      <a href="/qa/settings-redesign.html?presentation=page&section=account">设置</a>
      <span role="status">{notice}</span>
    </header></div>
    <WorkspaceBar collapsed={collapsed} onToggle={() => setCollapsed(value => !value)} onSearch={() => setNotice("本夹具只验证角色主线；全局搜索仍由正式应用提供。")} />
    <div className={`workbench vc-mode-assistant inspector-closed ${collapsed ? "vc-sidebar-collapsed" : ""}`}>
      <aside id="conversation-sidebar" className="sidebar vc-sidebar">
        <PersonalSpacePicker context={context} profiles={[]} localResidentId={null} connection={connected ? "connected" : "disconnected"}
          switching={false} onSelect={async () => {}} onManage={() => setNotice("当前是独立的临时个人空间，不修改你的连接登记。")} />
        <AssistantSidebar mainChat={{ sid: "fixture-main" }} current={selected ? "" : "fixture-main"} sideChats={[]} page={null}
          onPage={() => setNotice("本夹具只验证角色主线，其它页面请在正式应用查看。")} onSelect={() => setSelected(null)} onNewSideChat={() => setNotice("旁聊不在本夹具范围。")}
          roles={<RoleNavigation name="VortoOne" directory={registry} connected={connected} error={error} onRefresh={() => void refresh()}
            currentAssistant={!selected} currentMember={selected?.id} onAssistant={() => setSelected(null)} onMember={open}
            onAdd={() => setAdding(settingsTargetLease(() => source.current))} />} />
        <SidebarFooter identity={{ initial: "", name: "我的账号", detail: "离线个人空间", title: "设置与账号" }} update={null}
          onOpenSettings={() => { location.href = "/qa/settings-redesign.html?presentation=page&section=account"; }} />
      </aside>
      {selected ? <RoleConversation key={`${selected.lease.target.readKey}:${selected.id}`} memberId={selected.id}
        name={registry?.members.find(member => member.id === selected.id)?.name || "角色"} lease={selected.lease} drafts={pool}
        onProfile={(member, role) => setProfile({ member, role })} onMain={() => setSelected(null)} />
        : <section className="conversation empty-state"><div className="conversation-body"><div className="conversation-scroll">
          <ConversationHome assistant starting={false} showStarting={false} now={new Date("2026-11-15T09:00:00")} />
          <p className="qa-role-notice">从侧栏选择一位角色，或添加角色，开始离线验证。</p>
        </div></div></section>}
    </div>
    {adding && registry && <RoleCreateDialog registry={registry} lease={adding} onClose={() => setAdding(null)}
      onRegistered={id => { void refresh(); setAdding(null); open(id); }} />}
    {profile && <RoleProfileDialog name={profile.member?.name || "角色"} description={profile.role?.brief || ""}
      character={staffCharacter(profile.role)} current={selected?.lease.current() ?? false} onClose={() => setProfile(null)}
      sections={[{ id: "settings", label: "设置", content: <p>{profile.member?.engine} · {profile.member?.model || "沿用登记模型"}</p> },
        { id: "access", label: "工具与权限", content: profile.role ? <RoleGrants role={profile.role} /> : null }]} />}
  </div>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
