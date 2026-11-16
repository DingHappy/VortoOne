import { execFileSync, spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  checkDevConfig,
  DEV_ID,
  DEV_NAME,
  RELEASES,
  digestFile,
  digestTree,
  devReleaseNotes,
  nextDevelopmentVersion,
} from "./desktop-dev-model.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const repo = fileURLToPath(new URL("../..", import.meta.url));
const appRoot = join(root, "apps/desktop-dev");
const tauri = join(appRoot, "src-tauri");
const config = JSON.parse(readFileSync(join(tauri, "tauri.conf.json")));
const stable = JSON.parse(
  readFileSync(join(repo, "desktop/src-tauri/tauri.conf.json")),
);
checkDevConfig(config, stable);
const app = join(tauri, "target/release/bundle/macos", `${DEV_NAME}.app`);
const out = join(tauri, "target/release/channel");
const target = join("/Applications", `${DEV_NAME}.app`);
const tag = `v${config.version}`;
const dmg = join(out, `VortoOne_Dev_${config.version}_aarch64.dmg`);
const manifest = join(out, "desktop-dev.json");
const checksum = join(out, `vortoone-dev-${config.version}-SHA256SUMS`);
const notes = join(out, "release-notes.md");
function run(cmd, args, options = {}) {
  return execFileSync(cmd, args, { cwd: repo, encoding: "utf8", ...options });
}
const commit = () => run("git", ["rev-parse", "HEAD"]).trim();
function mainOnly() {
  run("git", ["merge-base", "--is-ancestor", "HEAD", "origin/main"]);
  if (run("git", ["status", "--porcelain"]).trim())
    throw new Error(
      "Commit changes and merge to main before packaging or publishing",
    );
}
function installed(path) {
  if (!existsSync(path)) return null;
  return {
    path,
    identifier: run("plutil", [
      "-extract",
      "CFBundleIdentifier",
      "raw",
      join(path, "Contents/Info.plist"),
    ]).trim(),
    version: run("plutil", [
      "-extract",
      "CFBundleShortVersionString",
      "raw",
      join(path, "Contents/Info.plist"),
    ]).trim(),
  };
}
function inspect(path) {
  const result = installed(path);
  if (result?.identifier !== DEV_ID || result.version !== config.version)
    throw new Error("Unexpected development bundle identity/version");
  run("codesign", ["--verify", "--deep", "--strict", path]);
  return { ...result, sha256Tree: digestTree(path) };
}
function identity() {
  const text = run("security", ["find-identity", "-v", "-p", "codesigning"]);
  const ids = [
    ...text.matchAll(/\b([A-Fa-f0-9]{40})\s+"Developer ID Application:[^"]+"/g),
  ].map((m) => m[1]);
  if (ids.length !== 1)
    throw new Error(
      "A single Developer ID Application identity is required for the development installer",
    );
  return ids[0];
}
function verifyPackage() {
  const built = JSON.parse(readFileSync(manifest));
  if (
    built.commit !== commit() ||
    built.channel !== "development" ||
    built.version !== config.version
  )
    throw new Error("Rebuild development package from this main commit");
  if (
    built.dmgSha256 !== digestFile(dmg) ||
    built.appSha256Tree !== inspect(app).sha256Tree
  )
    throw new Error("Development package content changed after validation");
  return built;
}
const action = process.argv[2] || "status";
if (action === "status") {
  console.log(
    JSON.stringify(
      {
        stable: {
          ...installed("/Applications/VortoOne.app"),
          release: `${RELEASES}/latest`,
        },
        development: {
          version: config.version,
          installed: installed(target),
          release: `${RELEASES}/tag/${tag}`,
          updates: "manual-development-draft",
        },
        manage: RELEASES,
      },
      null,
      2,
    ),
  );
} else if (action === "version") {
  const version = nextDevelopmentVersion(config.version, process.argv[3] || "");
  const cargoPath = join(tauri, "Cargo.toml"),
    lockPath = join(tauri, "Cargo.lock");
  const cargo = readFileSync(cargoPath, "utf8"),
    lock = readFileSync(lockPath, "utf8");
  const lockVersion =
    /(\[\[package\]\]\nname = "vortoone-desktop-dev"\nversion = ")([^"]+)(")/;
  if (
    cargo.match(/^version = "([^"]+)"/m)?.[1] !== config.version ||
    lock.match(lockVersion)?.[2] !== config.version
  )
    throw new Error(
      "Development version files are inconsistent; reconcile them before bumping",
    );
  writeFileSync(
    join(tauri, "tauri.conf.json"),
    JSON.stringify({ ...config, version }, null, 2) + "\n",
  );
  writeFileSync(
    cargoPath,
    cargo.replace(/^version = "[^"]+"/m, `version = "${version}"`),
  );
  writeFileSync(
    lockPath,
    lock.replace(
      lockVersion,
      (_, prefix, old, suffix) => prefix + version + suffix,
    ),
  );
  console.log(
    `Development version: ${config.version} -> ${version}; commit and merge before building.`,
  );
} else if (action === "manage") {
  run("open", [RELEASES]);
} else if (action === "bundle") {
  mainOnly();
  if (process.platform !== "darwin" || process.arch !== "arm64")
    throw new Error("This installer currently targets Apple Silicon macOS");
  const signing = identity();
  const buildEnv = { ...process.env };
  for (const key of Object.keys(buildEnv)) {
    if (/^APPLE_(ID|PASSWORD|APP_SPECIFIC_PASSWORD|API_(KEY|ISSUER))/.test(key))
      delete buildEnv[key];
  }
  // App only: sign the independent bundle, then create a DMG from that exact bundle.
  run(
    join(root, "node_modules/.bin/tauri"),
    [
      "build",
      "--bundles",
      "app",
      "--ci",
      "--config",
      JSON.stringify({ bundle: { macOS: { signingIdentity: signing } } }),
      "--",
      "--locked",
    ],
    { cwd: appRoot, stdio: "inherit", env: buildEnv },
  );
  const built = inspect(app);
  mkdirSync(out, { recursive: true });
  const staging = mkdtempSync(join(tmpdir(), "vortoone-dev-dmg-"));
  try {
    run("ditto", [app, join(staging, `${DEV_NAME}.app`)]);
    symlinkSync("/Applications", join(staging, "Applications"));
    rmSync(dmg, { force: true });
    run(
      "hdiutil",
      [
        "create",
        "-volname",
        DEV_NAME,
        "-srcfolder",
        staging,
        "-format",
        "UDZO",
        "-ov",
        dmg,
      ],
      { stdio: "inherit" },
    );
    run("codesign", ["--force", "--sign", signing, "--timestamp", dmg], {
      stdio: "inherit",
    });
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
  const info = {
    channel: "development",
    version: config.version,
    identifier: DEV_ID,
    commit: commit(),
    data: "in-memory fixtures",
    notarized: false,
    updates: "manual-development-draft",
    appSha256Tree: built.sha256Tree,
    dmgSha256: digestFile(dmg),
  };
  writeFileSync(manifest, JSON.stringify(info, null, 2) + "\n");
  writeFileSync(
    checksum,
    `${digestFile(dmg)}  ${dmg.split("/").pop()}\n${digestFile(manifest)}  desktop-dev.json\n`,
  );
  writeFileSync(
    notes,
    devReleaseNotes({
      version: config.version,
      commit: commit(),
      stableVersion: stable.version,
    }),
  );
  console.log(JSON.stringify({ app, dmg, manifest, checksum }, null, 2));
} else if (action === "install") {
  mainOnly();
  verifyPackage();
  const running = spawnSync(
    "pgrep",
    ["-f", target.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "/Contents/MacOS/"],
    { encoding: "utf8" },
  );
  if (running.status === 0)
    throw new Error("Quit VortoOne Dev before installing its replacement");
  if (running.status !== 1)
    throw new Error("Could not verify that VortoOne Dev is closed");
  const before = installed(target);
  if (before && before.identifier !== DEV_ID)
    throw new Error("Target is not the independent development app");
  const stage = mkdtempSync(join("/Applications", ".vortoone-dev-install-"));
  const candidate = join(stage, `${DEV_NAME}.app`);
  let backup = null;
  let replaced = false;
  let cleanupSafe = true;
  try {
    run("ditto", [app, candidate]);
    if (inspect(candidate).sha256Tree !== inspect(app).sha256Tree)
      throw new Error("Staged application verification failed");
    if (before) {
      const parent = join(
        homedir(),
        "Library/Application Support",
        DEV_ID,
        "backups",
      );
      mkdirSync(parent, { recursive: true });
      backup = join(mkdtempSync(join(parent, "install-")), `${DEV_NAME}.app`);
      run("ditto", [target, backup]);
      if (digestTree(target) !== digestTree(backup))
        throw new Error("Backup verification failed");
      renameSync(target, join(stage, "previous.app"));
    }
    renameSync(candidate, target);
    replaced = true;
    inspect(target);
  } catch (error) {
    try {
      if (replaced) renameSync(target, candidate);
      if (existsSync(join(stage, "previous.app")))
        renameSync(join(stage, "previous.app"), target);
    } catch (rollback) {
      cleanupSafe = false;
      throw new AggregateError(
        [error, rollback],
        `Install rollback failed; retained ${stage} and ${backup}`,
      );
    }
    throw error;
  } finally {
    if (cleanupSafe) rmSync(stage, { recursive: true, force: true });
  }
  console.log(
    JSON.stringify({ installed: installed(target), backup }, null, 2),
  );
} else if (action === "draft") {
  mainOnly();
  verifyPackage();
  run(
    "gh",
    [
      "release",
      "create",
      tag,
      "--repo",
      "DingHappy/VortoOne",
      "--target",
      commit(),
      "--draft",
      "--prerelease",
      "--latest=false",
      "--title",
      `VortoOne Dev ${config.version}`,
      "--notes-file",
      notes,
      dmg,
      manifest,
      checksum,
    ],
    { stdio: "inherit" },
  );
} else {
  throw new Error("Choose status, version, bundle, install, manage or draft");
}
