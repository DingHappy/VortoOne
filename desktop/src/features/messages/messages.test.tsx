import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MessageRow } from "./MessageRow";
import { ProcessBlock } from "./ProcessBlock";

it("an owner message keeps author and time for screen readers without an avatar column", () => {
  const html = renderToStaticMarkup(<MessageRow own name="你" avatar={<i>头像</i>} meta={<time>09:00</time>}>整理一下</MessageRow>);
  expect(html).toContain('class="vc-msg-head sr-only"'); expect(html).toContain("09:00");
  expect(html).not.toContain("头像"); expect(html).toContain("整理一下");
});

it("orders work, text and cards the same way for every conversation", () => {
  const html = renderToStaticMarkup(<MessageRow name="研究助手" avatar={<i>头像</i>} process={<b>过程</b>}
    attachments={<u>卡片</u>} actions={<s>操作</s>}>正文</MessageRow>);
  const order = ["头像", "研究助手", "过程", "正文", "卡片", "操作"].map(text => html.indexOf(text));
  expect(order.every((at, index) => at > (order[index - 1] ?? -1))).toBe(true);
});

it("a row without text renders no empty body", () => {
  expect(renderToStaticMarkup(<MessageRow name="助手" process={<b>过程</b>} />)).not.toContain("vc-msg-body");
});

it("a collapsed process keeps its attention note visible and its details out of the page", () => {
  const closed = renderToStaticMarkup(<ProcessBlock label="用了 3 个工具" summary="12 分钟" attention="1 项需要关注">细节</ProcessBlock>);
  expect(closed).toContain('aria-expanded="false"'); expect(closed).toContain("1 项需要关注");
  expect(closed).toContain("12 分钟"); expect(closed).not.toContain("细节");
  const open = renderToStaticMarkup(<ProcessBlock label="用了 3 个工具" open>细节</ProcessBlock>);
  expect(open).toContain('aria-expanded="true"'); expect(open).toContain("细节");
});
