import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { commitOnMain } from "../../desktop/scripts/release-commit.mjs";
import { dmgAssetName, notarizationOutcome, releaseDmg } from "../../desktop/scripts/release-dmg.mjs";
import { releaseUpdater } from "../../desktop/scripts/updater-release.mjs";
import { checkStableConfig, digestFile, UI_RESOURCES } from "./desktop-dev-model.mjs";
import { signRuntime } from "./standalone-runtime.mjs";

if (process.platform !== "darwin" || process.arch !== "arm64") {
  throw new Error("The stable macOS package builds natively on Apple Silicon");
}
const frontend = fileURLToPath(new URL("..", import.meta.url));
const repo = fileURLToPath(new URL("../..", import.meta.url));
const appRoot = join(frontend, "apps", "desktop-dev");
const tauri = join(appRoot, "src-tauri");
const sidecar = join(repo, "desktop", "src-tauri");
const target = join(tauri, "target");
const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const config = checkStableConfig(
  readJson(join(tauri, "tauri.stable.conf.json")), readJson(join(sidecar, "tauri.conf.json")),
);
const run = (command, args, options = {}) => execFileSync(command, args, {
  cwd: repo, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], ...options,
});
const commit = run("git", ["rev-parse", "HEAD"]).trim();
const onMain = commitOnMain((args) => spawnSync("git", args, { cwd: repo, stdio: "ignore" }), commit);
if (!onMain.onMain) throw new Error(onMain.reason);
if (run("git", ["status", "--porcelain"]).trim()) throw new Error("Build from a clean main checkout");
const identities = [...run("security", ["find-identity", "-v", "-p", "codesigning"])
  .matchAll(/\b([A-Fa-f0-9]{40})\s+"Developer ID Application:[^"]+"/g)];
if (identities.length !== 1) throw new Error("A single Developer ID Application identity is required");
const identity = identities[0][1];
const profile = process.env.VORTOONE_NOTARY_PROFILE || "vortocode-notary";
const notes = process.argv[2] ? readFileSync(process.argv[2], "utf8") : "";
const cli = join(frontend, "node_modules", "@tauri-apps", "cli", "tauri.js");
const env = { ...process.env, CARGO_TARGET_DIR: target };
delete env.VORTOONE_SIDECAR_BOOTSTRAP_PYTHON;
delete env.VORTOONE_SIDECAR_PYTHON;
for (const name of Object.keys(env)) {
  if (name.startsWith("APPLE_")) delete env[name];
}
run(process.execPath, [join(repo, "desktop", "scripts", "build-sidecar.mjs")], { env, stdio: "inherit" });
const runtime = readJson(join(sidecar, "target", "sidecar", "aarch64-apple-darwin", "manifest.json"));
if (runtime.runtimeEnvironment !== "managed" || !runtime.pythonProvenance?.verifiedArchive) {
  throw new Error("Stable macOS requires the pinned and verified sidecar");
}
const captureDir = join(target, "desktop-capture");
mkdirSync(captureDir, { recursive: true });
const capture = join(captureDir, "vortoone-desktop-capture");
run("xcrun", ["swiftc", "-O", "-parse-as-library", "-target", "arm64-apple-macosx13.0",
  "-module-cache-path", join(captureDir, "swift-cache"), join(appRoot, "desktop-capture.swift"),
  "-o", `${capture}-aarch64-apple-darwin`], { stdio: "inherit" });
const overlay = join(target, "bundle-stable-macos.json");
writeFileSync(overlay, JSON.stringify({ ...config, bundle: {
  ...config.bundle,
  macOS: { ...config.bundle.macOS, signingIdentity: identity },
  externalBin: [capture],
  resources: {
    ...UI_RESOURCES,
    [join(sidecar, "binaries", "runtime")]: "runtime/",
    [join(sidecar, "resources", "THIRD_PARTY_NOTICES.txt")]: "THIRD_PARTY_NOTICES.txt",
  },
} }));
run(process.execPath, [cli, "build", "--bundles", "app", "--ci", "--config", overlay, "--", "--locked"], {
  cwd: appRoot, env, stdio: "inherit",
});
const app = join(target, "release", "bundle", "macos", "VortoOne.app");
const plist = join(app, "Contents", "Info.plist");
for (const [key, value] of Object.entries({
  CFBundleIdentifier: config.identifier,
  CFBundleShortVersionString: config.version,
  CFBundleExecutable: config.mainBinaryName,
})) {
  if (run("plutil", ["-extract", key, "raw", plist]).trim() !== value) {
    throw new Error(`Unexpected macOS bundle property ${key}`);
  }
}
signRuntime(app, identity, repo);
const installed = join(app, "Contents", "MacOS", config.mainBinaryName);
const smoke = JSON.parse(run(installed, ["--check-standalone"]));
if (smoke.standalone !== true || smoke.freshProfile !== true || smoke.login !== true ||
    smoke.ui !== true || smoke.inferenceSent !== false) {
  throw new Error("Standalone macOS smoke check failed");
}
const scratch = mkdtempSync(join(tmpdir(), "vortoone-stable-notary-"));
try {
  const zip = join(scratch, "VortoOne.zip");
  run("ditto", ["-c", "-k", "--keepParent", app, zip]);
  const outcome = notarizationOutcome(run("xcrun", ["notarytool", "submit", zip, "--keychain-profile",
    profile, "--wait", "--timeout", "20m", "--output-format", "json"]));
  if (!outcome.ok) throw new Error(`App notarization failed: ${outcome.status} (${outcome.id || ""})`);
  run("xcrun", ["stapler", "staple", app]);
  run("xcrun", ["stapler", "validate", app]);
  run("spctl", ["--assess", "--type", "execute", app]);
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
const out = join(target, "release", "channel", "stable", "macos-arm64");
mkdirSync(out, { recursive: true });
const installer = dmgAssetName(config.version);
const { sha256 } = releaseDmg({ app, dmg: join(out, installer), version: config.version, profile });
const updater = releaseUpdater({ app, out, version: config.version, notes,
  sign: ([signer, sign, ...args]) => run(process.execPath, [
    cli, signer, sign, "--app-version", config.version, ...args,
  ], { cwd: appRoot, stdio: ["ignore", "ignore", "inherit"] }),
});
const files = [installer, ...Object.values(updater).filter((path) => path !== updater.mirrorLatest).map((path) => path.slice(out.length + 1))];
writeFileSync(join(out, "SHA256SUMS"), files.map((file) => `${digestFile(join(out, file))}  ${file}\n`).join(""));
writeFileSync(join(out, "desktop-release.json"), JSON.stringify({
  channel: "stable", version: config.version, identifier: config.identifier, commit, platform: "macos-arm64",
  installer, sha256, runtime, notarized: true, smoke,
}, null, 2) + "\n");
console.log(`Built signed and notarized macOS assets in ${out}`);
