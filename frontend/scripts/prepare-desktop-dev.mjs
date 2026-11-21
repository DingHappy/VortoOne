import { execFileSync } from "node:child_process";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { checkDevConfig } from "./desktop-dev-model.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const config = JSON.parse(
  await readFile(
    new URL("../apps/desktop-dev/src-tauri/tauri.conf.json", import.meta.url),
  ),
);
const stable = JSON.parse(
  await readFile(
    new URL("../../desktop/src-tauri/tauri.conf.json", import.meta.url),
  ),
);
checkDevConfig(config, stable);
if (!process.argv.includes("--skip-build")) {
  execFileSync("npm", ["run", "build:preview"], {
    cwd: root,
    stdio: "inherit",
  });
  // 连本机后端时由后端提供的界面（随包资源 web/）
  execFileSync("npm", ["run", "build", "--workspace", "@vortoone/web"], {
    cwd: root,
    stdio: "inherit",
  });
  // 首次引导、偏好设置窗口：连真外壳的那一份（不是示例数据），外壳从 shell/index.html 打开
  execFileSync("npm", ["run", "build", "--workspace", "@vortoone/desktop", "--", "--outDir", "dist-live"], {
    cwd: root,
    stdio: "inherit",
  });
}
const output = new URL("../apps/desktop-dev/dist/", import.meta.url);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(new URL("../apps/web/dist-preview/", import.meta.url), output, {
  recursive: true,
});
await cp(new URL("../../NOTICE", import.meta.url), new URL("NOTICE", output));
await cp(new URL("../apps/desktop/dist-live/", import.meta.url), new URL("shell/", output), {
  recursive: true,
});
for (const name of ["model-settings.html", "model-settings.css", "model-settings.js", "starting.html", "starting.css", "computers.html", "computers.css", "computers.js"]) {
  await cp(new URL(`../apps/desktop-dev/${name}`, import.meta.url), new URL(name, output));
}
const commit = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: root,
  encoding: "utf8",
}).trim();
const info = {
  channel: "development",
  version: config.version,
  productName: config.productName,
  identifier: config.identifier,
  commit,
  data: "bundled Python backend with local persistent data; separate preview fixtures",
  updates: "manual-development-draft",
  stable: {
    version: stable.version,
    url: "https://github.com/DingHappy/VortoOne/releases/latest",
  },
};
await writeFile(
  new URL("desktop-channel.json", output),
  JSON.stringify(info, null, 2) + "\n",
);
const escape = (s) =>
  String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll('"', "&quot;");
await writeFile(
  new URL("versions.html", output),
  `<!doctype html><html lang="zh-Hans"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VortoOne Dev · 版本管理</title>
<link rel="stylesheet" href="version-manager.css"><main><h1>版本管理</h1><p>稳定使用与开发验收，分别管理。</p>
<section><h2>公开稳定版</h2><strong>VortoOne ${escape(stable.version)}</strong><p>已发布的正式桌面 App，继续使用现有自动更新。</p><p>应用：VortoOne.app</p></section>
<section><h2>当前开发版</h2><strong>VortoOne Dev ${escape(config.version)}</strong><p>内置 Python 后端 · 本机持久数据 · 安装后即可配置模型</p><p>应用：VortoOne Dev.app</p><p>通过独立开发安装包更新；发布草稿由你管理。</p></section>
<p>在菜单「开发版」中打开公开稳定版下载或 GitHub 发布管理。</p><details><summary>构建信息</summary><p>提交：${escape(commit)}</p><p>应用标识：${escape(config.identifier)}</p></details></main></html>`,
);
await writeFile(
  new URL("version-manager.css", output),
  `:root{color-scheme:light dark;font:15px -apple-system,BlinkMacSystemFont,sans-serif}body{margin:0;background:light-dark(#fafafa,#171717);color:light-dark(#202020,#eee)}main{max-width:720px;margin:44px auto;padding:0 28px}h1{font-size:28px}h2{font-size:17px;margin-top:0}p{line-height:1.7;color:light-dark(#666,#bbb)}section{border:1px solid light-dark(#ddd,#444);border-radius:14px;padding:22px;margin:18px 0}strong{font-size:20px}details{margin-top:22px;overflow-wrap:anywhere}`,
);
console.log(
  `Prepared ${config.productName} ${config.version}: ${fileURLToPath(output)}`,
);
