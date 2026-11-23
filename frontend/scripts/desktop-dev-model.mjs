import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export const DEV_ID = "com.vorto.vortoone.dev";
export const DEV_NAME = "VortoOne Dev";
export const RELEASES = "https://github.com/DingHappy/VortoOne/releases";
/** Base UI resources. The trusted bundle command adds the pinned onedir backend and its notices at build time. */
export const UI_RESOURCES = { "../../web/dist": "web" };

const DEV_VERSION =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-dev\.([1-9]\d*)$/;

export function developmentVersion(raw) {
  const match = DEV_VERSION.exec(raw);
  if (!match) throw new Error("Development version must be x.y.z-dev.N");
  const parts = match.slice(1).map(Number);
  if (!parts.every(Number.isSafeInteger))
    throw new Error("Version component is too large");
  return parts;
}

export function nextDevelopmentVersion(current, next) {
  const before = developmentVersion(current),
    after = developmentVersion(next);
  for (let i = 0; i < before.length; i++) {
    if (after[i] > before[i]) return next;
    if (after[i] < before[i])
      throw new Error("Development version must increase");
  }
  throw new Error("Development version must increase");
}

export function checkDevConfig(config, stable) {
  if (config.identifier !== DEV_ID || config.productName !== DEV_NAME)
    throw new Error(
      "Development app must use its independent name and identifier",
    );
  if (
    config.identifier === stable.identifier ||
    config.productName === stable.productName
  )
    throw new Error("Development and stable apps must remain separate");
  developmentVersion(config.version);
  // 只有打包脚本可以加入内置后端和来自本项目的原生辅助程序。
  const resources = JSON.stringify(config.bundle?.resources ?? null);
  if (
    config.plugins?.updater ||
    config.bundle?.externalBin ||
    (resources !== "null" && resources !== JSON.stringify(UI_RESOURCES))
  )
    throw new Error(
      "Base development config accepts the UI only; the bundle command owns runtime resources",
    );
  return config;
}

export function checkStableConfig(config, previous) {
  if (config.identifier !== previous.identifier || config.productName !== previous.productName ||
      config.identifier === DEV_ID || config.productName === DEV_NAME ||
      config.mainBinaryName !== "vortoone")
    throw new Error("Stable Desktop must retain the released product identity and have its own executable");
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(config.version))
    throw new Error("Stable Desktop requires an x.y.z release version");
  const updater = config.plugins?.updater;
  const publishedUpdater = previous.plugins?.updater;
  if (!publishedUpdater?.pubkey || updater?.pubkey !== publishedUpdater.pubkey ||
      updater?.requireSignedVersion !== true || !Array.isArray(updater?.endpoints) ||
      JSON.stringify(updater.endpoints) !== JSON.stringify(publishedUpdater.endpoints))
    throw new Error("Stable Desktop requires the published updater key, endpoint priority and a signed version");
  return config;
}

export function digestFile(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

export function digestTree(root) {
  const hash = createHash("sha256");
  function visit(path, relative) {
    const stat = lstatSync(path);
    if (stat.isSymbolicLink())
      throw new Error(`Unexpected symlink in app: ${relative}`);
    if (stat.isDirectory()) {
      for (const name of readdirSync(path).sort())
        visit(join(path, name), `${relative}/${name}`);
    } else if (stat.isFile()) {
      hash.update(relative + "\0" + digestFile(path) + "\n");
    }
  }
  visit(root, "");
  return hash.digest("hex");
}

export function devReleaseNotes({ version, commit, stableVersion }) {
  return (
    `VortoOne Dev ${version}\n\n独立的桌面界面开发版，可与公开稳定版并排安装。\n\n` +
    `- 新桌面布局与史莱姆颜色、眼型、表情和配饰。\n- 本地打包资源，包含桌面、角色编辑、偏好设置预览和版本管理。\n` +
    `- 内置独立 Python 后端，安装后默认连接真实本机数据，无需源码、系统 Python 或 backend.json。\n- 首次启动可在模型连接窗口填写服务地址、模型名和 API Key。示例数据界面仍可从菜单单独打开。\n\n公开稳定版继续使用 ${stableVersion}。开发版安装包名为 VortoOne Dev，应用标识为 ${DEV_ID}。\n\n` +
    `源码提交：${commit}\n\n本版本由维护者保留为草稿；需要提供给测试者时，可在 GitHub Release 中发布为预发布。正式用户继续使用公开稳定版更新通道。\n`
  );
}
