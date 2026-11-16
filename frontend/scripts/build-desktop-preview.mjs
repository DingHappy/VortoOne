import { execFileSync } from "node:child_process";
import { cp, copyFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
for (const args of [
  [
    "run",
    "build",
    "--workspace",
    "@agenteschat/web",
    "--",
    "--mode",
    "desktop-preview",
  ],
  ["run", "build", "--workspace", "@agenteschat/desktop", "--", "--mode", "desktop-preview"],
])
  execFileSync(npm, args, { cwd: root, stdio: "inherit" });

const output = new URL("../apps/web/dist-preview/", import.meta.url);
await cp(
  new URL("../apps/desktop/dist/", import.meta.url),
  new URL("windows/", output),
  { recursive: true },
);
await copyFile(
  new URL("desktop-preview.html", output),
  new URL("index.html", output),
);
const commit = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: root,
  encoding: "utf8",
}).trim();
await writeFile(
  new URL("version.json", output),
  JSON.stringify(
    {
      product: "VortoOne",
      kind: "isolated-desktop-preview",
      commit,
      builtAt: new Date().toISOString(),
      data: "in-memory fixtures",
      entries: {
        desktop: "./?lang=zh-Hans",
        characters: "./character-lab.html",
        preferences:
          "./windows/?window=prefs&scenario=selfhost&lang=zh-Hans&bare=1",
      },
    },
    null,
    2,
  ) + "\n",
);
console.log(`Static desktop preview: ${fileURLToPath(output)}`);
