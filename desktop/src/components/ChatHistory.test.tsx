import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { ConversationMessage, TurnActivity } from "../types";
import { ChatHistory, StreamingMessage } from "./ChatHistory";

const messages: ConversationMessage[] = [
  { id: "u1", role: "user", text: "检查项目", rid: "r1", source: "telegram", images: ["data:image/png;base64,AA=="] },
  { id: "a1", role: "assistant", text: "**检查完成**", rid: "r1", source: "telegram" },
];
const activity: TurnActivity = { id: "t1", kind: "tool", status: "completed", label: "读取配置", result: "读取结果" };
const props = { messages, activityGroups: new Map([["r1", [activity]]]), busy: false,
  activeTurnRid: null, assistant: true };

it("preserves phone attribution, attachments, Markdown and the completed turn timeline", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} />);
  for (const text of ["Telegram", "回复在手机上", "发送的图片 1", "<strong>检查完成</strong>", 'aria-expanded="false"']) {
    expect(html).toContain(text);
  }
  expect(html).toContain(">你</strong>");
  expect(html).toContain(">VortoOne</strong>");
  expect(html).toContain("general.png");
  expect(renderToStaticMarkup(<ChatHistory {...props} assistant={false} />)).toContain('<div class="message-author">你</div>');
  expect(html).not.toContain("读取结果");
});

it("does not collapse a turn based on another turn's output or an empty reply", () => {
  for (const reply of [{ ...messages[1], rid: "r2" }, { ...messages[1], text: " \n" }]) {
    const html = renderToStaticMarkup(<ChatHistory {...props} messages={[messages[0], reply]} />);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("读取结果");
  }
});

it("keeps current and failed work visible even if the turn has output", () => {
  const active = renderToStaticMarkup(<ChatHistory {...props} busy activeTurnRid="r1" />);
  expect(active).toContain("正在工作");
  expect(active).toContain("读取结果");
  const failed = renderToStaticMarkup(<ChatHistory {...props}
    activityGroups={new Map([["r1", [{ ...activity, status: "failed" }]]])} />);
  expect(failed).toContain("需要关注");
  expect(failed).toContain("读取结果");
});

it("uses workbench authors outside assistant mode", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} assistant={false} />);
  expect(html).toContain('<div class="message-author">你</div>');
  expect(html).toContain('<div class="message-author">VortoOne</div>');
});

it("exposes exact turn anchors and highlights only the selected turn", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} focusedRid="r1"
    messages={[...messages, { id: "other", rid: "r2", role: "user", text: "别的请求" }]} />);
  expect(html.match(/located-turn/g)).toHaveLength(2);
  expect(html.match(/tabindex="-1"/g)).toHaveLength(2);
  expect(html).toContain('data-turn-rid="r1" data-message-role="user"');
  const other = html.match(/<article[^>]*data-turn-rid="r2"[^>]*>/)![0];
  expect(other).not.toContain("located-turn"); expect(other).not.toContain("tabindex");
});

const articles = (html: string) => html.match(/<article[^>]*>/g) ?? [];

it("keeps a turn's work inside the reply it produced, in one row with one identity", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} />);
  expect(articles(html)).toHaveLength(2);
  expect(html.match(/general\.png/g)).toHaveLength(2); // preload + one avatar
  expect(html.indexOf("读取配置")).toBeGreaterThan(html.indexOf(">VortoOne</strong>"));
  expect(html.indexOf("读取配置")).toBeLessThan(html.indexOf("<strong>检查完成</strong>"));
});

it("shows work that has no reply yet as the assistant's own row after the request", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} messages={[messages[0]]} busy activeTurnRid="r1" />);
  expect(articles(html)).toHaveLength(2);
  expect(articles(html)[1]).toContain('data-message-role="process"');
  expect(html).toContain(">VortoOne</strong>"); expect(html).toContain("正在工作");
});

it("groups consecutive replies under one identity, but never the owner's request or a team note", () => {
  const more: ConversationMessage[] = [...messages, { id: "a2", role: "assistant", text: "补充一句", rid: "r1" },
    { id: "n1", role: "assistant", text: "小组交付", source: "team" }];
  const rows = articles(renderToStaticMarkup(<ChatHistory {...props} messages={more} />));
  expect(rows.map(row => row.includes("vc-msg-grouped"))).toEqual([false, false, true, false]);
  expect(rows[0]).toContain("vc-msg-own");
});

it("the live reply continues the pending work row instead of repeating the identity", () => {
  const alone = renderToStaticMarkup(<StreamingMessage text="写到一半" rid="r1" />);
  expect(alone).toContain(">VortoOne</strong>"); expect(alone).toContain('data-rid="r1"');
  const continued = renderToStaticMarkup(<StreamingMessage text="写到一半" rid="r1" grouped />);
  expect(continued).not.toContain("<strong>VortoOne"); expect(continued).not.toContain("general.png");
});

it("a blank saved reply shows its work without an empty text block", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} messages={[messages[0], { ...messages[1], text: " \n" }]} />);
  expect(html).toContain("读取结果"); expect(html.match(/vc-msg-body/g)).toHaveLength(1);
});

it("a team note is labelled as the team, not as a phone reply, and renders its card", () => {
  const note: ConversationMessage = { id: "n1", role: "assistant", text: "🧩 任务小组「股市」交付了", source: "team",
    card: { kind: "team", id: "task-1", status: "review" } };
  const html = renderToStaticMarkup(<ChatHistory {...props} messages={[note]}
    renderCard={message => <span>卡片 {message.card?.id}</span>} />);
  expect(html).toContain("任务小组"); expect(html).not.toContain("回复在手机上"); expect(html).toContain("卡片 task-1");
});

it("offers copy for loaded user and assistant text, preserving system notes and card-only messages", () => {
  const more: ConversationMessage[] = [...messages,
    { id: "system", role: "system", text: "断开连接" },
    { id: "card", role: "assistant", text: "", card: { kind: "team", id: "t", status: "review" } },
  ];
  const html = renderToStaticMarkup(<ChatHistory {...props} messages={more} renderCard={() => <button>核对交付</button>} />);
  expect(html.match(/aria-label="复制消息"/g)).toHaveLength(2);
  expect(html).toContain("断开连接");
  expect(html).toContain("核对交付");
  expect(renderToStaticMarkup(<ChatHistory {...props} assistant={false} />)).not.toContain("复制消息");
});

it("an image-only message keeps its attachment without an empty text bubble or copy action", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} activityGroups={new Map()}
    messages={[{ id: "image", role: "user", text: "", images: ["data:image/png;base64,AA=="], source: "telegram" }]} />);
  expect(html).toContain("发送的图片 1");
  expect(html).toContain("Telegram");
  expect(html).not.toContain('class="message-text"');
  expect(html).not.toContain("复制消息");
});
