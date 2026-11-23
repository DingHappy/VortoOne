#!/usr/bin/env node
// 把这一版的更新包、DMG 和镜像用的 latest.json 传到阿里云 OSS，再从公网取回核对。在 release:dmg、release:updater 之后运行。
//   目录：vortoone/desktop/v<版本>/ 放更新包和 DMG，vortoone/desktop/latest.json 是 App 固定来问的清单。
//   先传包、核对公网取回的 sha256，最后才传 latest.json——任何时刻镜像上的清单都不会指向一个还没传完的包。
// 上传凭据只在发版的这台机器上：macOS 钥匙串条目 vortoone-oss-upload（账户名 = AccessKey ID，密码 = Secret），
// 属于只能读写 bucket `vorto` 下 vortoone/desktop/ 的 RAM 子账号。镜像被人改了也装不进去：App 只认内置公钥签过的包。
// 用法：node scripts/oss-release.mjs
// 新桌面正式版在 GitHub 发布并成为最新版本后：node desktop/scripts/oss-release.mjs --stable
import { execFileSync } from "node:child_process";
import { createHash, createHmac } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";
import {
  GITHUB_LATEST, MIRROR_BASE, REPO, dmgAssetName, mirrorAssetBase, updaterAssetName, updaterManifest,
} from "./updater-release.mjs";

export const KEYCHAIN_SERVICE = "vortoone-oss-upload";
const mirror = new URL(MIRROR_BASE);
export const BUCKET = mirror.hostname.split(".")[0];
export const PREFIX = mirror.pathname.replace(/^\/+|\/+$/g, "");

/** OSS V1 签名的待签字符串（不带 x-oss-* 头）：动词、Content-MD5、Content-Type、Date、/bucket/对象。 */
export function stringToSign({ verb, md5 = "", type = "", date, key }) {
  return `${verb}\n${md5}\n${type}\n${date}\n/${BUCKET}/${key}`;
}

function credentials() {
  const read = (args) => execFileSync("security", ["find-generic-password", "-s", KEYCHAIN_SERVICE, ...args],
    { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  let attrs, secret;
  try {
    attrs = read([]);
    secret = read(["-w"]).trim();
  } catch {
    throw new Error(`钥匙串里没有 ${KEYCHAIN_SERVICE}：在「终端」里运行 `
      + `security add-generic-password -s ${KEYCHAIN_SERVICE} -a <AccessKeyId> -w`);
  }
  const id = /"acct"<blob>="([^"]+)"/.exec(attrs)?.[1];
  if (!id || !secret) throw new Error(`钥匙串条目 ${KEYCHAIN_SERVICE} 缺 AccessKey ID 或 Secret`);
  return { id, secret };
}

async function put({ id, secret }, key, body, type, cacheControl) {
  const md5 = createHash("md5").update(body).digest("base64");
  const date = new Date().toUTCString();
  const signature = createHmac("sha1", secret).update(stringToSign({ verb: "PUT", md5, type, date, key })).digest("base64");
  const headers = { "Content-MD5": md5, "Content-Type": type, Date: date, Authorization: `OSS ${id}:${signature}` };
  if (cacheControl) headers["Cache-Control"] = cacheControl;
  const res = await fetch(`https://${mirror.host}/${key}`, { method: "PUT", body, headers });
  if (!res.ok) {
    const code = /<Code>([A-Za-z][A-Za-z0-9]{0,63})<\/Code>/.exec(await res.text())?.[1] || "";
    throw new Error(`上传 ${key} 失败：HTTP ${res.status} ${code}`);
  }
}

async function publicBytes(url) {
  const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`公网取不回 ${url}：HTTP ${res.status}（bucket 要是公共读）`);
  return Buffer.from(await res.arrayBuffer());
}

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

