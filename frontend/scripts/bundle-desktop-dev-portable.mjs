import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { checkDevConfig, checkStableConfig, digestFile, UI_RESOURCES } from "./desktop-dev-model.mjs";

const frontend = fileURLToPath(new URL("..", import.meta.url));
const repo = fileURLToPath(new URL("../..", import.meta.url));
const app = join(frontend, "apps", "desktop-dev");
const tauri = join(app, "src-tauri");
const sidecarTauri = join(repo, "desktop", "src-tauri");
const readConfig = (path) => JSON.parse(readFileSync(path, "utf8"));
const stable = process.argv.includes("--stable");
const development = readConfig(join(tauri, "tauri.conf.json"));
const legacy = readConfig(join(sidecarTauri, "tauri.conf.json"));
checkDevConfig(development, legacy);
const stableConfig = stable
  ? checkStableConfig(readConfig(join(tauri, "tauri.stable.conf.json")), legacy)
  : null;
const config = stableConfig || development;
const target = {
  win32: { triple: "x86_64-pc-windows-msvc", bundle: "nsis", platform: "windows-x64", extension: ".exe" },
  linux: { triple: "x86_64-unknown-linux-gnu", bundle: "deb", platform: "ubuntu-amd64", extension: ".deb" },
}[process.platform];
if (!target || process.arch !== "x64") {
  throw new Error("Run this builder on native Windows x64 or Ubuntu amd64");
}
const host = execFileSync("rustc", ["--print", "host-tuple"], { cwd: repo, encoding: "utf8" }).trim();
if (host !== target.triple) throw new Error(`Expected Rust host ${target.triple}, got ${host}`);
const commit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" }).trim();
if (execFileSync("git", ["status", "--porcelain"], { cwd: repo, encoding: "utf8" }).trim()) {
  throw new Error("Build the installer from a clean, committed checkout");
}

execFileSync(process.execPath, [join(repo, "desktop", "scripts", "build-sidecar.mjs")], {
  cwd: repo,
  stdio: "inherit",
});
const runtimeManifest = JSON.parse(readFileSync(
  join(sidecarTauri, "target", "sidecar", target.triple, "manifest.json"), "utf8",
));
if (stable && (runtimeManifest.runtimeEnvironment !== "managed" ||
    runtimeManifest.pythonProvenance?.verifiedArchive !== true)) {
  throw new Error("Stable installers require the exact managed Python archive for their target");
}
const buildDir = join(tauri, "target");
const channel = stable ? "stable" : "development";
const resources = {
  ...UI_RESOURCES,
  [join(sidecarTauri, "binaries", "runtime")]: "runtime/",
  [join(sidecarTauri, "resources", "THIRD_PARTY_NOTICES.txt")]: "THIRD_PARTY_NOTICES.txt",
};
const overlay = join(buildDir, `bundle-${target.platform}.json`);
mkdirSync(buildDir, { recursive: true });
writeFileSync(overlay, JSON.stringify({ ...stableConfig, bundle: { ...stableConfig?.bundle, resources } }));
// Pass a config file, so paths with spaces and the Windows separators never pass through shell quoting.
execFileSync(process.execPath, [
  join(frontend, "node_modules", "@tauri-apps", "cli", "tauri.js"),
  "build", "--ci", "--bundles", target.bundle, "--config", overlay, "--", "--locked",
], {
  cwd: app,
  env: { ...process.env, CARGO_TARGET_DIR: buildDir },
  stdio: "inherit",
});

const bundleDir = join(buildDir, "release", "bundle", target.bundle);
const generated = readdirSync(bundleDir).filter((name) =>
  name.includes(`_${config.version}_`) && name.endsWith(target.extension),
);
if (generated.length !== 1) throw new Error(`Expected one ${target.bundle} installer for ${config.version}`);
const output = stable
  ? join(buildDir, "release", "channel", "stable", target.platform)
  : join(buildDir, "release", "channel", target.platform);
mkdirSync(output, { recursive: true });
const name = `${stable ? "VortoOne" : "VortoOne_Dev"}_${config.version}_${target.platform}${target.extension}`;
const installer = join(output, name);
copyFileSync(join(bundleDir, generated[0]), installer);
const sha256 = digestFile(installer);
writeFileSync(join(output, "SHA256SUMS"), `${sha256}  ${name}\n`);
writeFileSync(join(output, stable ? "desktop-release.json" : "desktop-dev.json"), JSON.stringify({
  channel,
  identifier: config.identifier,
  version: config.version,
  commit,
  platform: target.platform,
  installer: name,
  sha256,
  runtime: runtimeManifest,
}, null, 2) + "\n");
console.log(`Built ${installer}`);
