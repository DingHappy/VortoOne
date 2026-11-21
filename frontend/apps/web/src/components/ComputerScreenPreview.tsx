import { Button } from "@vortoone/ui";
import { type FormEvent, type PointerEvent, useEffect, useRef, useState } from "react";
import { type DesktopWatch, type PrivatePreviewKind, INPUT_TEXT_BYTES, watchControllableDesktop, watchPairedRunDisplay, watchPrivateScreen } from "../screen/private-browser-preview";

export function ComputerScreenPreview({ orgId, machineId, name, kind, allowControl, agentRun, close }: {
  orgId: string;
  machineId: string;
  name: string;
  kind: PrivatePreviewKind;
  allowControl: boolean;
  agentRun?: { agentId: string; runId: string };
  close: () => void;
}) {
  const [image, setImage] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [frameReady, setFrameReady] = useState(false);
  const [loaded, setLoaded] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const control = useRef<DesktopWatch | null>(null);
  const latestImage = useRef<string | null>(null);
  const text = useRef<HTMLInputElement>(null);
  const screen = kind === "desktop" ? "桌面" : "浏览器";
  const agentId = agentRun?.agentId;
  const runId = agentRun?.runId;
  const canControl = allowControl && !agentRun && kind === "desktop";
  const canAct = Boolean(frameReady && image && image === loaded);

  useEffect(() => {
    const discard = () => {
      if (latestImage.current) URL.revokeObjectURL(latestImage.current);
      latestImage.current = null;
    };
    const end = () => {
      discard();
      setImage(null);
      setLoaded(null);
      setFrameReady(false);
      setControlling(false);
      setNotice("");
      setEnded(true);
      if (text.current) text.current.value = "";
    };
    const onHidden = () => {
      if (!document.hidden) return;
      control.current?.stop();
      end();
    };
    const options = {
      origin: window.location.origin, orgId, machineId,
      onFrame(frame: Blob) {
        discard();
        const next = URL.createObjectURL(frame);
        latestImage.current = next;
        setImage(next);
      },
      onEnd: end,
    };
    let stop: (() => void) | undefined;
    try {
      if (canControl) {
        const watch = watchControllableDesktop({
          ...options,
          onControl() {
            setControlling(true);
            setNotice("");
          },
          onFrameReady: setFrameReady,
          onInputUnavailable: () => setNotice("当前画面或输入位置已变化，确认下一帧后再操作。"),
          onControlBusy: () => setNotice("本机任务浏览器正在完成当前操作，请稍后再点「接管桌面」。"),
        });
        control.current = watch;
        stop = watch.stop;
        document.addEventListener("visibilitychange", onHidden);
      } else if (agentId !== undefined && runId !== undefined) {
        stop = watchPairedRunDisplay({ ...options, agentId, runId });
      } else {
        stop = watchPrivateScreen({ ...options, kind });
      }
    } catch {
      end();
    }
    return () => {
      stop?.();
      control.current = null;
      document.removeEventListener("visibilitychange", onHidden);
      discard();
    };
  }, [orgId, machineId, kind, canControl, agentId, runId]);

  function takeControl() {
    if (canAct && loaded === latestImage.current) control.current?.takeControl();
  }

  function clickFrame(event: PointerEvent<HTMLImageElement>) {
    if (!controlling || !canAct || loaded !== latestImage.current || event.currentTarget.currentSrc !== loaded ||
        !event.isTrusted || !event.isPrimary || event.button !== 0 || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    if (!(x >= 0 && x < 1 && y >= 0 && y < 1)) return;
    if (control.current?.sendInput({ kind: "click", x: Math.floor(x * 10_000), y: Math.floor(y * 10_000) })) {
      setNotice("");
    }
  }

  function sendText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = text.current;
    if (!input || !event.isTrusted || !controlling || !canAct || loaded !== latestImage.current) return;
    const value = input.value;
    if (!value || new TextEncoder().encode(value).length > INPUT_TEXT_BYTES ||
        Array.from(value).some((char) => char !== " " && /[\p{C}\p{Z}]/u.test(char))) {
      setNotice("请输入少量可见文字。");
      return;
    }
    if (control.current?.sendInput({ kind: "text", text: value })) {
      input.value = "";
      setNotice("");
    }
  }

  return <section aria-label={`${name}的${screen}画面`}>
    <div className="row">
      <strong>{name} · {controlling ? "桌面接管中" : `只读${screen}画面`}</strong>
      {canControl && !controlling && !ended && <Button size="sm" disabled={!canAct} onClick={(event) => {
        if (event.isTrusted) takeControl();
      }}>接管桌面</Button>}
      <Button size="sm" onClick={close}>{ended ? "关闭" : controlling ? "交还控制并结束分享" : "停止查看"}</Button>
    </div>
    {image && <img src={image} alt={`${name}的${screen}画面`} draggable={false}
      onLoad={(event) => {
        if (event.currentTarget.currentSrc === latestImage.current) setLoaded(latestImage.current);
      }} onPointerDown={controlling ? clickFrame : undefined}
      style={{ display: "block", width: "auto", maxWidth: "100%", cursor: controlling ? "crosshair" : undefined }} />}
    {controlling && <form className="row" onSubmit={sendText}>
      <label>普通文字 <input ref={text} type="text" maxLength={INPUT_TEXT_BYTES} autoComplete="off" spellCheck={false} /></label>
      <Button type="submit" size="sm" disabled={!canAct}>发送到当前输入框</Button>
    </form>}
    {notice && <p className="small muted" role="status">{notice}</p>}
    {!image && <p className="small muted" role="status">
      {ended ? "分享已结束或暂不可用。可以在目标电脑的 VortoOne Dev 中开启分享后重新查看。" : `正在连接已分享的${screen}…`}
    </p>}
  </section>;
}
