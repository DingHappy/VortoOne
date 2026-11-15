import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import {
  Avatar,
  AgentSticker,
  Logo,
  normalizeAppearance,
  switchTheme,
  type AgentAppearance,
  type AgentState,
} from "@agenteschat/ui";
import type { Expression } from "@agenteschat/ui/agent/engine";
import { LookPicker } from "./components/AgentFields";
import { loadI18n } from "./i18n";
import "./styles.css";
import "./character-lab.css";

const DEFAULT: Required<AgentAppearance> = {
  color: "sky",
  eyes: "round",
  accessory: "none",
};
const KEY = "vortoone.slime.look.v1";
function savedLook() {
  try {
    return {
      ...DEFAULT,
      ...Object.fromEntries(
        Object.entries(
          normalizeAppearance(JSON.parse(localStorage.getItem(KEY) || "{}")),
        ).filter(([, v]) => v !== undefined),
      ),
    };
  } catch {
    return DEFAULT;
  }
}
const FACES: [Expression | "auto", string][] = [
  ["auto", "自然"],
  ["happy", "开心"],
  ["wink", "眨眼"],
  ["heart", "喜欢"],
  ["star", "兴奋"],
  ["surprised", "惊讶"],
  ["closed", "闭眼"],
  ["dizzy", "晕乎乎"],
  ["angry", "生气"],
  ["cross", "出错"],
];
const STATES: [AgentState, string][] = [
  ["idle", "空闲"],
  ["thinking", "思考"],
  ["writing", "输入"],
  ["waiting", "等待"],
  ["sleeping", "休息"],
];
const PRESETS: { name: string; look: Required<AgentAppearance> }[] = [
  { name: "总助手", look: DEFAULT },
  {
    name: "研究助手",
    look: { color: "violet", eyes: "round", accessory: "glasses" },
  },
  {
    name: "开发助手",
    look: { color: "teal", eyes: "round", accessory: "headphones" },
  },
  {
    name: "日程助手",
    look: { color: "amber", eyes: "round", accessory: "scarf" },
  },
];

function CharacterLab() {
  const [look, setLook] = useState<Required<AgentAppearance>>(savedLook);
  const [preview, setPreview] = useState<Partial<AgentAppearance> | null>(null);
  const [expression, setExpression] = useState<Expression | "auto">("auto");
  const [state, setState] = useState<AgentState>("idle");
  const [dark, setDark] = useState(false);
  const [kept, setKept] = useState(true);
  const shown = { ...look, ...preview };
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(look));
      setKept(true);
    } catch {
      setKept(false);
    }
  }, [look]);
  const choose = (patch: Partial<AgentAppearance>) => {
    setPreview(null);
    setLook((l) => ({ ...l, ...patch }));
  };
  return (
    <main className="slime-lab">
      <header className="slime-lab__header">
        <Logo size={25} />
        <div className="slime-lab__nav">
          <a href="/desktop-preview.html?lang=zh-Hans">返回桌面预览 ↗</a>
          <button
            type="button"
            onClick={() => {
              switchTheme(dark ? "light" : "dark");
              setDark(!dark);
            }}
          >
            {dark ? "浅色" : "深色"}
          </button>
        </div>
      </header>
      <div className="slime-lab__intro">
        <span className="slime-lab__eyebrow">VORTOONE CHARACTERS</span>
        <h1>让史莱姆有自己的个性。</h1>
        <p>选一个颜色，换个表情，再戴上一点喜欢的东西。</p>
      </div>
      <div className="slime-lab__layout">
        <section className="slime-lab__stage" aria-label="史莱姆实时预览">
          <div className="slime-lab__live">
            <span /> 实时预览
          </div>
          <div className="slime-lab__hero">
            <Avatar
              kind="agent"
              name="你的史莱姆"
              size="xl"
              framed={false}
              appearance={shown}
              state={state}
              expression={expression === "auto" ? undefined : expression}
              style={{ width: 260, height: 260 }}
            />
          </div>
          <h2>你的史莱姆</h2>
          <p>{kept ? "外观已保存在此浏览器" : "外观暂未保存"}</p>
          <div className="slime-lab__sizes" aria-label="不同尺寸的头像">
            {(["sm", "md", "lg"] as const).map((size) => (
              <Avatar
                key={size}
                kind="agent"
                name="史莱姆"
                size={size}
                appearance={shown}
                state={state}
                expression={expression === "auto" ? undefined : expression}
              />
            ))}
          </div>
          <div className="slime-lab__presets" aria-label="角色预设">
            {PRESETS.map((p) => (
              <button
                type="button"
                key={p.name}
                onClick={() => {
                  setPreview(null);
                  setLook(p.look);
                }}
              >
                <Avatar
                  kind="agent"
                  name={p.name}
                  appearance={p.look}
                  size="md"
                  framed={false}
                />
                <span>{p.name}</span>
              </button>
            ))}
          </div>
        </section>
        <section className="slime-lab__controls" aria-label="自定义史莱姆">
          <div className="slime-lab__section-title">
            <h2>外观</h2>
            <button
              type="button"
              onClick={() => {
                setLook(DEFAULT);
                setPreview(null);
                setExpression("auto");
                setState("idle");
              }}
            >
              恢复默认
            </button>
          </div>
          <LookPicker look={look} onPick={choose} onPreview={setPreview} />
          <div className="slime-lab__section-title">
            <h2>表情</h2>
            <span>不改变角色外观</span>
          </div>
          <div className="slime-lab__choices" role="group" aria-label="表情">
            {FACES.map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={expression === value}
                onClick={() => {
                  setExpression(value);
                  setState("idle");
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="slime-lab__section-title">
            <h2>工作状态</h2>
          </div>
          <div
            className="slime-lab__choices"
            role="group"
            aria-label="工作状态"
          >
            {STATES.map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={state === value}
                onClick={() => {
                  setState(value);
                  setExpression("auto");
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </section>
      </div>
      <section className="slime-lab__stickers" aria-label="动态贴纸">
        <div>
          <h2>同一个角色，也会表达心情。</h2>
          <p>贴纸沿用你的配色和配饰。</p>
        </div>
        {(
          [
            ["wave", "打招呼"],
            ["thinking", "想一想"],
            ["celebrate", "庆祝"],
            ["love", "喜欢"],
          ] as const
        ).map(([mood, label]) => (
          <div className="slime-lab__sticker" key={mood}>
            <AgentSticker
              appearance={shown}
              mood={mood}
              size={104}
              label={label}
            />
            <span>{label}</span>
          </div>
        ))}
      </section>
    </main>
  );
}

const i18n = await loadI18n("zh-Hans");
switchTheme("light");
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nextProvider i18n={i18n}>
      <CharacterLab />
    </I18nextProvider>
  </StrictMode>,
);
