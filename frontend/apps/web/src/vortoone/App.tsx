import {
  Avatar,
  Button,
  Card,
  Composer,
  EmptyState,
  Field,
  Message,
  MessageText,
  PageHeader,
} from "@vortoone/ui";
import type { AgentAppearance } from "@vortoone/ui";
import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
} from "react";
import { useTheme } from "../lib/theme";
import { Markdown } from "./Markdown";
import {
  request,
  VortoRuntime,
  type RecordItem,
  type Session,
} from "./runtime";

type View = "chat" | "goals" | "tasks" | "memories" | "schedule" | "settings";
const PAGES: { id: View; label: string; mark: string }[] = [
  { id: "chat", label: "聊天", mark: "◌" },
  { id: "goals", label: "目标", mark: "◎" },
  { id: "tasks", label: "任务", mark: "☷" },
  { id: "memories", label: "记忆", mark: "◇" },
  { id: "schedule", label: "日程", mark: "▦" },
  { id: "settings", label: "设置", mark: "⚙" },
];
const ROLES: { name: string; color: AgentAppearance["color"] }[] = [
  { name: "总助手", color: "rose" },
  { name: "研究助手", color: "violet" },
  { name: "开发助手", color: "green" },
  { name: "日程助手", color: "amber" },
];
const CONNECTION = {
  disconnected: "未连接",
  connecting: "正在连接",
  connected: "已连接",
  reconnecting: "正在重连",
  incompatible: "版本不兼容",
};
const STATUS: Record<string, string> = {
  running: "进行中",
  working: "进行中",
  queued: "排队中",
  pending: "待处理",
  idle: "空闲",
  inactive: "已保存",
  needs_input: "等待确认",
  failed: "失败",
  blocked: "受阻",
  done: "执行结束",
  completed: "执行结束",
  succeeded: "已执行",
  cancelled: "已停止",
  active: "推进中",
  draft: "草稿",
  achieved: "已达成",
  paused: "已暂停",
  unknown: "待核对",
};
const status = (value: unknown) =>
  typeof value === "string" ? (STATUS[value] ?? value) : "待核对";
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "请求未成功，请重试。";
const stored = (key: string) => {
  try {
    return localStorage.getItem(`vortoone.${key}`) ?? "";
  } catch {
    return "";
  }
};
const save = (key: string, value: string) => {
  try {
    localStorage.setItem(`vortoone.${key}`, value);
  } catch {
    /* Private browsing can disable storage. */
  }
};
const text = (item: RecordItem, ...keys: string[]) => {
  for (const key of keys)
    if (typeof item[key] === "string" && item[key]) return item[key] as string;
  return "";
};

function useRead<T>(path: string | null) {
  const [value, setValue] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!!path);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    setValue(undefined);
    setError("");
    setLoading(!!path);
    if (!path) return;
    const controller = new AbortController();
    request<T>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setValue(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorText(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, version]);
  return { value, error, loading, refresh: () => setVersion((v) => v + 1) };
}

