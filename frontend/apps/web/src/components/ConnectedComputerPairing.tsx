import { createRequester, useComputerSetup, useMachines } from "@agenteschat/client";
import { can } from "@agenteschat/client/lib/permissions";
import { Button, Card } from "@agenteschat/ui";
import { useEffect, useRef, useState } from "react";
import { useOrg } from "./org-context";

type Pairing = { pairing_id: string; code: string; expires_at: number; home_instance_id: string };
const request = createRequester();

export function ConnectedComputerPairing() {
  const { org, me } = useOrg();
  const owner = org.role === "owner";
  const setup = useComputerSetup(org.id);
  const available = owner || setup.data?.memberPairing === true;
  const canPair = owner || can(org, "pair_computers");
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [legacy, setLegacy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const machines = useMachines(org.id, available, pairing ? 3000 : false);
  const active = useRef<AbortController | null>(null);
  const base = `/api/app/organizations/${encodeURIComponent(org.id)}`;
  useEffect(() => {
    setPairing(null);
    setLegacy(false);
    setError("");
    setBusy(false);
    return () => active.current?.abort();
  }, [org.id, me.user.id]);
  useEffect(() => {
    if (!pairing) return;
    const timer = setTimeout(() => setPairing(null), Math.max(0, pairing.expires_at * 1000 - Date.now()));
    return () => clearTimeout(timer);
  }, [pairing]);
  if (!available) return null;

  async function act(operation: (signal: AbortSignal) => Promise<void>) {
    if (active.current) active.current.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      await operation(controller.signal);
    } catch {
      if (!controller.signal.aborted) setError("请求未完成，请确认已认领这个家、使用安全连接，并刷新后重试。");
    } finally {
      if (active.current === controller) {
        active.current = null;
        setBusy(false);
      }
    }
  }

  return <Card>
    <h2>连接的电脑</h2>
    {canPair && <p>生成配对码后，到目标电脑的 VortoOne Dev 菜单「连接的电脑…」输入。</p>}
    {!owner && <p className="small muted">只有你能看到和管理这里的电脑。当前可查看设备连接状态。</p>}
    {!canPair && <p className="small muted">你当前的身份组无法生成配对码。</p>}
    {owner && <>
      <label><input type="checkbox" checked={legacy} disabled={busy || !!pairing} onChange={(event) => setLegacy(event.target.checked)} /> 这是仍由我管理、仅使用所有者密钥登录的旧家</label>
      <p className="small muted">首次安装请先完成所有者认领。旧家需要上面的明确确认。</p>
    </>}
    <div className="row">
      {canPair && <Button disabled={busy} onClick={() => void act(async (signal) => {
        const result = await request<Pairing>(`${base}/computer-pairings`, { json: { legacy_confirmed: owner && legacy }, signal });
        if (!signal.aborted) setPairing(result);
      })}>生成电脑配对码</Button>}
      <Button disabled={busy} onClick={() => void machines.refetch()}>刷新连接状态</Button>
    </div>
    {pairing && <div role="status">
      <p>配对码：<strong>{pairing.code.match(/.{1,4}/g)?.join(" ")}</strong></p>
      <p className="small muted">5 分钟内有效、只能使用一次。重新生成会立即使旧码失效；请只在你核对过身份的本机窗口输入。</p>
      <p className="small muted">家身份：<code>{pairing.home_instance_id}</code></p>
      <Button disabled={busy} onClick={() => void act(async (signal) => {
        await request<void>(`${base}/computer-pairings/${encodeURIComponent(pairing.pairing_id)}`, { method: "DELETE", signal });
        if (!signal.aborted) setPairing(null);
      })}>作废这个配对码</Button>
    </div>}
    {error && <p role="alert">{error}</p>}
    {machines.isError && <p role="alert">连接列表读取失败，请刷新重试。</p>}
    <ul>{(machines.data ?? []).map((machine) => <li key={machine.id}>
      <strong>{machine.name}</strong> · {machine.online ? "已连接" : "未连接"} · 执行能力未就绪{" "}
      <Button size="sm" disabled={busy} onClick={() => {
        if (!window.confirm(`移除“${machine.name}”？此授权立即失效，重新使用需要在目标电脑上再次配对。`)) return;
        void act(async (signal) => {
          await request<void>(`${base}/machines/${encodeURIComponent(machine.id)}`, { method: "DELETE", signal });
          if (!signal.aborted) await machines.refetch();
        });
      }}>移除</Button>
    </li>)}</ul>
  </Card>;
}
