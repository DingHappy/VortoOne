import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export const DEV_ID = "com.vorto.vortoone.dev";
export const DEV_NAME = "VortoOne Dev";
export const RELEASES = "https://github.com/DingHappy/VortoOne/releases";

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
  if (
    config.plugins?.updater ||
    config.bundle?.externalBin ||
    config.bundle?.resources
  )
    throw new Error(
      "UI preview must not bundle an updater, runner or runtime resources",
    );
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
    `- 当前使用示例数据，尚未连接正式后端。\n\n公开稳定版继续使用 ${stableVersion}。开发版安装包名为 VortoOne Dev，应用标识为 ${DEV_ID}。\n\n` +
    `源码提交：${commit}\n\n本版本由维护者保留为草稿；需要提供给测试者时，可在 GitHub Release 中发布为预发布。正式用户继续使用公开稳定版更新通道。\n`
  );
}
