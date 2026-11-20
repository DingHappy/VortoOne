import { sealSecret } from "@agenteschat/client/lib/secret-box";

export const MAX_PNG_BYTES = 768 * 1024;
export const INPUT_TEXT_BYTES = 96;
export const LOGIN_VALUE_BYTES = 256;
const REPLY_TIMEOUT = 8000;

export type BrowserInput =
  | { kind: "click"; x: number; y: number }
  | { kind: "scroll"; delta: number }
  | { kind: "text"; text: string }
  | { kind: "backspace" };

export interface LoginFormOffer {
  offer: string;
  public_key: string;
  kind: "password" | "code";
  username: boolean;
  expires_in: number;
}

export type BrowserWatchEnd = "paused" | "ended" | "login_ready";
type LoginFields = { username?: string; password: string } | { code: string };

interface PreviewOptions {
  origin: string;
  orgId: string;
  onFrame: (frame: Blob) => void;
  onEnd: (reason: BrowserWatchEnd) => void;
  createSocket?: (url: string) => WebSocket;
}

interface ControlOptions {
  onControl: () => void;
  onFrameReady: (ready: boolean) => void;
  onLoginForm?: (form: LoginFormOffer) => void;
  onLoginWaiting?: () => void;
}

export interface BrowserWatch {
  stop: () => void;
  takeControl: () => boolean;
  sendInput: (action: BrowserInput) => boolean;
  submitLogin: (offer: string, siteOrigin: string, fields: LoginFields) => Promise<boolean>;
}

export function watchPrivateScreen({ machineId, ...options }: PreviewOptions & { machineId: string }): () => void {
  return watchBrowserFrames(options, "machines/" + encodeURIComponent(machineId) + "/browser-preview").stop;
}

export function watchHandoffBrowser({ approvalId, ...options }: PreviewOptions & { approvalId: string }): () => void {
  return watchBrowserFrames(options, `approvals/${encodeURIComponent(approvalId)}/browser-preview/watch`).stop;
}

export function watchControllableBrowser({
  approvalId, onControl, onFrameReady, onLoginForm, onLoginWaiting, ...options
}: PreviewOptions & ControlOptions & { approvalId: string }): BrowserWatch {
  return watchBrowserFrames(options, `approvals/${encodeURIComponent(approvalId)}/browser-preview/watch`, {
    onControl, onFrameReady, onLoginForm, onLoginWaiting,
  });
}

