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
  updates: stableChannel ? (process.platform === "darwin" ? "signed-macos" : "manual-stable")
    : "manual-development-draft",
  stable: {
    version: stableChannel ? config.version : previous.version,
    url: "https://github.com/DingHappy/VortoOne/releases/latest",
  },
};
await writeFile(new URL("desktop-channel.json", output), JSON.stringify(info, null, 2) + "\n");

const escape = (s) => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const pageTitle = `${escape(config.productName)} · 版本管理`;
const versionDetails = stableChannel
  ? `<section><h2>公开稳定版</h2><strong>${escape(config.productName)} ${escape(config.version)}</strong></section>`
  : `<p>稳定使用与开发验收，分别管理。</p>
<section><h2>公开稳定版</h2><strong>VortoOne ${escape(previous.version)}</strong><p>已发布的正式桌面 App，继续使用现有自动更新。</p></section>
<section><h2>当前开发版</h2><strong>VortoOne Dev ${escape(config.version)}</strong><p>内置 Python 后端 · 本机持久数据 · 安装后即可配置模型</p><p>通过独立开发安装包更新；发布草稿由你管理。</p></section>
<p>在菜单「开发版」中打开公开稳定版下载或 GitHub 发布管理。</p>`;
await writeFile(
  new URL("versions.html", output),
  `<!doctype html><html lang="zh-Hans"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${pageTitle}</title>
<link rel="stylesheet" href="version-manager.css"><main><h1>版本管理</h1>${versionDetails}
<details><summary>构建信息</summary><p>提交：${escape(commit)}</p><p>应用标识：${escape(config.identifier)}</p></details></main></html>`,
);
await writeFile(
  new URL("version-manager.css", output),
  `:root{color-scheme:light dark;font:15px -apple-system,BlinkMacSystemFont,sans-serif}body{margin:0;background:light-dark(#fafafa,#171717);color:light-dark(#202020,#eee)}main{max-width:720px;margin:44px auto;padding:0 28px}h1{font-size:28px}h2{font-size:17px}p{line-height:1.7;color:light-dark(#666,#bbb)}section{border:1px solid light-dark(#ddd,#444);border-radius:14px;padding:18px 22px;margin:18px 0}strong{font-size:20px}details{margin-top:22px;overflow-wrap:anywhere}`,
);
console.log(`Prepared ${config.productName} ${config.version}: ${fileURLToPath(output)}`);