/** 这一版要传的包：[本地路径, 镜像上的对象路径, Content-Type]。 */
export function releaseUploads(version, bundle, { flat = false } = {}) {
  const folder = `${PREFIX}/v${version}`;
  return [
    [join(bundle, flat ? "" : "updater", updaterAssetName(version)), `${folder}/${updaterAssetName(version)}`, "application/gzip"],
    [join(bundle, flat ? "" : "dmg", dmgAssetName(version)), `${folder}/${dmgAssetName(version)}`, "application/x-apple-diskimage"],
  ];
}

export function stableMirrorCandidate(bundle, { version, identifier }) {
  if (!/^\d+\.\d+\.\d+$/.test(version) || identifier !== "com.vorto.vortoone") {
    throw new Error("新版 OSS 发布需要正式版应用配置");
  }
  const evidence = JSON.parse(readFileSync(join(bundle, "desktop-release.json"), "utf8"));
  const archive = updaterAssetName(version);
  const installer = dmgAssetName(version);
  const expectedSmoke = { standalone: true, freshProfile: true, login: true, ui: true, inferenceSent: false };
  if (evidence.channel !== "stable" || evidence.platform !== "macos-arm64"
    || evidence.version !== version || evidence.identifier !== identifier || evidence.installer !== installer
    || !/^[a-f0-9]{40}$/.test(evidence.commit) || evidence.notarized !== true
    || evidence.runtime?.runtimeEnvironment !== "managed" || evidence.runtime?.targetTriple !== "aarch64-apple-darwin"
    || evidence.runtime?.pythonProvenance?.verifiedArchive !== true
    || !Object.entries(expectedSmoke).every(([key, value]) => evidence.smoke?.[key] === value)) {
    throw new Error("macOS 构建证据缺少正式版标识、公证、运行时来源或独立启动验证");
  }
  const assets = Object.fromEntries([installer, archive, `${archive}.sig`, "latest.json"].map((name) => {
    const bytes = readFileSync(join(bundle, name));
    return [name, { bytes, sha256: sha256(bytes) }];
  }));
  const checksums = Object.entries(assets).map(([name, asset]) => `${asset.sha256}  ${name}`).sort();
  const sums = readFileSync(join(bundle, "SHA256SUMS"), "utf8").trimEnd().split(/\r?\n/).sort();
  if (!isDeepStrictEqual(sums, checksums) || evidence.sha256 !== assets[installer].sha256) {
    throw new Error("macOS 正式版产物的 SHA-256 与构建证据不一致");
  }

  const manifest = readFileSync(join(bundle, "oss", "latest.json"));
  const github = JSON.parse(assets["latest.json"].bytes.toString("utf8"));
  const fields = { version, notes: github.notes, pubDate: github.pub_date,
    signature: assets[`${archive}.sig`].bytes.toString("utf8") };
  if (!isDeepStrictEqual(github, updaterManifest(fields))
    || !isDeepStrictEqual(JSON.parse(manifest.toString("utf8")), updaterManifest({ ...fields, base: mirrorAssetBase(version) }))) {
    throw new Error("GitHub 与 OSS 更新清单必须使用同一版已签名的更新包");
  }
  const uploads = releaseUploads(version, bundle, { flat: true }).map(([local, key, type]) => (
    { key, type, bytes: assets[basename(local)].bytes }
  ));
  return { version, commit: evidence.commit, assets, uploads, manifest };
}

// Use the release operator's GitHub CLI for API metadata so a shared-IP
// rate limit cannot delay a rollout. The updater file is still read publicly.
function stableReleaseRead(url) {
  const origin = "https://api.github.com/";
  if (!url.startsWith(`${origin}repos/${REPO}/`)) return publicBytes(url);
  return execFileSync("gh", ["api", "--hostname", "github.com", url.slice(origin.length)], {
    stdio: ["ignore", "pipe", "pipe"],
  });
}