/** Pull one PNG at a time while this viewer is mounted. A closed session is never retried. */
function watchBrowserFrames(options: PreviewOptions, path: string, controls?: ControlOptions): BrowserWatch {
  const { origin, orgId, onFrame, onEnd, createSocket = (url) => new WebSocket(url) } = options;
  const route = `/api/app/organizations/${encodeURIComponent(orgId)}/${path}`;
  const url = new URL(route, origin);
  if (url.protocol === "https:") {
    url.protocol = "wss:";
  } else if (url.protocol === "http:" && ["127.0.0.1", "[::1]", "localhost"].includes(url.hostname)) {
    url.protocol = "ws:";
  } else {
    throw new Error("Screen previews require a secure origin");
  }
  const socket = createSocket(url.href);
  socket.binaryType = "blob";
  let timer: ReturnType<typeof setTimeout>;
  let minFrameMs = 0;
  let awaiting: "frame" | "control" | "input" | "login" | null = null;
  let closed = false;
  let frameReady = false;
  let controlling = false;
  let loginForm: LoginFormOffer | null = null;
  let loginEntered = false;

  function stop() {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    socket.onmessage = socket.onclose = socket.onerror = null;
    socket.close();
  }

  function end(reason: BrowserWatchEnd = "ended") {
    if (closed) return;
    stop();
    onEnd(reason);
  }

  function request(expected: NonNullable<typeof awaiting>, message: object) {
    if (closed || awaiting) return false;
    clearTimeout(timer);
    awaiting = expected;
    frameReady = false;
    controls?.onFrameReady(false);
    timer = setTimeout(end, REPLY_TIMEOUT);
    try {
      socket.send(JSON.stringify(message));
      return true;
    } catch {
      end();
      return false;
    }
  }

  function pull() {
    request("frame", { type: "next" });
  }

  timer = setTimeout(end, REPLY_TIMEOUT);
  socket.onclose = socket.onerror = () => end();
  socket.onmessage = ({ data }) => {
    if (typeof data === "string") {
      try {
        const control = data.length <= 512 && JSON.parse(data);
        if (minFrameMs && awaiting && control?.type === "paused" && Object.keys(control).length === 1) {
          return end("paused");
        }
        if (minFrameMs && awaiting === "frame" && controls?.onLoginForm && control?.type === "login_ready" &&
            Object.keys(control).length === 1) {
          return end("login_ready");
        }
        if (minFrameMs && controls?.onLoginForm && control && Object.keys(control).length === 1 &&
            ((awaiting === "login" && control.type === "login_submitted") ||
             (awaiting === "frame" && control.type === "login_waiting"))) {
          clearTimeout(timer);
          awaiting = null;
          loginForm = null;
          loginEntered = true;
          controls.onLoginWaiting?.();
          if (!closed) timer = setTimeout(pull, minFrameMs);
          return;
        }
        if (minFrameMs && awaiting === "frame" && controls?.onLoginForm && control?.type === "login_form" &&
            Object.keys(control).length === 6 && typeof control.offer === "string" && /^[a-f0-9]{32}$/.test(control.offer) &&
            typeof control.public_key === "string" && /^B[A-Za-z0-9+/]{86}=$/.test(control.public_key) &&
            (control.kind === "password" || (control.kind === "code" && control.username === false)) &&
            typeof control.username === "boolean" && control.expires_in === 90) {
          clearTimeout(timer);
          awaiting = null;
          frameReady = true;
          loginForm = control;
          if (control.kind === "code") loginEntered = true;
          timer = setTimeout(() => end("paused"), control.expires_in * 1000);
          controls.onLoginForm(control);
          controls.onFrameReady(true);
          return;
        }
        if (minFrameMs && awaiting && controls && control && Object.keys(control).length === 1 &&
            ((awaiting === "control" && control.type === "control_ready") ||
             (awaiting === "input" && control.type === "input_applied"))) {
          if (control.type === "control_ready") {
            controlling = true;
            controls.onControl();
          }
          clearTimeout(timer);
          awaiting = null;
          if (!closed) timer = setTimeout(pull, minFrameMs);
          return;
        }
        if (minFrameMs || control?.type !== "ready" || Object.keys(control).length !== 2 ||
            !Number.isInteger(control.min_frame_ms) || control.min_frame_ms < 500 || control.min_frame_ms > 3000) {
          return end();
        }
        minFrameMs = control.min_frame_ms;
      } catch {
        return end();
      }
      clearTimeout(timer);
      pull();
      return;
    }
    if (loginEntered || awaiting !== "frame" || !(data instanceof Blob) || data.size < 45 || data.size > MAX_PNG_BYTES) {
      return end();
    }
    clearTimeout(timer);
    awaiting = null;
    loginForm = null;
    frameReady = true;
    onFrame(data.slice(0, data.size, "image/png"));
    if (!closed) {
      controls?.onFrameReady(true);
      if (!awaiting) timer = setTimeout(pull, minFrameMs);
    }
  };
  return {
    stop,
    takeControl: () => Boolean(controls && frameReady && !controlling && request("control", { type: "take_control" })),
    sendInput: (action) => Boolean(controls && frameReady && controlling && !loginForm && request("input", { type: "input", action })),
    async submitLogin(offer, siteOrigin, fields) {
      const form = loginForm;
      if (!form || form.offer !== offer || closed || awaiting || !controlling || !frameReady) return false;
      if ("code" in fields) {
        if (form.kind !== "code" || !/^[0-9]{4,12}$/.test(fields.code)) return false;
      } else if (form.kind !== "password" || form.username !== (typeof fields.username === "string")) {
        return false;
      }
      try {
        const url = new URL(siteOrigin);
        if (url.protocol !== "https:" || url.origin !== siteOrigin) return false;
        const data = Object.values(fields);
        const encoder = new TextEncoder();
        if (!data.every(value => value.length > 0 && !/[\r\n\0]/.test(value) && encoder.encode(value).length <= LOGIN_VALUE_BYTES)) {
          return false;
        }
        const box = await sealSecret(form.public_key, JSON.stringify(fields), `vortoone-browser-login|v1|${offer}|${siteOrigin}`);
        return !closed && loginForm === form && request("login", { type: "submit_login", offer, box });
      } catch {
        return false;
      }
    },
  };
}
