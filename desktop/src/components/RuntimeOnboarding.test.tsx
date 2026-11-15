import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RuntimeOnboarding, type RuntimeOnboardingProps } from "./RuntimeOnboarding";
import { OnboardingModel } from "./OnboardingModel";
import { elapsedLabel, FirstRunLocalView, FirstRunProgress } from "./FirstRunLocal";
import type { LocalResidentStatus } from "../lib/localResident";
import { LOCAL_SETUP_IDLE, type LocalSetupProgress } from "../lib/localSetup";
const noop = () => {};
const props: RuntimeOnboardingProps = { initial: { version: 1, status: "pending", choice: null }, profiles: [], modelReady: false, modelSetup: null,
  onStatus: noop, onInspectAgain: noop, onConnect: async () => {}, onComplete: noop };
it("offers two locations with one preselected and a single button that continues", () => {
  const html = renderToStaticMarkup(<RuntimeOnboarding {...props} />);
  for (const text of ["在这台 Mac 上", "在一台一直开着的电脑上", "睡眠时暂停", 'aria-labelledby="runtime-onboarding-title"', 'role="radiogroup"']) expect(html).toContain(text);
  expect(html.match(/role="radio"/g)).toHaveLength(2);
  expect(html.match(/aria-checked="true"/g)).toHaveLength(1);
  expect(html).toMatch(/<button[^>]*vc-btn--primary[^>]*>准备这台 Mac/);
  expect(html).not.toContain('type="checkbox"');
});
it("keeps an unknown inspection explicit and prevents choosing before retry", () => {
  const html = renderToStaticMarkup(<RuntimeOnboarding {...props} inspectionFailed initialError="读取失败" />);
  expect(html).toContain('role="alert"');
  expect(html).toContain('重新核对已有配置');
  expect(html.match(/<button\b(?=[^>]*\bdisabled="")[^>]*>/g)).toHaveLength(3);
});
const local: LocalResidentStatus = { supported: true, installed: false, state: "not_installed", availableVersion: "1", cronEnabled: false, revision: "r1", message: "" };
const view = { status: local, reading: false, readError: "", copy: false, modelReady: true, modelSetup: <i>模型表单</i>, back: null,
  onCopy: noop, onEditModel: noop, onInspect: noop, onBegin: noop, onRemote: noop };
const start = (html: string) => html.match(/<button[^>]*vc-btn--primary[^>]*>/)![0];
it("this Mac: start stays off until a model is saved and the owner agrees to copy it", () => {
  const unchecked = renderToStaticMarkup(<FirstRunLocalView {...view} />);
  expect(unchecked.match(/type="checkbox"/g)).toHaveLength(1); expect(unchecked).not.toContain('checked=""');
  expect(start(unchecked)).toContain("disabled"); expect(unchecked).not.toContain("模型表单");
  expect(start(renderToStaticMarkup(<FirstRunLocalView {...view} copy />))).not.toContain("disabled");
  const noModel = renderToStaticMarkup(<FirstRunLocalView {...view} modelReady={false} copy />);
  expect(noModel).toContain("模型表单"); expect(start(noModel)).toContain("disabled");
  expect(noModel).not.toContain("定时作业");
});
it("this Mac: an installed assistant needs neither a model form nor consent, and unknown states offer no start", () => {
  const installed = renderToStaticMarkup(<FirstRunLocalView {...view} modelReady={false} status={{ ...local, installed: true, state: "stopped", version: "0.2.64" }} />);
  expect(installed).toContain("启动并连接"); expect(installed).not.toContain('type="checkbox"'); expect(installed).not.toContain("模型表单");
  expect(start(installed)).not.toContain("disabled");
  for (const html of [renderToStaticMarkup(<FirstRunLocalView {...view} copy status={{ ...local, state: "unavailable" }} />),
    renderToStaticMarkup(<FirstRunLocalView {...view} copy readError="读取失败" />),
    renderToStaticMarkup(<FirstRunLocalView {...view} copy reading />)]) expect(start(html)).toContain("disabled");
  const unsupported = renderToStaticMarkup(<FirstRunLocalView {...view} status={{ ...local, supported: false }} />);
  expect(unsupported).toContain("连接另一台电脑"); expect(unsupported).not.toContain("vc-btn--primary");
});
const progress = (patch: Partial<LocalSetupProgress>): LocalSetupProgress => ({ ...LOCAL_SETUP_IDLE, activeAt: 0, ...patch });
it("progress names each step, keeps a failure on its step and only then offers retry", () => {
  const running = renderToStaticMarkup(<FirstRunProgress now={12000} onCancel={noop} onBack={noop} onRetry={noop}
    progress={progress({ steps: { install: "done", start: "active", connect: "todo" } })} />);
  for (const text of ["安装常驻助理", "启动", "连接到这台 Mac 的助理", 'aria-valuenow="1"', "0:12", ">取消<"]) expect(running).toContain(text);
  expect(running).not.toContain("重试");
  const failed = renderToStaticMarkup(<FirstRunProgress now={0} onCancel={noop} onBack={noop} onRetry={noop}
    progress={progress({ end: "failed", error: "合成启动失败", steps: { install: "done", start: "failed", connect: "todo" } })} />);
  for (const text of ["没有准备完", "合成启动失败", "重试", "返回"]) expect(failed).toContain(text);
  expect(failed).not.toContain(">取消<");
  expect(elapsedLabel(75000)).toBe("1:15");
});
it("remote setup omits project paths and does not claim to copy local model configuration", () => {
  const html = renderToStaticMarkup(<RuntimeOnboarding {...props} initial={{ version: 1, status: "pending", choice: "remote" }} />);
  expect(html).toContain('不会复制这台 Mac 的模型配置');
  expect(html).toContain('type="password"');
  expect(html).not.toContain('服务器上的项目目录');
});
it("model setup uses saved-configuration language and blocks saving during failed reads", () => {
  const html = renderToStaticMarkup(<OnboardingModel profile={null} baseUrl="" model="" apiKey="" fastModel="" strongModel="" roleModels={{}}
    loading={false} busy={false} conflict={false} error="读取失败" note="" onBaseUrl={noop} onModel={noop} onKey={noop} onSave={noop} onReload={noop} onAccount={noop} />);
  expect(html).toContain('读取失败');
  expect(html.match(/<button[^>]*>保存模型配置<\/button>/)?.[0]).toContain('disabled');
  expect(html).toContain('放弃草稿并重新载入');
});