export function App() {
  const [runtime] = useState(() => new VortoRuntime());
  const state = useSyncExternalStore(runtime.subscribe, runtime.getSnapshot);
  const [view, setView] = useState<View>("chat");
  const [auth, setAuth] = useState<{
    auth_required: boolean;
    authed: boolean;
  } | null>(null);
  const [authError, setAuthError] = useState("");
  const [boot, setBoot] = useState(0);
  const [token, setToken] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionsError, setSessionsError] = useState("");
  const [profile, setProfile] = useState<RecordItem>({});
  const [draft, setDraft] = useState("");
  const draftRef = useRef("");
  const [mode, setMode] = useState<"plan" | "build">("plan");
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState("");
  const [deciding, setDeciding] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [roleColor, setRoleColor] = useState<AgentAppearance["color"]>(
    () => ROLES.find((r) => r.color === stored("character"))?.color ?? "rose",
  );
  const { resolved, preference, setPreference } = useTheme();
  const appearance: AgentAppearance = { color: roleColor };
  const name = text(profile, "name") || "VortoOne";
  const authenticated = auth !== null && (!auth.auth_required || auth.authed);
  const connected = state.connection === "connected";
  const scroll = useRef<HTMLDivElement>(null);
  const follows = useRef(true);

  useEffect(() => {
    const controller = new AbortController();
    setAuthError("");
    setAuth(null);
    request<{ auth_required: boolean; authed: boolean }>("/api/auth/status", {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          if (
            typeof value.auth_required !== "boolean" ||
            typeof value.authed !== "boolean"
          )
            throw new Error("运行端的登录响应无法识别");
          setAuth(value);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setAuthError(errorText(error));
      });
    return () => controller.abort();
  }, [boot]);

  useEffect(() => {
    if (!authenticated) return;
    const controller = new AbortController();
    request<{ sid: string | null }>("/api/assistant/main-chat", {
      signal: controller.signal,
    })
      .catch(() => ({ sid: null }))
      .then((main) => {
        if (controller.signal.aborted) return;
        const saved = stored("sid");
        const sid = /^[A-Za-z0-9_-]{1,64}$/.test(saved)
          ? saved
          : main.sid && /^[A-Za-z0-9_-]{1,64}$/.test(main.sid)
            ? main.sid
            : crypto.randomUUID();
        runtime.connect(sid);
        save("sid", sid);
      });
    request<RecordItem>("/api/assistant/profile", { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setProfile(value);
      })
      .catch(() => {});
    return () => {
      controller.abort();
      runtime.disconnect();
    };
  }, [authenticated, runtime]);

  useEffect(() => {
    if (!authenticated) return;
    const controller = new AbortController();
    let reading = false;
    const refresh = async () => {
      if (reading) return;
      reading = true;
      try {
        const result = await request<{ sessions: Session[] }>(
          "/api/agent/sessions",
          { signal: controller.signal },
        );
        if (!Array.isArray(result.sessions))
          throw new Error("会话列表响应无法识别");
        if (!controller.signal.aborted) {
          setSessions(result.sessions);
          setSessionsError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) setSessionsError(errorText(error));
      } finally {
        reading = false;
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 15_000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [authenticated, state.sid, state.chat.busy]);

  useEffect(() => {
    let value = "";
    try {
      value = sessionStorage.getItem(`vortoone.draft.${state.sid}`) ?? "";
    } catch {
      /* No persistent drafts in private mode. */
    }
    setDraft(value);
    draftRef.current = value;
    setNotice("");
    follows.current = true;
  }, [state.sid]);
  const changeDraft = (value: string) => {
    setDraft(value);
    draftRef.current = value;
    try {
      sessionStorage.setItem(`vortoone.draft.${state.sid}`, value);
    } catch {
      /* Best effort, never store credentials here. */
    }
  };
  useEffect(() => {
    if (follows.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [
    state.chat.messages,
    state.chat.stream,
    state.chat.confirmations,
    state.chat.activities,
    state.sid,
    view,
  ]);

  const selectSession = (sid: string) => {
    if (sid !== state.sid) {
      runtime.connect(sid);
      save("sid", sid);
    }
    setView("chat");
    setDrawer(false);
  };
  const send = async (value: string) => {
    if (sending || !value.trim()) return;
    const sid = state.sid;
    setSending(true);
    setNotice("");
    try {
      await runtime.sendMessage(value, mode);
      if (runtime.getSnapshot().sid === sid) {
        if (draftRef.current.trim() === value.trim()) changeDraft("");
        follows.current = true;
      }
    } catch (error) {
      if (runtime.getSnapshot().sid === sid) setNotice(errorText(error));
    } finally {
      setSending(false);
    }
  };
  const decide = async (id: string, ok: boolean) => {
    setDeciding(id);
    setNotice("");
    const sid = state.sid;
    try {
      await runtime.decide(id, ok);
    } catch (error) {
      if (runtime.getSnapshot().sid === sid) setNotice(errorText(error));
    } finally {
      setDeciding(null);
    }
  };
  const login = async (event: FormEvent) => {
    event.preventDefault();
    setLoggingIn(true);
    setAuthError("");
    try {
      await request("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      setToken("");
      setBoot((value) => value + 1);
    } catch (error) {
      setAuthError(errorText(error));
    } finally {
      setLoggingIn(false);
    }
  };
  const logout = async () => {
    try {
      await request("/api/auth/logout", { method: "POST" });
      runtime.disconnect();
      setBoot((value) => value + 1);
    } catch (error) {
      setNotice(errorText(error));
    }
  };

  if (!authenticated)
    return (
      <div className="vo-connect">
        <Card padded className="vo-connect__card">
          <Avatar
            kind="agent"
            name="VortoOne"
            size="xl"
            appearance={appearance}
          />
          <h1>VortoOne</h1>
          <p>你的日常助理与工作空间</p>
          {auth?.auth_required ? (
            <form onSubmit={login} className="vo-login">
              <Field label="运行端访问令牌" id="runtime-token">
                <input
                  id="runtime-token"
                  className="ac-input"
                  type="password"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  autoComplete="off"
                  required
                />
              </Field>
              <Button type="submit" variant="primary" disabled={loggingIn}>
                {loggingIn ? "正在登录…" : "连接工作空间"}
              </Button>
            </form>
          ) : (
            !authError && <p role="status">正在连接工作空间…</p>
          )}
          {authError && (
            <div role="alert" className="vo-error">
              <p>
                {authError === "Failed to fetch"
                  ? "暂时无法连接工作空间，请检查运行端是否已启动。"
                  : authError}
              </p>
              <Button onClick={() => setBoot((value) => value + 1)}>
                重新连接
              </Button>
            </div>
          )}
        </Card>
      </div>
    );

  const currentSession = sessions.find((item) => item.sid === state.sid);
  return (
    <div className="vo-shell" data-drawer={drawer || undefined}>
      <aside className="vo-rail" aria-label="工作空间">
        <button
          className="vo-workspace"
          title="VortoOne 工作空间"
          aria-label="VortoOne 工作空间"
          onClick={() => {
            setView("chat");
            setDrawer(true);
          }}
        >
          <Avatar
            kind="agent"
            name="VortoOne"
            size="md"
            appearance={appearance}
          />
        </button>
        <span className="vo-rail__line" />
        <button
          className="vo-rail__home"
          title="聊天"
          aria-label="聊天首页"
          onClick={() => {
            setView("chat");
            setDrawer(true);
          }}
        >
          ⌂
        </button>
        <button
          className="vo-rail__theme"
          title={`切换为${resolved === "dark" ? "浅色" : "深色"}主题`}
          aria-label={`切换为${resolved === "dark" ? "浅色" : "深色"}主题`}
          onClick={() => setPreference(resolved === "dark" ? "light" : "dark")}
        >
          {resolved === "dark" ? "☀" : "☾"}
        </button>
      </aside>
      {drawer && (
        <button
          className="vo-scrim"
          aria-label="关闭导航"
          onClick={() => setDrawer(false)}
        />
      )}
      <aside className="sidebar vo-sidebar" aria-label="主导航">
        <div className="sidebar__top">
          <button
            className="vo-brand"
            onClick={() => {
              setView("chat");
              setDrawer(false);
            }}
          >
            <strong>
              VortoOne<span>✦</span>
            </strong>
            <small>我的工作空间</small>
          </button>
          <Button
            variant="ghost"
            className="vo-new"
            onClick={() => selectSession(crypto.randomUUID())}
          >
            <span aria-hidden="true">＋</span> 开始新聊天
          </Button>
        </div>
        <div className="sidebar__scroll">
          <nav className="vo-nav">
            {PAGES.map((page) => (
              <button
                key={page.id}
                className="sidebar__item"
                aria-current={view === page.id ? "page" : undefined}
                onClick={() => {
                  setView(page.id);
                  setDrawer(false);
                }}
              >
                <span className="vo-nav__mark" aria-hidden="true">
                  {page.mark}
                </span>
                {page.label}
              </button>
            ))}
          </nav>
          <section className="vo-recent">
            <h2>最近的聊天</h2>
            {sessionsError ? (
              <p className="vo-muted" role="status">
                会话列表暂时无法读取
              </p>
            ) : sessions.length === 0 ? (
              <p className="vo-muted">还没有已保存的聊天</p>
            ) : (
              sessions.slice(0, 40).map((session) => (
                <button
                  className="sidebar__item vo-session"
                  key={session.sid}
                  aria-current={
                    view === "chat" && session.sid === state.sid
                      ? "page"
                      : undefined
                  }
                  onClick={() => selectSession(session.sid)}
                >
                  <span aria-hidden="true">#</span>
                  <span>{session.title || "未命名聊天"}</span>
                  {session.status === "working" && (
                    <i className="vo-dot" title="进行中" />
                  )}
                </button>
              ))
            )}
          </section>
        </div>
        <div className="sidebar__foot">
          <button
            className="vo-profile"
            onClick={() => {
              setView("settings");
              setDrawer(false);
            }}
          >
            <Avatar
              kind="agent"
              name={name}
              size="md"
              appearance={appearance}
            />
            <span>
              <strong>{name}</strong>
              <small>
                <i className="vo-dot" data-online={connected} />
                {CONNECTION[state.connection]}
              </small>
            </span>
            <span aria-hidden="true">⌘</span>
          </button>
        </div>
      </aside>
      <main className="vo-main">
        <button
          className="vo-mobile-menu"
          aria-label="打开导航"
          onClick={() => setDrawer(true)}
        >
          ☰
        </button>
        {(notice || state.error) && (
          <div className="vo-banner" role="alert">
            <span>{notice || state.error}</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setNotice("");
                runtime.clearError();
              }}
            >
              关闭
            </Button>
          </div>
        )}
        {view === "chat" ? (
          <>
            <header className="channel__header vo-chathead">
              <Avatar
                kind="agent"
                name={name}
                size="sm"
                appearance={appearance}
              />
              <div>
                <h1 className="channel__title">
                  {currentSession?.title || "与助理聊天"}
                </h1>
                <p className="vo-chathead__sub">
                  {state.chat.busy
                    ? "正在处理你的请求"
                    : "想法、问题和接下来的事，都可以从这里开始"}
                </p>
              </div>
              <span className="vo-chathead__status">
                <i className="vo-dot" data-online={connected} />
                {CONNECTION[state.connection]}
              </span>
            </header>
            <div
              className="vo-chatbody"
              ref={scroll}
              onScroll={() => {
                const el = scroll.current;
                if (el)
                  follows.current =
                    el.scrollHeight - el.scrollTop - el.clientHeight < 100;
              }}
            >
              {state.chat.messages.length === 0 && !state.chat.stream ? (
                <div className="vo-welcome">
                  <Avatar
                    kind="agent"
                    name={name}
                    size="xl"
                    appearance={appearance}
                  />
                  <p className="vo-eyebrow">{name.toUpperCase()}</p>
                  <h2>今天，想一起做点什么？</h2>
                  <p>聊聊想法，整理计划，或把手头的事情向前推进。</p>
                  <div className="vo-suggestions">
                    {[
                      "帮我梳理今天要做的事",
                      "一起研究一个新想法",
                      "看看项目接下来怎么推进",
                    ].map((value) => (
                      <button
                        key={value}
                        disabled={!connected}
                        onClick={() => changeDraft(value)}
                      >
                        {value}
                        <span aria-hidden="true">↗</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="vo-messages" aria-label="聊天记录">
                  {state.chat.messages.map((message) =>
                    message.role === "system" ? (
                      <p key={message.id} className="vo-system">
                        {message.text}
                      </p>
                    ) : (
                      <Message
                        key={message.id}
                        author={message.role === "user" ? "你" : name}
                        time=""
                        own={message.role === "user"}
                        agent={message.role === "assistant"}
                        avatar={
                          <Avatar
                            kind={message.role === "user" ? "person" : "agent"}
                            name={message.role === "user" ? "你" : name}
                            appearance={appearance}
                            size="md"
                          />
                        }
                      >
                        <MessageText>
                          <Markdown text={message.text} />
                        </MessageText>
                      </Message>
                    ),
                  )}
                  {state.chat.stream && (
                    <Message
                      author={name}
                      time=""
                      agent
                      streaming
                      avatar={
                        <Avatar
                          kind="agent"
                          name={name}
                          state="writing"
                          appearance={appearance}
                        />
                      }
                    >
                      <MessageText>
                        <Markdown text={state.chat.stream} />
                      </MessageText>
                    </Message>
                  )}
                </div>
              )}
              {state.chat.activities.length > 0 && (
                <details className="vo-activities" open={state.chat.busy}>
                  <summary>
                    执行过程 <span>{state.chat.activities.length}</span>
                  </summary>
                  {state.chat.activities.slice(-30).map((activity) => (
                    <div className="vo-activity" key={activity.id}>
                      <i className="vo-dot" data-status={activity.status} />
                      <span>{activity.label}</span>
                      <small>{status(activity.status)}</small>
                      {(activity.result || activity.error) && (
                        <details>
                          <summary>查看详情</summary>
                          <pre>{activity.error || activity.result}</pre>
                        </details>
                      )}
                    </div>
                  ))}
                </details>
              )}
              {state.chat.confirmations.map((confirmation) => (
                <Card padded className="vo-confirm" key={confirmation.id}>
                  <div className="vo-confirm__heading">需要你的确认</div>
                  <Markdown text={confirmation.text} />
                  <div className="vo-confirm__actions">
                    {confirmation.sent ? (
                      <p role="status">决定已提交，等待运行端回执…</p>
                    ) : (
                      <>
                        <Button
                          disabled={!connected || deciding !== null}
                          onClick={() => void decide(confirmation.id, false)}
                        >
                          拒绝
                        </Button>
                        <Button
                          variant="primary"
                          disabled={!connected || deciding !== null}
                          onClick={() => void decide(confirmation.id, true)}
                        >
                          {deciding === confirmation.id
                            ? "正在核对…"
                            : "批准这次操作"}
                        </Button>
                      </>
                    )}
                  </div>
                </Card>
              ))}
            </div>
            <div className="vo-compose">
              <div className="vo-compose__inner">
                {state.chat.queue.length > 0 && (
                  <div className="vo-queue" aria-label="排队消息">
                    {state.chat.queue.map((item) => (
                      <div key={item.id}>
                        <span>排队中 · {item.text}</span>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={!connected}
                          onClick={() => {
                            try {
                              runtime.removeQueued(item.id);
                            } catch (error) {
                              setNotice(errorText(error));
                            }
                          }}
                        >
                          移除
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
                <Composer
                  value={draft}
                  onChange={changeDraft}
                  onSend={(value) => void send(value)}
                  label="发送给助理"
                  placeholder={
                    connected ? `给 ${name} 发消息…` : "连接恢复后即可发送消息"
                  }
                  sendLabel={sending ? "正在发送" : "发送消息"}
                  disabled={!connected}
                  sendBlocked={sending}
                  agentBusy={state.chat.busy}
                  working={
                    state.chat.busy
                      ? {
                          stopLabel: "停止当前回合",
                          onStop: () => {
                            try {
                              runtime.stop();
                            } catch (error) {
                              setNotice(errorText(error));
                            }
                          },
                        }
                      : null
                  }
                  pills={
                    <div className="vo-compose__mode">
                      <button
                        aria-pressed={mode === "plan"}
                        onClick={() => setMode("plan")}
                      >
                        讨论
                      </button>
                      <button
                        aria-pressed={mode === "build"}
                        onClick={() => setMode("build")}
                      >
                        执行
                      </button>
                    </div>
                  }
                  format={{
                    toolbar: "格式",
                    bold: "加粗",
                    italic: "斜体",
                    strike: "删除线",
                    code: "行内代码",
                    codeBlock: "代码块",
                    link: "链接",
                    bulletList: "列表",
                    numberedList: "有序列表",
                    quote: "引用",
                  }}
                />
                <p className="vo-compose__hint">
                  Enter 发送 · Shift + Enter 换行
                  {state.chat.busy ? " · 空输入框时可停止当前回合" : ""}
                </p>
              </div>
            </div>
          </>
        ) : view === "settings" ? (
          <div className="page vo-page">
            <PageHeader title="设置" meta="让这个工作空间更适合你。" />
            <Card padded>
              <h2>外观</h2>
              <div className="vo-theme-options">
                {(["system", "light", "dark"] as const).map((item, i) => (
                  <Button
                    key={item}
                    variant={preference === item ? "primary" : "default"}
                    onClick={() => setPreference(item)}
                  >
                    {["跟随系统", "浅色", "深色"][i]}
                  </Button>
                ))}
              </div>
              <h2>助理形象</h2>
              <div className="vo-characters">
                {ROLES.map((role) => (
                  <button
                    key={role.color}
                    aria-pressed={role.color === roleColor}
                    onClick={() => {
                      setRoleColor(role.color);
                      save("character", role.color ?? "rose");
                    }}
                  >
                    <Avatar
                      kind="agent"
                      name={role.name}
                      size="lg"
                      appearance={{ color: role.color }}
                    />
                    <span>{role.name}</span>
                  </button>
                ))}
              </div>
            </Card>
            <Card padded>
              <h2>工作空间连接</h2>
              <p>
                <i className="vo-dot" data-online={connected} />{" "}
                {CONNECTION[state.connection]}
              </p>
              <Button onClick={() => runtime.connect(state.sid)}>
                重新连接
              </Button>
              {auth?.auth_required && (
                <Button variant="ghost" onClick={() => void logout()}>
                  退出登录
                </Button>
              )}
            </Card>
          </div>
        ) : (
          <RecordsPage view={view} />
        )}
      </main>
    </div>
  );
}

const SOURCES = {
  goals: {
    title: "目标",
    meta: "你交代的目标与当前记录。",
    path: "/api/assistant/goals",
    key: "goals",
    empty: "还没有记录目标",
  },
  tasks: {
    title: "任务",
    meta: "查看任务的进展、结果与阻塞。",
    path: "/api/tasks",
    key: "tasks",
    empty: "还没有任务记录",
  },
  memories: {
    title: "记忆",
    meta: "助理记下的事实、偏好与约定。",
    path: "/api/assistant/memories",
    key: "memories",
    empty: "还没有保存记忆",
  },
  schedule: {
    title: "日程",
    meta: "助理的定时事项与最近运行记录。",
    path: "/api/assistant/schedule",
    key: "items",
    empty: "还没有定时事项",
  },
};
function RecordsPage({ view }: { view: keyof typeof SOURCES }) {
  const source = SOURCES[view];
  const { value, loading, error, refresh } = useRead<unknown>(source.path);
  const raw = Array.isArray(value)
    ? value
    : value && typeof value === "object"
      ? (value as RecordItem)[source.key]
      : undefined;
  const malformed =
    value !== undefined &&
    (!Array.isArray(raw) ||
      raw.some((item) => !item || typeof item !== "object"));
  const items = Array.isArray(raw)
    ? (raw.filter((item) => item && typeof item === "object") as RecordItem[])
    : [];
  return (
    <div className="page vo-page">
      <PageHeader
        title={source.title}
        meta={source.meta}
        actions={
          <Button onClick={refresh} disabled={loading}>
            刷新
          </Button>
        }
      />
      {error || malformed ? (
        <div className="vo-error" role="alert">
          <p>{error || "响应无法识别，请刷新或检查运行端版本。"}</p>
          <Button onClick={refresh}>重试</Button>
        </div>
      ) : loading ? (
        <p role="status" className="vo-muted">
          正在读取…
        </p>
      ) : items.length === 0 ? (
        <EmptyState>{source.empty}</EmptyState>
      ) : (
        <div className="vo-records">
          {items.map((item, index) => (
            <Card padded key={text(item, "id", "name") || index}>
              <div className="vo-record__heading">
                <h2>
                  {text(
                    item,
                    "title",
                    "objective",
                    "name",
                    "label",
                    "prompt",
                  ) || (view === "memories" ? "记忆" : "未命名事项")}
                </h2>
                {typeof item.status === "string" && (
                  <span className="vo-badge">{status(item.status)}</span>
                )}
              </div>
              {text(
                item,
                "content",
                "text",
                "description",
                "detail",
                "summary",
                "activity",
                "result",
                "last_result",
                "next_action",
              ) && (
                <Markdown
                  text={text(
                    item,
                    "content",
                    "text",
                    "description",
                    "detail",
                    "summary",
                    "activity",
                    "result",
                    "last_result",
                    "next_action",
                  )}
                />
              )}
              {text(
                item,
                "next_run",
                "next_due",
                "schedule",
                "cron",
                "next",
              ) && (
                <p className="vo-muted">
                  {text(
                    item,
                    "next_run",
                    "next_due",
                    "schedule",
                    "cron",
                    "next",
                  )}
                </p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
