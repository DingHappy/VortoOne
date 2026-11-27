import { execFileSync } from "node:child_process";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { checkDevConfig, checkStableConfig } from "./desktop-dev-model.mjs";
import { runNpm } from "./run-npm.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const stableChannel = process.argv.includes("--stable");
const native = new URL("../apps/desktop-dev/src-tauri/", import.meta.url);
const previous = JSON.parse(
  await readFile(new URL("../../desktop/src-tauri/tauri.conf.json", import.meta.url)),
);
const config = JSON.parse(await readFile(new URL(
  stableChannel ? "tauri.stable.conf.json" : "tauri.conf.json", native,
)));
if (stableChannel) checkStableConfig(config, previous);
else checkDevConfig(config, previous);

if (!process.argv.includes("--skip-build")) {
  if (!stableChannel) runNpm(["run", "build:preview"], root);
  // Serve the real backend UI from web/ and the native shell from shell/.
  runNpm(["run", "build", "--workspace", "@vortoone/web"], root);
  runNpm(["run", "build", "--workspace", "@vortoone/desktop", "--", "--outDir", "dist-live"], root);
}

const output = new URL(stableChannel ? "../dist-stable/" : "../dist/", native);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
if (!stableChannel) {
  await cp(new URL("../apps/web/dist-preview/", import.meta.url), output, { recursive: true });
}
await cp(new URL("../../NOTICE", import.meta.url), new URL("NOTICE", output));
await cp(new URL("../apps/desktop/dist-live/", import.meta.url), new URL("shell/", output), {
  recursive: true,
});
for (const name of [
  "model-settings.html", "model-settings.css", "model-settings.js", "starting.html", "starting.css",
  "computers.html", "computers.css", "computers.js",
]) {
  const from = new URL(`../${name}`, native);
  const to = new URL(name, output);
  if (stableChannel && name === "computers.html") {
    await writeFile(to, (await readFile(from, "utf8")).replace("VortoOne Dev", "VortoOne"));
  } else {
    await cp(from, to);
  }
}

const commit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
const info = {
  channel: stableChannel ? "stable" : "development",
  version: config.version,
  productName: config.productName,
  identifier: config.identifier,
  commit,
  data: stableChannel ? "bundled Python backend with local persistent data"
    : "bundled Python backend with local persistent data; separate preview fixtures",
  updates: stableChannel ? "signed-native" : "manual-development-draft",
  stable: {
    version: stableChannel ? config.version : previous.version,
    url: "https://github.com/DingHappy/VortoOne/releases/latest",
  },
};
await writeFile(new URL("desktop-channel.json", output), JSON.stringify(info, null, 2) + "\n");

const escape = (s) => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const pageTitle = `${escape(config.productName)} · 版本管理`;
const versionDetails = stableChannel
  ? `<section><h2>当前版本</h2><strong>${escape(config.productName)} ${escape(config.version)}</strong></section>`
  : `<p>稳定使用与开发验收，分别管理。</p>
<section><h2>公开稳定版</h2><strong>VortoOne ${escape(previous.version)}</strong><p>已发布的正式桌面 App，继续使用现有自动更新。</p></section>
<section><h2>当前开发版</h2><strong>VortoOne Dev ${escape(config.version)}</strong><p>内置 Python 后端 · 本机持久数据 · 安装后即可配置模型</p><p>通过独立开发安装包更新；发布草稿由你管理。</p></section>
<p>在菜单「开发版」中打开公开稳定版下载或 GitHub 发布管理。</p>`;
const grayPanel = stableChannel ? `
<section id="gray-updates" aria-labelledby="gray-title" hidden>
  <div class="row"><h2 id="gray-title">灰度更新</h2><button id="gray-admin" type="button" class="secondary">打开灰度管理</button></div>
  <p><span id="gray-enrollment-state"></span> · 当前版本 <span id="gray-installed"></span></p>
  <form id="gray-enroll" hidden>
    <p>从灰度管理页取得一次性登记码，再登记这台电脑。</p>
    <div class="fields">
      <div><label for="gray-name">设备名称</label><input id="gray-name" type="text" autocomplete="off" maxlength="64" required></div>
      <div><label for="gray-code">一次性登记码</label><input id="gray-code" type="text" autocomplete="off" spellcheck="false" autocapitalize="none" maxlength="96" required></div>
    </div>
    <button type="submit">登记此电脑</button>
  </form>
  <div id="gray-actions" class="row" hidden><button id="gray-check" type="button">检查更新</button><button id="gray-leave" type="button" class="secondary">退出灰度</button></div>
</section>
<p id="gray-status" role="status" aria-atomic="true"></p>
<dialog id="gray-leave-confirm" aria-labelledby="gray-leave-title">
  <h2 id="gray-leave-title">退出此电脑的灰度更新？</h2>
  <p>已安装的版本会保留。</p>
  <form method="dialog" class="row"><button type="submit" value="cancel" class="secondary" autofocus>取消</button><button type="submit" value="leave">退出灰度</button></form>
</dialog>
` : "";
if (stableChannel) await cp(new URL("../versions.js", native), new URL("versions.js", output));
await cp(new URL("../version-manager.css", native), new URL("version-manager.css", output));
await writeFile(
  new URL("versions.html", output),
  `<!doctype html><html lang="zh-Hans"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${pageTitle}</title>
<link rel="stylesheet" href="version-manager.css"><main><h1>版本管理</h1>${versionDetails}${grayPanel}
<details><summary>构建信息</summary><p>提交：${escape(commit)}</p><p>应用标识：${escape(config.identifier)}</p></details></main>
${stableChannel ? '<script type="module" src="versions.js"></script>' : ""}</html>`,
);
console.log(`Prepared ${config.productName} ${config.version}: ${fileURLToPath(output)}`);
