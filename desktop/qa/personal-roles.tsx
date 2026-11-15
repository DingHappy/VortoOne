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
import { RoleProfilePage } from "../src/components/RoleProfile";
import { staffCharacter } from "../src/components/RoleAvatar";
import { WorkspaceBar } from "../src/components/WorkspaceBar";
import { PersonalSpacePicker } from "../src/components/PersonalSpacePicker";
import { AssistantSidebar } from "../src/components/AssistantMode";
import { SidebarFooter } from "../src/components/SidebarFooter";
import { ConversationHome } from "../src/components/ConversationHome";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { CreateChannelDialog } from "../src/components/SessionChannels";
import { sessionChannel } from "../src/lib/sessionChannels";
import type { SessionSummary } from "../src/types";
import { RoleConversation } from "../src/features/roles/RoleConversation";
import { RoleCreateDialog } from "../src/features/roles/RoleCreateDialog";
import { roleDrafts } from "../src/features/roles/conversationController";
import { roleSummary } from "../src/features/roles/roleSummary";
import { registeredRoleSections } from "../src/features/roles/profileSections";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./frontend-redesign.css";

const requestedPort = typeof location === "undefined" ? "" : new URLSearchParams(location.search).get("apiPort") || "";
const apiPort = /^\d{4,5}$/.test(requestedPort) && Number(requestedPort) <= 65535 ? requestedPort : "4352";
const settings = { baseUrl: `http://127.0.0.1:${apiPort}`, token: "offline-role-owner", expectedScope: "general" as const };
const client = new GatewayClient(settings);
const fixtureAction = (action: string) => fetch(`${settings.baseUrl}/qa/${action}`, {
  method: "POST", headers: { Authorization: `Bearer ${settings.token}` },
});
export function PersonalRolesFixture() {
  const params = new URLSearchParams(typeof location === "undefined" ? "" : location.search);
  const [theme, setTheme] = useState(params.get("theme") || "light"), [connected, setConnected] = useState(true);
  const [collapsed, setCollapsed] = useState(false), [registry, setRegistry] = useState<StaffSnapshot | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<{ id: string; lease: SettingsTargetLease<GatewayClient> } | null>(null);
  const [adding, setAdding] = useState<SettingsTargetLease<GatewayClient> | null>(null);
  const [profile, setProfile] = useState<{ member?: StaffMember; role?: StaffRole; lease: SettingsTargetLease<GatewayClient> } | null>(null);
  const [channels, setChannels] = useState<SessionSummary[]>([]), [channel, setChannel] = useState<SessionSummary | null>(null);
  const [channelLease, setChannelLease] = useState<SettingsTargetLease<GatewayClient> | null>(null);
  const [channelError, setChannelError] = useState("");
  const pool = useRef(roleDrafts()).current;
  const transport = client.desktopSource;
  const context = desktopContext({ registrationId: null, assistantId: null, scope: "general", repoRoot: "", sid: transport.sid,
    baseUrl: settings.baseUrl, runtimeId: "offline-fixture", connection: connected ? "connected" : "disconnected",
    profiles: [], localResidentId: null, source: transport });
  const source = useRef({ context, client }); source.current = { context, client };
  const refresh = async () => { try { setRegistry(await client.readStaff()); setError(""); } catch { setError("离线服务尚未启动，请启动夹具后刷新"); } };
  const refreshChannels = async () => {
    const lease = settingsTargetLease(() => source.current);
    try {
      const rows = await lease.client!.listSessions();
      if (lease.current()) { setChannels(rows.filter(row => sessionChannel(row))); setChannelError(""); }
    } catch { if (lease.current()) setChannelError("频道暂时读不到，请刷新核对。"); }
  };
  useEffect(() => { void refresh(); void refreshChannels(); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const openMain = () => { setProfile(null); setSelected(null); setChannel(null); };
  const open = (id: string) => { setProfile(null); setChannel(null); setSelected({ id, lease: settingsTargetLease(() => source.current) }); };
  return <div className="app-shell vc-product-shell qa-reference-shell">
    <div className="qa-role-controls"><header className="qa-redesign-controls" hidden={params.has("capture")}>
      <span>离线真实接口与任务池 · 临时资料 · 未调用模型</span>
      <button onClick={() => setTheme(theme === "light" ? "dark" : "light")}>切换主题</button>
      <button onClick={() => setConnected(value => !value)}>{connected ? "模拟断开" : "恢复连接"}</button>
      <button onClick={() => void fixtureAction("finish").then(() => setNotice("示例执行已放行；等待真实任务池写回结果。"))}>交回示例结果</button>
      <button onClick={() => void fixtureAction("lose-receipt").then(() => setNotice("下次派活会实际受理，但丢失发送回执。"))}>下次丢失回执</button>
      <a href="/qa/settings-redesign.html?presentation=page&section=account">设置</a>
      <button onClick={() => void refreshChannels()}>刷新频道</button>
      <span role="status">{notice}</span>
    </header></div>
    <WorkspaceBar collapsed={collapsed} onToggle={() => setCollapsed(value => !value)} onSearch={() => setNotice("本夹具只验证角色主线；全局搜索仍由正式应用提供。")} />
    <div className={`workbench vc-mode-assistant inspector-closed ${collapsed ? "vc-sidebar-collapsed" : ""}`}>
      <aside id="conversation-sidebar" className="sidebar vc-sidebar">
        <PersonalSpacePicker context={context} profiles={[]} localResidentId={null} connection={connected ? "connected" : "disconnected"}
          switching={false} onSelect={async () => {}} onManage={() => setNotice("当前是独立的临时个人空间，不修改你的连接登记。")} />
        <AssistantSidebar mainChat={{ sid: "fixture-main" }} current={selected ? "" : channel?.sid || "fixture-main"} sideChats={[]} page={null}
          channels={channels} channelsDisabled={!connected} onNewChannel={() => setChannelLease(settingsTargetLease(() => source.current))}
          onPage={() => setNotice("本夹具验证角色主线和频道入口，其它页面请在正式应用查看。")}
          onSelect={sid => { setProfile(null); setSelected(null); setChannel(channels.find(row => row.sid === sid) || null); }} onNewSideChat={() => setNotice("旁聊不在本夹具范围。")}
          roles={<RoleNavigation name="VortoOne" directory={registry} connected={connected} error={error} onRefresh={() => void refresh()}
            currentAssistant={!selected && !channel} currentMember={selected?.id} onAssistant={openMain} onMember={open}
            onAdd={() => setAdding(settingsTargetLease(() => source.current))} />} />
        {channelError && <p role="status" className="qa-role-notice">{channelError}</p>}
        <SidebarFooter identity={{ initial: "", name: "我的账号", detail: "离线个人空间", title: "设置与账号" }} update={null}
          onOpenSettings={() => { location.href = "/qa/settings-redesign.html?presentation=page&section=account"; }} />
      </aside>
      {profile ? <RoleProfilePage name={profile.member?.name || "角色"} description={roleSummary(profile.role)}
        sourceLabel={profile.lease.target.runtimeLabel} character={staffCharacter(profile.role)} current={profile.lease.current()}
        onClose={() => setProfile(null)} sections={profile.member
          ? registeredRoleSections(profile.member, profile.role, profile.lease.current(), () => setNotice("正式应用会打开原角色与权限设置；此处只展示临时合成资料。"))
          : [{ id: "settings", label: "设置", content: <p>角色资料尚未读取。</p> }]} /> : selected ? <RoleConversation key={`${selected.lease.target.readKey}:${selected.id}`} memberId={selected.id}
        name={registry?.members.find(member => member.id === selected.id)?.name || "角色"} lease={selected.lease} drafts={pool}
        onProfile={(member, role) => setProfile({ member: structuredClone(member), role: structuredClone(role), lease: selected.lease })} onMain={openMain} />
        : channel ? <section className="conversation conversation-channel empty-state">
          <ConversationHeader title={channel.title} channelTopic={sessionChannel(channel)!.topic} place={null} panelOpen={false}
            waiting={0} plan={{ done: 0, total: 0 }} busy={false} onTogglePanel={() => setNotice("此预览只核对频道入口与创建，不连接聊天模型。")} onStop={() => {}} />
          <div className="conversation-body"><div className="conversation-scroll"><div className="vc-channel-welcome">
            <h1>{channel.title}</h1><p>{sessionChannel(channel)!.topic}</p>
            <small>频道已保存在临时个人空间。此预览只核对频道入口与创建；聊天消息请在正式应用中发送。</small></div>
          </div></div>
        </section>
        : <section className="conversation empty-state"><div className="conversation-body"><div className="conversation-scroll">
          <ConversationHome assistant starting={false} showStarting={false} now={new Date("2026-11-15T09:00:00")} />
          <p className="qa-role-notice">从侧栏选择一位角色，或添加角色，开始离线验证。</p>
        </div></div></section>}
    </div>
    {adding && registry && <RoleCreateDialog registry={registry} lease={adding} onClose={() => setAdding(null)}
      onRegistered={id => { void refresh(); setAdding(null); open(id); }} />}
    {channelLease && <CreateChannelDialog lease={channelLease} onClose={() => setChannelLease(null)} onCreated={row => {
      if (!channelLease.current()) return;
      setChannels(previous => [row, ...previous.filter(item => item.sid !== row.sid)]);
      setChannelLease(null); setProfile(null); setSelected(null); setChannel(row);
    }} />}
  </div>;
}
const root = typeof document === "undefined" ? null : document.getElementById("root");
if (root) createRoot(root).render(<StrictMode><PersonalRolesFixture /></StrictMode>);
