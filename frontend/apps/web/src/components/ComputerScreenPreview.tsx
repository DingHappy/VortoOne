import { Button } from "@agenteschat/ui";
import { useEffect, useState } from "react";
import { watchPrivateScreen } from "../screen/private-browser-preview";

export function ComputerScreenPreview({ orgId, machineId, name, close }: {
  orgId: string;
  machineId: string;
  name: string;
  close: () => void;
}) {
  const [image, setImage] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    let current: string | null = null;
    function discard() {
      if (current) URL.revokeObjectURL(current);
      current = null;
    }

    function end() {
      discard();
      setImage(null);
      setEnded(true);
    }

    let stop: (() => void) | undefined;
    try {
      stop = watchPrivateScreen({
        origin: window.location.origin,
        orgId,
        machineId,
        onFrame(frame) {
          discard();
          current = URL.createObjectURL(frame);
          setImage(current);
        },
        onEnd: end,
      });
    } catch {
      end();
    }

    return () => {
      stop?.();
      discard();
    };
  }, [orgId, machineId]);

  return <section className="stack">
    <p>正在查看 {name} 的专用浏览器</p>
    {image ? <img src={image} alt="专用浏览器当前画面" style={{ maxWidth: "100%" }} /> :
      <p role="status">{ended ? "浏览器分享已结束" : "等待浏览器画面…"}</p>}
    <Button size="sm" onClick={close}>{ended ? "关闭" : "停止查看"}</Button>
  </section>;
}