export async function requirePublicStable(candidate, read = stableReleaseRead) {
  const api = `https://api.github.com/repos/${REPO}`;
  const json = async (path) => JSON.parse((await read(`${api}/${path}`)).toString("utf8"));
  const tag = `v${candidate.version}`;
  const release = await json("releases/latest");
  if (release.tag_name !== tag || release.draft !== false || release.prerelease !== false) {
    throw new Error(`先将 ${tag} 发布为 GitHub 最新正式版`);
  }
  let target = (await json(`git/ref/tags/${tag}`)).object;
  if (target?.type === "tag" && /^[a-f0-9]{40}$/.test(target.sha)) {
    target = (await json(`git/tags/${target.sha}`)).object;
  }
  if (target?.type !== "commit" || target.sha !== candidate.commit) {
    throw new Error("GitHub 发布标签与本地构建提交不一致");
  }
  for (const [name, { bytes, sha256: digest }] of Object.entries(candidate.assets)) {
    const published = release.assets?.find((asset) => asset.name === name);
    if (published?.state !== "uploaded" || published.digest !== `sha256:${digest}` || published.size !== bytes.length) {
      throw new Error(`GitHub 发布附件 ${name} 与本地正式版不一致`);
    }
  }
  if (sha256(await read(GITHUB_LATEST)) !== candidate.assets["latest.json"].sha256) {
    throw new Error("GitHub 更新入口尚未提供这一版的更新清单");
  }
}

async function publishMirror({ version, uploads, manifest }, beforeLatest) {
  const creds = credentials();
  for (const { bytes, key, type } of uploads) {
    const url = `${mirror.origin}/${key}`;
    await put(creds, key, bytes, type);
    if (sha256(await publicBytes(url)) !== sha256(bytes)) throw new Error(`公网取回的 ${url} 与本地不一致`);
    console.log(`✓ ${url}（${(bytes.length / 1048576).toFixed(1)} MB，sha256 一致）`);
  }

  await beforeLatest?.();
  await put(creds, `${PREFIX}/latest.json`, manifest, "application/json", "no-cache");
  if (sha256(await publicBytes(`${MIRROR_BASE}/latest.json`)) !== sha256(manifest)) {
    throw new Error("镜像最新清单和已上传的正式版清单不一致");
  }
  console.log(`✓ 清单 ${MIRROR_BASE}/latest.json → ${version}`);
}

async function main() {
  const desktop = dirname(dirname(fileURLToPath(import.meta.url)));
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === "--stable") {
    const tauri = join(desktop, "..", "frontend", "apps", "desktop-dev", "src-tauri");
    const config = JSON.parse(readFileSync(join(tauri, "tauri.stable.conf.json"), "utf8"));
    const candidate = stableMirrorCandidate(join(tauri, "target", "release", "channel", "stable", "macos-arm64"), config);
    await requirePublicStable(candidate);
    await publishMirror(candidate, () => requirePublicStable(candidate));
    return;
  }
  if (args.length) throw new Error("用法：node desktop/scripts/oss-release.mjs [--stable]");
  const version = JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8")).version;
  const bundle = join(desktop, "src-tauri", "target", "release", "bundle");
  const uploads = releaseUploads(version, bundle);
  const manifestPath = join(bundle, "updater", "oss", "latest.json");
  for (const path of [...uploads.map(([local]) => local), manifestPath]) {
    if (!existsSync(path)) throw new Error(`没有 ${path}：先运行 npm run release:dmg 和 release:updater`);
  }
  const bytes = readFileSync(manifestPath);
  const manifest = JSON.parse(bytes.toString("utf8"));
  const assetUrl = `${mirrorAssetBase(version)}/${updaterAssetName(version)}`;
  if (manifest.version !== version || Object.values(manifest.platforms || {})[0]?.url !== assetUrl) {
    throw new Error(`${manifestPath} 不是 ${version} 的镜像清单：重新运行 npm run release:updater`);
  }
  await publishMirror({
    version,
    uploads: uploads.map(([local, key, type]) => ({ key, type, bytes: readFileSync(local) })),
    manifest: bytes,
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    console.error(`上传 OSS 镜像失败：${error instanceof Error ? error.message : error}`);
    process.exit(1);
  });
}
