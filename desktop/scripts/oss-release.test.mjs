import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BUCKET, PREFIX, releaseUploads, requirePublicStable, stableMirrorCandidate, stringToSign } from "./oss-release.mjs";
import { GITHUB_LATEST, MIRROR_BASE, REPO, dmgAssetName, mirrorAssetBase, updaterAssetName, updaterManifest } from "./updater-release.mjs";

test("uploads go to the bucket and folder the app downloads from", () => {
  assert.equal(`https://${BUCKET}.oss-cn-hangzhou.aliyuncs.com/${PREFIX}`, MIRROR_BASE);
  assert.equal(BUCKET, "vorto");
  assert.equal(PREFIX, "vortoone/desktop");
});

test("the V1 string to sign covers verb, body hash, type, date and the object", () => {
  assert.equal(
    stringToSign({ verb: "PUT", md5: "bWQ1", type: "application/json", date: "Mon, 09 Nov 2026 08:00:00 GMT",
      key: "vortoone/desktop/latest.json" }),
    "PUT\nbWQ1\napplication/json\nMon, 09 Nov 2026 08:00:00 GMT\n/vorto/vortoone/desktop/latest.json",
  );
});

test("each release goes into its own v<version>/ folder: update archive and DMG", () => {
  assert.deepEqual(releaseUploads("0.2.56", "/b").map(([local, key]) => [local, key]), [
    ["/b/updater/VortoOne_0.2.56_aarch64.app.tar.gz", "vortoone/desktop/v0.2.56/VortoOne_0.2.56_aarch64.app.tar.gz"],
    ["/b/dmg/VortoOne_0.2.56_aarch64.dmg", "vortoone/desktop/v0.2.56/VortoOne_0.2.56_aarch64.dmg"],
  ]);
});

function stableFixture(t) {
  const bundle = mkdtempSync(join(tmpdir(), "vorto-oss-release-"));
  t.after(() => rmSync(bundle, { recursive: true, force: true }));
  const config = { version: "0.3.0", identifier: "com.vorto.vortoone" };
  const { version } = config;
  const archive = updaterAssetName(version);
  const installer = dmgAssetName(version);
  const fields = { version, notes: "Fresh install", pubDate: "2026-11-23T10:00:00Z", signature: "fixture-signature" };
  const github = JSON.stringify(updaterManifest(fields));
  const mirror = updaterManifest({ ...fields, base: mirrorAssetBase(version) });
  const files = { [installer]: "fixture installer", [archive]: "fixture updater",
    [`${archive}.sig`]: fields.signature, "latest.json": github };
  const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
  for (const [name, bytes] of Object.entries(files)) writeFileSync(join(bundle, name), bytes);
  writeFileSync(join(bundle, "SHA256SUMS"), Object.entries(files).map(([name, bytes]) => (
    `${digest(bytes)}  ${name}\n`
  )).join(""));
  const evidence = { ...config, channel: "stable", platform: "macos-arm64", commit: "a".repeat(40), installer,
    sha256: digest(files[installer]), notarized: true,
    runtime: { runtimeEnvironment: "managed", targetTriple: "aarch64-apple-darwin",
      pythonProvenance: { verifiedArchive: true } },
    smoke: { standalone: true, freshProfile: true, login: true, ui: true, inferenceSent: false } };
  writeFileSync(join(bundle, "desktop-release.json"), JSON.stringify(evidence));
  mkdirSync(join(bundle, "oss"));
  writeFileSync(join(bundle, "oss", "latest.json"), JSON.stringify(mirror));
  return { archive, bundle, config, evidence, installer, mirror };
}

test("reads the stable flat artifact layout and rejects altered bytes or mismatched mirrors", (t) => {
  const { archive, bundle, config, installer, mirror } = stableFixture(t);
  const candidate = stableMirrorCandidate(bundle, config);
  assert.deepEqual(candidate.uploads.map(({ key }) => key), [
    `${PREFIX}/v${config.version}/${archive}`, `${PREFIX}/v${config.version}/${installer}`,
  ]);
  const manifestPath = join(bundle, "oss", "latest.json");
  writeFileSync(manifestPath, JSON.stringify({ ...mirror, version: "0.3.1" }));
  assert.throws(() => stableMirrorCandidate(bundle, config), /更新清单/);
  writeFileSync(manifestPath, JSON.stringify(mirror));
  writeFileSync(join(bundle, archive), "different bytes");
  assert.throws(() => stableMirrorCandidate(bundle, config), /SHA-256/);
});

test("rejects a stable package without managed runtime and notarization evidence", (t) => {
  const { bundle, config, evidence } = stableFixture(t);
  const path = join(bundle, "desktop-release.json");
  writeFileSync(path, JSON.stringify({ ...evidence, notarized: false }));
  assert.throws(() => stableMirrorCandidate(bundle, config), /公证/);
  writeFileSync(path, JSON.stringify({ ...evidence, runtime: { runtimeEnvironment: "external" } }));
  assert.throws(() => stableMirrorCandidate(bundle, config), /运行时来源/);
});

test("requires a current public GitHub release, exact tag, matching assets, and public manifest", async (t) => {
  const { bundle, config } = stableFixture(t);
  const candidate = stableMirrorCandidate(bundle, config);
  const tag = `v${candidate.version}`;
  const api = `https://api.github.com/repos/${REPO}`;
  const release = { tag_name: tag, draft: false, prerelease: false,
    assets: Object.entries(candidate.assets).map(([name, asset]) => ({
      name, state: "uploaded", digest: `sha256:${asset.sha256}`, size: asset.bytes.length,
    })) };
  const tagId = "b".repeat(40);
  const replies = new Map([
    [`${api}/releases/latest`, release],
    [`${api}/git/ref/tags/${tag}`, { object: { type: "tag", sha: tagId } }],
    [`${api}/git/tags/${tagId}`, { object: { type: "commit", sha: candidate.commit } }],
    [GITHUB_LATEST, candidate.assets["latest.json"].bytes],
  ]);
  const read = async (url) => {
    assert(replies.has(url));
    const reply = replies.get(url);
    return Buffer.isBuffer(reply) ? reply : Buffer.from(JSON.stringify(reply));
  };
  await requirePublicStable(candidate, read);
  replies.set(`${api}/git/ref/tags/${tag}`, { object: { type: "commit", sha: "c".repeat(40) } });
  await assert.rejects(() => requirePublicStable(candidate, read), /标签与本地构建/);
  replies.set(`${api}/git/ref/tags/${tag}`, { object: { type: "commit", sha: candidate.commit } });
  const digest = release.assets[0].digest;
  release.assets[0].digest = "sha256:bad";
  await assert.rejects(() => requirePublicStable(candidate, read), /发布附件/);
  release.assets[0].digest = digest;
  replies.set(GITHUB_LATEST, Buffer.from("different latest.json"));
  await assert.rejects(() => requirePublicStable(candidate, read), /更新入口尚未提供/);
  release.prerelease = true;
  await assert.rejects(() => requirePublicStable(candidate, read), /最新正式版/);
});
