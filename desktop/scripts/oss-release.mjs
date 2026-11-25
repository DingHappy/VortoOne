#!/usr/bin/env node
// 把这一版的更新包、DMG 和镜像用的 latest.json 传到阿里云 OSS，再从公网取回核对。在 release:dmg、release:updater 之后运行。
//   目录：vortoone/desktop/v<版本>/ 放更新包和 DMG，vortoone/desktop/latest.json 是 App 固定来问的清单。
//   先传包、核对公网取回的 sha256，最后才传 latest.json——任何时刻镜像上的清单都不会指向一个还没传完的包。
// 上传凭据只在发版的这台机器上：macOS 钥匙串条目 vortoone-oss-upload（账户名 = AccessKey ID，密码 = Secret），
// 属于只能读写 bucket `vorto` 下 vortoone/desktop/ 的 RAM 子账号。镜像被人改了也装不进去：App 只认内置公钥签过的包。
// 用法：node scripts/oss-release.mjs
// 新桌面正式版：Mac 产物在 channel/stable/macos-arm64/，「VortoOne stable installer checks」的 Windows、Ubuntu 产物解压到
//   channel/stable/windows-x64/ 与 channel/stable/ubuntu-amd64/。三个平台必须来自同一个提交。
//   node desktop/scripts/oss-release.mjs --stable --stage <空目录>  核对三平台，把 GitHub 发布要传的全部附件（含总校验和）放进去
//   在 GitHub 发布并成为最新版本后：node desktop/scripts/oss-release.mjs --stable  传三平台安装包、校验和，最后传清单
import { execFileSync } from "node:child_process";
import { createHash, createHmac } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
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

/** Windows / Ubuntu 正式版安装包：[平台, 扩展名, Content-Type, 运行时目标]。 */
export const PORTABLE_PLATFORMS = [
  ["windows-x64", ".exe", "application/vnd.microsoft.portable-executable", "x86_64-pc-windows-msvc"],
  ["ubuntu-amd64", ".deb", "application/vnd.debian.binary-package", "x86_64-unknown-linux-gnu"],
];

/** 读 CI 构建的 Windows、Ubuntu 正式版产物，核对它们与 macOS 是同一版、同一个提交。 */
export function portableInstallers(root, { version, identifier, commit }) {
  return PORTABLE_PLATFORMS.map(([platform, extension, type, triple]) => {
    const dir = join(root, platform);
    const name = `VortoOne_${version}_${platform}${extension}`;
    let evidence;
    try {
      evidence = JSON.parse(readFileSync(join(dir, "desktop-release.json"), "utf8"));
    } catch {
      throw new Error(`没有 ${platform} 的正式版构建证据：把「VortoOne stable installer checks」的产物解压到 ${dir}`);
    }
    if (evidence.channel !== "stable" || evidence.platform !== platform || evidence.version !== version
      || evidence.identifier !== identifier || evidence.installer !== name
      || evidence.runtime?.runtimeEnvironment !== "managed" || evidence.runtime?.targetTriple !== triple
      || evidence.runtime?.pythonProvenance?.verifiedArchive !== true) {
      throw new Error(`${platform} 的构建证据不是这一版带固定运行时的正式安装包`);
    }
    if (evidence.commit !== commit) throw new Error(`${platform} 安装包和 macOS 不是同一个提交构建的`);
    const bytes = readFileSync(join(dir, name));
    const sums = readFileSync(join(dir, "SHA256SUMS"), "utf8").trimEnd();
    if (sha256(bytes) !== evidence.sha256 || sums !== `${evidence.sha256}  ${name}`) {
      throw new Error(`${platform} 安装包的 SHA-256 与构建证据不一致`);
    }
    return { name, type, bytes };
  });
}

/** 三个平台的正式版：GitHub 发布的全部附件（含总校验和）和镜像要传的对象。 */
export function stableReleaseCandidate(root, config) {
  const mac = stableMirrorCandidate(join(root, "macos-arm64"), config);
  const portable = portableInstallers(root, { ...config, commit: mac.commit });
  const assets = { ...mac.assets };
  for (const { name, bytes } of portable) assets[name] = { bytes, sha256: sha256(bytes) };
  const sums = Buffer.from(Object.entries(assets).map(([name, asset]) => `${asset.sha256}  ${name}\n`).join(""));
  assets.SHA256SUMS = { bytes: sums, sha256: sha256(sums) };
  const folder = `${PREFIX}/v${mac.version}`;
  const uploads = [
    ...mac.uploads,
    ...portable.map(({ name, type, bytes }) => ({ key: `${folder}/${name}`, type, bytes })),
    { key: `${folder}/SHA256SUMS`, type: "text/plain; charset=utf-8", bytes: sums },
  ];
  return { ...mac, assets, uploads };
}

/** 把 GitHub 发布要传的附件写进一个空目录。 */
export function stageRelease(candidate, dir) {
  mkdirSync(dir, { recursive: true });
  if (readdirSync(dir).length) throw new Error(`${dir} 不是空目录`);
  for (const [name, { bytes }] of Object.entries(candidate.assets)) writeFileSync(join(dir, name), bytes);
  return Object.keys(candidate.assets).map((name) => join(dir, name));
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
  const staging = args.length === 3 && args[1] === "--stage";
  if (args[0] === "--stable" && (args.length === 1 || staging)) {
    const tauri = join(desktop, "..", "frontend", "apps", "desktop-dev", "src-tauri");
    const config = JSON.parse(readFileSync(join(tauri, "tauri.stable.conf.json"), "utf8"));
    const candidate = stableReleaseCandidate(join(tauri, "target", "release", "channel", "stable"), config);
    if (staging) {
      for (const path of stageRelease(candidate, args[2])) console.log(path);
      console.log(`三个平台来自 ${candidate.commit}；在 GitHub 发布 v${candidate.version} 并成为最新版本后再运行 --stable`);
      return;
    }
    await requirePublicStable(candidate);
    await publishMirror(candidate, () => requirePublicStable(candidate));
    return;
  }
  if (args.length) throw new Error("用法：node desktop/scripts/oss-release.mjs [--stable [--stage <空目录>]]");
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
