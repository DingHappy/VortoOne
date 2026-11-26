import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  BUCKET, PREFIX, releaseUploads, requirePublicStable, signPortableInstallers,
  stableMirrorCandidate, stableReleaseCandidate, stageRelease, stringToSign,
} from "./oss-release.mjs";
import {
  GITHUB_LATEST, MIRROR_BASE, PLATFORM, REPO, dmgAssetName, githubAssetBase, mirrorAssetBase, updaterAssetName, updaterManifest,
} from "./updater-release.mjs";

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

function fixtureSigningKey() {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const rawKey = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  const keyId = Buffer.from("test-key");
  const publicKeyLine = Buffer.concat([Buffer.from("Ed"), keyId, rawKey]).toString("base64");
  const pubkey = Buffer.from(`untrusted comment: test public key\n${publicKeyLine}\n`).toString("base64");
  const signBytes = (bytes, version) => {
    const comment = `timestamp:1\tversion:${version}`;
    const prehash = createHash("blake2b512").update(bytes).digest();
    const signature = sign(null, prehash, privateKey);
    const signatureLine = Buffer.concat([Buffer.from("ED"), keyId, signature]).toString("base64");
    const globalSignature = sign(null, Buffer.concat([signature, Buffer.from(comment)]), privateKey);
    const signatureText = `untrusted comment: test signature\n${signatureLine}\ntrusted comment: ${comment}\n${globalSignature.toString("base64")}\n`;
    return Buffer.from(signatureText).toString("base64");
  };
  return { pubkey, signBytes };
}

