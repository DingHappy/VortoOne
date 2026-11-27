#!/usr/bin/env node
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stableMirrorCandidate, stableReleaseCandidate } from "../../desktop/scripts/oss-release.mjs";
import { PLATFORM, PORTABLE_PLATFORMS, portableAssetName, updaterAssetName } from "../../desktop/scripts/updater-release.mjs";

const configFile = new URL("../../frontend/apps/desktop-dev/src-tauri/tauri.stable.conf.json", import.meta.url);
const VERSION = /^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$/;
const MAX_DOWNLOAD = 512 * 1024 * 1024;

export function stageRollout(bundle, dataDirectory, allPlatforms = false) {
  const macBundle = join(bundle, "macos-arm64");
  const { version } = JSON.parse(readFileSync(join(macBundle, "desktop-release.json"), "utf8"));
  if (typeof version !== "string" || !VERSION.test(version)) throw new Error("需要正式版本号");
  const config = { ...JSON.parse(readFileSync(configFile, "utf8")), version };
  const candidate = allPlatforms ? stableReleaseCandidate(bundle, config) : stableMirrorCandidate(macBundle, config);
  const latest = JSON.parse(candidate.assets["latest.json"].bytes.toString("utf8"));
  const targets = [[PLATFORM, updaterAssetName(version)]];
  if (allPlatforms) {
    targets.push(...PORTABLE_PLATFORMS.map(({ target, platform, extension }) => [
      target, portableAssetName(version, platform, extension),
    ]));
  }

  const releases = join(dataDirectory, "releases");
  mkdirSync(releases, { recursive: true, mode: 0o700 });
  const destination = join(releases, version);
  if (existsSync(destination)) throw new Error(`版本 ${version} 已暂存，请使用新版本号`);
  const incoming = mkdtempSync(join(releases, ".incoming-"));
  try {
    const platforms = {};
    for (const [target, filename] of targets) {
      const { bytes, sha256 } = candidate.assets[filename];
      if (!bytes.length || bytes.length > MAX_DOWNLOAD) throw new Error(`${filename} 超出灰度更新大小限制`);
      const signature = latest.platforms[target].signature;
      writeFileSync(join(incoming, filename), bytes, { mode: 0o600, flag: "wx" });
      platforms[target] = { filename, signature, sha256 };
    }
    writeFileSync(join(incoming, "release.json"), JSON.stringify({
      version, commit: candidate.commit, notes: latest.notes, created_at: latest.pub_date, platforms,
    }, null, 2) + "\n", { mode: 0o600, flag: "wx" });
    renameSync(incoming, destination);
    return destination;
  } catch (error) {
    rmSync(incoming, { recursive: true, force: true });
    throw error;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const [bundle, dataDirectory, ...flags] = process.argv.slice(2);
  if (!bundle || !dataDirectory || (flags.length !== 0 && (flags.length !== 1 || flags[0] !== "--all-platforms"))) {
    console.error("用法: node deploy/desktop-rollout/stage.mjs <构建产物 stable 目录> <灰度数据目录> [--all-platforms]");
    process.exitCode = 2;
  } else {
    try {
      console.log(stageRollout(resolve(bundle), resolve(dataDirectory), flags.length > 0));
    } catch (error) {
      console.error(error.message);
      process.exitCode = 1;
    }
  }
}
