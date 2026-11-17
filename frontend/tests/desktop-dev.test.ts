import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  checkDevConfig,
  devReleaseNotes,
  nextDevelopmentVersion,
} from "../scripts/desktop-dev-model.mjs";

const config = JSON.parse(
  readFileSync(
    new URL("../apps/desktop-dev/src-tauri/tauri.conf.json", import.meta.url),
    "utf8",
  ),
);
const stable = JSON.parse(
  readFileSync(
    new URL("../../desktop/src-tauri/tauri.conf.json", import.meta.url),
    "utf8",
  ),
);
test("development version management increases only development versions", () => {
  assert.equal(
    nextDevelopmentVersion("0.3.0-dev.1", "0.3.0-dev.2"),
    "0.3.0-dev.2",
  );
  assert.equal(
    nextDevelopmentVersion("0.3.0-dev.2", "0.4.0-dev.1"),
    "0.4.0-dev.1",
  );
  for (const version of [
    "0.3.0",
    "v0.3.0-dev.3",
    "0.3.0-dev.0",
    "0.3.0-dev.01",
    "0.2.9-dev.9",
    "0.3.0-dev.1",
  ])
    assert.throws(() => nextDevelopmentVersion("0.3.0-dev.1", version));
});
test("development packaging rejects stable identities, stable versions and runtime/update resources", () => {
  assert.equal(
    checkDevConfig(config, stable).identifier,
    "com.vorto.vortoone.dev",
  );
  for (const patch of [
    { identifier: stable.identifier },
    { productName: stable.productName },
    { version: stable.version },
    { plugins: { updater: {} } },
    { bundle: { resources: ["runtime"] } },
    { bundle: { resources: { "../../web/dist": "web", "../runtime": "runtime" } } },
    { bundle: { resources: { "../../../src": "web" } } },
    { bundle: { externalBin: ["runner"] } },
  ])
    assert.throws(() => checkDevConfig({ ...config, ...patch }, stable));
  // 界面文件是唯一允许随包的资源；不带也可以
  assert.deepEqual(config.bundle.resources, { "../../web/dist": "web" });
  checkDevConfig({ ...config, bundle: {} }, stable);
});
test("development release notes identify fixtures, the stable version and the exact source", () => {
  const notes = devReleaseNotes({
    version: config.version,
    stableVersion: stable.version,
    commit: "abc123",
  });
  for (const text of [
    "示例数据",
    "无需源码、系统 Python",
    "默认连接真实本机数据",
    "草稿",
    "abc123",
    stable.version,
    config.version,
  ])
    assert.ok(notes.includes(text));
});
