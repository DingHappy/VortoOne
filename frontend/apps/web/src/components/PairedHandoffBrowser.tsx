import { useMachines } from "@agenteschat/client";
import { Button } from "@agenteschat/ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { HandoffBrowserPreview } from "./HandoffBrowserPreview";

function route(orgId: string, approvalId: string) {
  return `/api/app/organizations/${encodeURIComponent(orgId)}/approvals/${encodeURIComponent(approvalId)}/paired-browser`;
}

async function requestPairedBrowser(orgId: string, approvalId: string, body: { machineId?: string }, signal: AbortSignal) {
  const response = await fetch(route(orgId, approvalId), {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.error?.message || "暂时无法连接任务浏览器。");
  return data;
}

export function usePairedHandoffBrowser(orgId: string, approvalId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["paired-handoff-browser", orgId, approvalId],
    queryFn: async ({ signal }) => {
      const data = await requestPairedBrowser(orgId, approvalId, {}, signal);
      return { sharing: data.sharing === true, canControl: data.canControl === true, controlling: data.controlling === true };
    },
    enabled,
    retry: false,
    refetchInterval: 1500,
  });
}

export function PairedHandoffBrowser({ orgId, approvalId, sharing, canControl, siteOrigin }: {
  orgId: string;
  approvalId: string;
  sharing: boolean;
  canControl: boolean;
  siteOrigin: string;
}) {
  const machines = useMachines(orgId, !sharing, 3000);
  const online = machines.data?.filter((machine) => machine.online) ?? [];
  const [selected, setSelected] = useState("");
  const [invite, setInvite] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const inflight = useRef<AbortController | null>(null);

  useEffect(() => () => inflight.current?.abort(), []);
  useEffect(() => {
    if (sharing) setInvite(null);
  }, [sharing]);

  async function openInvitation() {
    if (inflight.current || !online.some((machine) => machine.id === selected)) return;
    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    setInvite(null);
    setMessage("");
    try {
      const data = await requestPairedBrowser(orgId, approvalId, { machineId: selected }, controller.signal);
      if (!/^[0-9a-f]{32}$/.test(data.code)) throw new Error("未收到可用的任务码。");
      if (!controller.signal.aborted) setInvite(data.code);
    } catch (error) {
      if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : "无法生成任务码。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      inflight.current = null;
    }
  }

  if (sharing) {
    return <HandoffBrowserPreview orgId={orgId} approvalId={approvalId} livePreview allowControl={canControl} siteOrigin={siteOrigin} />;
  }
  return <section aria-label="为配对电脑打开任务浏览器">
    {online.length ? <>
      <label>
        <span>选择配对电脑 </span>
        <select value={selected} disabled={busy} onChange={(event) => {
          setSelected(event.currentTarget.value);
          setInvite(null);
          setMessage("");
        }}>
          <option value="">选择在线电脑</option>
          {online.map((machine) => <option key={machine.id} value={machine.id}>{machine.name}</option>)}
        </select>
      </label>
      <Button size="sm" disabled={busy || !online.some((machine) => machine.id === selected)}
        onClick={() => void openInvitation()}>{invite ? "重新生成任务码" : "生成任务码"}</Button>
    </> : (
      <p className="small muted" role="status">
        {machines.isPending ? "正在读取配对电脑…" : "当前没有在线的配对电脑。"}
      </p>
    )}
    {invite && <p>
      在所选电脑的「连接的电脑 → 打开任务浏览器」输入
      {" "}<code>{invite}</code>{" "}，然后在专用窗口打开上方网站。任务码最长有效两分钟。
    </p>}
    {message && <p className="small muted" role="status">{message}</p>}
  </section>;
}
