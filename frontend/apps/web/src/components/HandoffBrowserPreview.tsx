import { Button } from "@vortoone/ui";
import { type FormEvent, type MouseEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  type BrowserWatch, type BrowserWatchEnd, type LoginFormOffer, INPUT_TEXT_BYTES, LOGIN_VALUE_BYTES,
  MAX_PNG_BYTES, watchControllableBrowser, watchHandoffBrowser,
} from "../screen/private-browser-preview";

const PAUSED = "当前画面暂不可查看。可在运行端完成登录或验证后重新开启。";
const ENDED = "查看已结束。若原回合仍在等待，可以重新开启。";

export function HandoffBrowserPreview({ orgId, approvalId, livePreview, allowControl, siteOrigin }: {
  orgId: string;
  approvalId: string;
  livePreview?: boolean;
  allowControl?: boolean;
  siteOrigin?: string;
}) {
  const [image, setImage] = useState<string | null>(null);
  const [mode, setMode] = useState<"idle" | "capture" | "watch">("idle");
  const [message, setMessage] = useState("");
  const [frameReady, setFrameReady] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [text, setText] = useState("");
  const [loginForm, setLoginForm] = useState<LoginFormOffer | null>(null);
  const [loginSending, setLoginSending] = useState(false);
  const username = useRef<HTMLInputElement>(null);
  const secret = useRef<HTMLInputElement>(null);
  const source = useRef<string | null>(null);
  const release = useRef<(() => void) | null>(null);
  const controls = useRef<BrowserWatch | null>(null);

  const discard = useCallback(() => {
    if (source.current) URL.revokeObjectURL(source.current);
    source.current = null;
  }, []);

  function show(frame: Blob) {
    discard();
    setLoginForm(null);
    setMessage("");
    setImageLoaded(false);
    source.current = URL.createObjectURL(frame);
    setImage(source.current);
  }

  const dispose = useCallback(() => {
    release.current?.();
    release.current = null;
    controls.current = null;
    discard();
  }, [discard]);

  const stop = useCallback(() => {
    dispose();
    setImage(null);
    setMode("idle");
    setMessage("");
    setFrameReady(false);
    setImageLoaded(false);
    setControlling(false);
    setText("");
    setLoginForm(null);
    setLoginSending(false);
  }, [dispose]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) stop();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      dispose();
    };
  }, [dispose, stop]);

  function startWatching() {
    if (release.current || document.hidden) return;
    setMode("watch");
    setMessage("");
    try {
      const options = {
        origin: window.location.origin, orgId, approvalId,
        onFrame: show,
        onEnd(reason: BrowserWatchEnd) {
          const hadControls = controls.current !== null;
          stop();
          setMessage(reason === "login_ready" ? "配对浏览器已返回本站，核对后可继续原任务。"
            : reason === "paused" && hadControls
            ? "页面操作已暂停。请在配对电脑上核对当前页面和操作结果。"
            : reason === "paused" ? PAUSED : ENDED);
        },
      };
      if (allowControl) {
        controls.current = watchControllableBrowser({
          ...options, onControl: () => setControlling(true), onFrameReady: setFrameReady,
          onLoginForm: siteOrigin ? (form) => {
            discard();
            setImage(null);
            setImageLoaded(false);
            setText("");
            setMessage("");
            setLoginSending(false);
            setLoginForm(form);
          } : undefined,
          onLoginWaiting: () => {
            setLoginForm(null);
            setLoginSending(false);
            setMessage("正在等待配对浏览器加载下一步…");
          },
        });
        release.current = controls.current.stop;
      } else {
        release.current = watchHandoffBrowser(options);
      }
    } catch {
      stop();
      setMessage(ENDED);
    }
  }

  async function capture() {
    if (release.current || document.hidden) return;
    const controller = new AbortController();
    release.current = () => controller.abort();
    setMode("capture");
    setMessage("");
    discard();
    setImage(null);
    try {
      const route = `/api/app/organizations/${encodeURIComponent(orgId)}/approvals/${encodeURIComponent(approvalId)}/browser-preview`;
      const response = await fetch(route, {
        method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (!response.ok || response.headers.get("content-type") !== "image/png") {
        setMessage(response.status === 409 ? PAUSED : ENDED);
        return;
      }
      const frame = await response.blob();
      if (controller.signal.aborted) return;
      if (frame.size < 45 || frame.size > MAX_PNG_BYTES) {
        setMessage("无法显示这张画面。");
        return;
      }
      show(frame);
    } catch {
      if (!controller.signal.aborted) setMessage("无法取得当前画面，请重试。");
    } finally {
      if (!controller.signal.aborted) {
        release.current = null;
        setMode("idle");
      }
    }
  }

  function clickPage(event: MouseEvent<HTMLImageElement>) {
    if (!controlling || !imageLoaded || event.button !== 0 || !event.isTrusted) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = Math.floor((event.clientX - bounds.left) * 10000 / bounds.width);
    const y = Math.floor((event.clientY - bounds.top) * 10000 / bounds.height);
    if (x >= 0 && x < 10000 && y >= 0 && y < 10000) controls.current?.sendInput({ kind: "click", x, y });
  }

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const watch = controls.current;
    const form = loginForm;
    if (!event.isTrusted || !controlling || !frameReady || loginSending || !watch || !form || !siteOrigin || !secret.current) return;
    const value = secret.current.value;
    const fields = form.kind === "code" ? { code: value }
      : form.username ? { username: username.current?.value ?? "", password: value } : { password: value };
    event.currentTarget.reset();
    setLoginSending(true);
    if (!await watch.submitLogin(form.offer, siteOrigin, fields) && controls.current === watch) {
      stop();
      setMessage("登录表单未发送。请重新打开画面并核对站点。");
    }
  }

  const canOperate = frameReady && imageLoaded;
  const canSendText = canOperate && text.length > 0 && new TextEncoder().encode(text).length <= INPUT_TEXT_BYTES;
  return <section aria-label="当前回合的浏览器画面">
    {livePreview ? (
      <Button size="sm" onClick={mode === "watch" ? stop : startWatching}>
        {controlling ? "交还页面控制" : mode === "watch" ? "停止查看" : "开始查看画面"}
      </Button>
    ) : (
      <Button size="sm" disabled={mode === "capture"} onClick={() => void capture()}>
        {image ? "更新画面" : "查看当前画面"}
      </Button>
    )}
    {allowControl && mode === "watch" && !controlling && (
      <Button size="sm" disabled={!canOperate && !(frameReady && loginForm)} onClick={(event) => {
        if (event.isTrusted) controls.current?.takeControl();
      }}>接管页面</Button>
    )}
    {!livePreview && image && <Button size="sm" onClick={stop}>收起画面</Button>}
    {image && <img src={image} alt="当前回合的专用浏览器画面" draggable={false} onClick={clickPage}
      onLoad={(event) => { if (source.current === event.currentTarget.currentSrc) setImageLoaded(true); }}
      style={{ display: "block", maxWidth: "100%", height: "auto", cursor: canOperate && controlling ? "pointer" : undefined }} />}
    {loginForm && siteOrigin && <div>
      <p className="small muted" role="status">{siteOrigin} {loginForm.kind === "code" ? "请求登录验证码。" : "请求登录。"}</p>
      {controlling && <form key={loginForm.offer} className="stack-4" onSubmit={(event) => void submitLogin(event)} autoComplete="off">
        {loginForm.username && <label>账号 <input ref={username} required maxLength={LOGIN_VALUE_BYTES}
          autoComplete="off" autoCapitalize="none" spellCheck={false} /></label>}
        {loginForm.kind === "code" ? <label>验证码 <input ref={secret} inputMode="numeric" required
          pattern="[0-9]{4,12}" maxLength={12} autoComplete="off" spellCheck={false} /></label>
          : <label>密码 <input ref={secret} type="password" required maxLength={LOGIN_VALUE_BYTES}
            autoComplete="new-password" /></label>}
        <Button type="submit" size="sm" disabled={!frameReady || loginSending}>
          {loginSending ? "正在提交…" : loginForm.kind === "code" ? "提交验证码" : "提交本站登录"}
        </Button>
      </form>}
    </div>}
    {controlling && !loginForm && !message && <>
      <p className="small muted" role="status">接管中。点击画面可点选页面，点击普通输入框后可发送文字。</p>
      <div className="row">
        <Button size="sm" disabled={!canOperate} onClick={(event) => {
          if (event.isTrusted) controls.current?.sendInput({ kind: "scroll", delta: -480 });
        }}>向上滚动</Button>
        <Button size="sm" disabled={!canOperate} onClick={(event) => {
          if (event.isTrusted) controls.current?.sendInput({ kind: "scroll", delta: 480 });
        }}>向下滚动</Button>
        <Button size="sm" disabled={!canOperate} onClick={(event) => {
          if (event.isTrusted) controls.current?.sendInput({ kind: "backspace" });
        }}>退格</Button>
        <input aria-label="发送到页面的普通文字" autoComplete="off" spellCheck={false} value={text} maxLength={INPUT_TEXT_BYTES}
          onChange={(event) => setText(event.currentTarget.value)} />
        <Button size="sm" disabled={!canSendText} onClick={(event) => {
          if (event.isTrusted && controls.current?.sendInput({ kind: "text", text })) setText("");
        }}>发送文字</Button>
      </div>
    </>}
    {(message || (mode === "watch" && !image && !loginForm)) && (
      <p role="status" className="small muted">{message || "正在等待当前回合的画面…"}</p>
    )}
  </section>;
}