function stableFixture(t, bundle = mkdtempSync(join(tmpdir(), "vorto-oss-release-"))) {
  mkdirSync(bundle, { recursive: true });
  t.after(() => rmSync(bundle, { recursive: true, force: true }));
  const { pubkey, signBytes } = fixtureSigningKey();
  const config = { version: "0.3.0", identifier: "com.vorto.vortoone", plugins: { updater: { pubkey } } };
  const { version } = config;
  const archive = updaterAssetName(version);
  const installer = dmgAssetName(version);
  const archiveBytes = "fixture updater";
  const fields = { version, notes: "Fresh install", pubDate: "2026-11-23T10:00:00Z", signature: signBytes(archiveBytes, version) };
  const github = JSON.stringify(updaterManifest(fields));
  const mirror = updaterManifest({ ...fields, base: mirrorAssetBase(version) });
  const files = { [installer]: "fixture installer", [archive]: archiveBytes,
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
  return { archive, bundle, config, evidence, installer, mirror, signBytes };
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

function threePlatformFixture(t) {
  const root = mkdtempSync(join(tmpdir(), "vorto-oss-stable-"));
  const mac = stableFixture(t, join(root, "macos-arm64"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
  const write = (platform, extension, triple, overrides = {}) => {
    const dir = join(root, platform);
    mkdirSync(dir, { recursive: true });
    const installer = `VortoOne_${mac.config.version}_${platform}${extension}`;
    const bytes = `fixture ${platform}`;
    writeFileSync(join(dir, installer), bytes);
    writeFileSync(join(dir, "SHA256SUMS"), `${digest(bytes)}  ${installer}\n`);
    writeFileSync(join(dir, "desktop-release.json"), JSON.stringify({
      channel: "stable", identifier: mac.config.identifier, version: mac.config.version, commit: mac.evidence.commit,
      platform, installer, sha256: digest(bytes),
      runtime: { runtimeEnvironment: "managed", targetTriple: triple, pythonProvenance: { verifiedArchive: true } },
      ...overrides,
    }));
    return installer;
  };
  const windows = write("windows-x64", ".exe", "x86_64-pc-windows-msvc");
  const ubuntu = write("ubuntu-amd64", ".deb", "x86_64-unknown-linux-gnu");
  const signFixture = (path, version) => {
    writeFileSync(`${path}.sig`, mac.signBytes(readFileSync(path), version));
  };
  return { ...mac, root, write, windows, ubuntu, signFixture };
}

test("a stable release carries all three platforms and one checksum file, uploaded before the manifest", (t) => {
  const { archive, config, installer, mirror, root, windows, ubuntu, signFixture } = threePlatformFixture(t);
  signPortableInstallers(root, config, signFixture);
  const candidate = stableReleaseCandidate(root, config);
  const folder = `${PREFIX}/v${config.version}`;
  assert.deepEqual(candidate.uploads.map(({ key }) => key), [
    `${folder}/${archive}`, `${folder}/${installer}`, `${folder}/${windows}`, `${folder}/${windows}.sig`,
    `${folder}/${ubuntu}`, `${folder}/${ubuntu}.sig`, `${folder}/SHA256SUMS`,
  ]);
  const github = JSON.parse(candidate.assets["latest.json"].bytes);
  const oss = JSON.parse(candidate.manifest);
  assert.deepEqual(github.mirrors, mirror.mirrors);
  assert.deepEqual(oss.mirrors, mirror.mirrors);
  assert.deepEqual(oss.platforms[PLATFORM], mirror.platforms[PLATFORM]);
  assert.deepEqual(github.platforms[PLATFORM], {
    signature: mirror.platforms[PLATFORM].signature, url: `${githubAssetBase(config.version)}/${archive}`,
  });
  for (const [target, name] of [["windows-x86_64-nsis", windows], ["linux-x86_64-deb", ubuntu]]) {
    const signature = candidate.assets[`${name}.sig`].bytes.toString("utf8");
    assert.deepEqual(github.platforms[target], { signature, url: `${githubAssetBase(config.version)}/${name}` });
    assert.deepEqual(oss.platforms[target], { signature, url: `${mirrorAssetBase(config.version)}/${name}` });
  }
  const sums = candidate.assets.SHA256SUMS.bytes.toString("utf8").trimEnd().split("\n");
  assert.deepEqual(sums.map((line) => line.split("  ")[1]).sort(),
    [installer, archive, `${archive}.sig`, "latest.json", windows, `${windows}.sig`, ubuntu, `${ubuntu}.sig`].sort());
  for (const line of sums) {
    const [hash, name] = line.split("  ");
    assert.equal(hash, candidate.assets[name].sha256);
  }

  const stage = join(root, "release");
  const staged = stageRelease(candidate, stage);
  assert.equal(staged.length, 9);
  assert.deepEqual(readdirSync(stage).sort(), Object.keys(candidate.assets).sort());
  assert.equal(readFileSync(join(stage, windows), "utf8"), "fixture windows-x64");
  assert.throws(() => stageRelease(candidate, stage), /不是空目录/);
});

test("portable signing verifies both signed versions before saving and keeps valid sidecars on retry", (t) => {
  const { config, root, windows, ubuntu, signFixture } = threePlatformFixture(t);
  const windowsSignature = join(root, "windows-x64", `${windows}.sig`);
  const ubuntuSignature = join(root, "ubuntu-amd64", `${ubuntu}.sig`);
  assert.throws(() => stableReleaseCandidate(root, config), /windows-x64.*--sign-portable/);
  assert.throws(() => signPortableInstallers(root, config, (path, version) => {
    signFixture(path, path.endsWith(".exe") ? version : "0.2.9");
  }), /ubuntu-amd64.*未绑定到版本/);
  assert.equal(existsSync(windowsSignature), false);
  assert.equal(existsSync(ubuntuSignature), false);
  const paths = signPortableInstallers(root, config, signFixture);
  assert.deepEqual(paths, [windowsSignature, ubuntuSignature]);
  const beforeRetry = paths.map((path) => readFileSync(path));
  assert.deepEqual(signPortableInstallers(root, config, () => assert.fail("valid signature replaced")), paths);
  assert.deepEqual(paths.map((path) => readFileSync(path)), beforeRetry);

  writeFileSync(windowsSignature, readFileSync(ubuntuSignature));
  assert.throws(() => stableReleaseCandidate(root, config), /windows-x64.*签名与安装包或受信注释不匹配/);
  assert.throws(() => signPortableInstallers(root, config, () => assert.fail("invalid sidecar silently replaced")),
    /windows-x64.*签名与安装包或受信注释不匹配/);
});

test("gates reject a label edited after signing and a signature for the wrong public key", (t) => {
  const { config, root, signFixture, windows } = threePlatformFixture(t);
  const installer = join(root, "windows-x64", windows);
  signPortableInstallers(root, config, signFixture);
  const wrongKey = fixtureSigningKey();
  const configWithWrongKey = { ...config, plugins: { updater: { pubkey: wrongKey.pubkey } } };
  assert.throws(() => stableReleaseCandidate(root, configWithWrongKey), /macOS.*签名与安装包或受信注释不匹配/);
  assert.throws(() => signPortableInstallers(root, configWithWrongKey, () => assert.fail("signed with invalid macOS evidence")),
    /macOS.*签名与安装包或受信注释不匹配/);
  writeFileSync(`${installer}.sig`, wrongKey.signBytes(readFileSync(installer), config.version));
  assert.throws(() => stableReleaseCandidate(root, config), /windows-x64.*签名与安装包或受信注释不匹配/);
  signFixture(installer, "0.2.9");
  writeFileSync(`${installer}.sig`, Buffer.from(
    Buffer.from(readFileSync(`${installer}.sig`, "utf8"), "base64").toString("utf8")
      .replace("version:0.2.9", `version:${config.version}`),
  ).toString("base64"));
  assert.throws(() => stableReleaseCandidate(root, config), /windows-x64.*签名与安装包或受信注释不匹配/);
});

test("Windows and Ubuntu installers must be the same version and commit as macOS, with intact bytes", (t) => {
  const { config, root, write, windows } = threePlatformFixture(t);
  write("windows-x64", ".exe", "x86_64-pc-windows-msvc", { commit: "d".repeat(40) });
  assert.throws(() => stableReleaseCandidate(root, config), /同一个提交/);
  write("windows-x64", ".exe", "x86_64-pc-windows-msvc", { runtime: { runtimeEnvironment: "external" } });
  assert.throws(() => stableReleaseCandidate(root, config), /固定运行时/);
  write("windows-x64", ".exe", "x86_64-pc-windows-msvc", { version: "0.2.9" });
  assert.throws(() => stableReleaseCandidate(root, config), /构建证据/);
  write("windows-x64", ".exe", "x86_64-pc-windows-msvc");
  writeFileSync(join(root, "windows-x64", windows), "tampered");
  assert.throws(() => stableReleaseCandidate(root, config), /SHA-256/);
  rmSync(join(root, "ubuntu-amd64"), { recursive: true });
  write("windows-x64", ".exe", "x86_64-pc-windows-msvc");
  assert.throws(() => stableReleaseCandidate(root, config), /ubuntu-amd64 的正式版构建证据/);
});
