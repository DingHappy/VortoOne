import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  readSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const tauriRoot = join(desktopRoot, "src-tauri");
const sourcesPath = join(desktopRoot, "managed-python-sources.json");

function run(command, args, options = {}) {
  const { env, ...spawnOptions } = options;
  const result = spawnSync(command, args, {
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ...env },
    ...spawnOptions,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed\n${result.stderr || result.stdout}`);
  }
  return (result.stdout || result.stderr || "").trim();
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function runtimeTreeSha256(root) {
  const hash = createHash("sha256");
  function visit(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true }).sort((left, right) => left.name.localeCompare(right.name))) {
      const path = join(directory, entry.name);
      const relativePath = path.slice(root.length + 1);
      if (relativePath === ".vortocode-managed-python.json") continue;
      const metadata = lstatSync(path);
      hash.update(relativePath);
      hash.update(String(metadata.mode & 0o777));
      if (entry.isDirectory()) {
        hash.update("directory");
        visit(path);
      } else if (entry.isSymbolicLink()) {
        hash.update("symlink");
        hash.update(readlinkSync(path));
      } else {
        hash.update("file");
        hash.update(readFileSync(path));
      }
    }
  }
  visit(root);
  return hash.digest("hex");
}

function architecture(machine) {
  switch (machine.toLowerCase()) {
    case "amd64":
    case "x86-64":
      return "x86_64";
    case "arm64":
      return "aarch64";
    default:
      return machine.toLowerCase();
  }
}

function windowsX64Executable(path) {
  const file = openSync(path, "r");
  try {
    const dos = Buffer.alloc(64);
    if (readSync(file, dos, 0, dos.length, 0) !== dos.length || dos.readUInt16LE(0) !== 0x5a4d) {
      return false;
    }
    // The DOS header points to the PE signature, COFF machine, and optional header.
    const header = Buffer.alloc(26);
    return readSync(file, header, 0, header.length, dos.readUInt32LE(0x3c)) === header.length
      && header.readUInt32LE(0) === 0x4550
      && header.readUInt16LE(4) === 0x8664
      && header.readUInt16LE(24) === 0x20b;
  } finally {
    closeSync(file);
  }
}

function bundledFile(root, name, directory) {
  const parts = typeof name === "string" ? name.split("/") : [];
  if (parts[0] !== directory || parts.length < 2 || parts.some(part => !part || /^(?:\.|\.\.)$|[:\\]/.test(part))) {
    throw new Error("Managed Python metadata contains an invalid archive-relative path");
  }
  const subtree = join(root, directory);
  if (!lstatSync(subtree).isDirectory()) {
    throw new Error("Managed Python archive directory is missing");
  }
  const path = join(root, ...parts);
  const within = relative(realpathSync(subtree), realpathSync(path));
  if (!within || within === ".." || within.startsWith(".." + sep) || isAbsolute(within) || !statSync(path).isFile()) {
    throw new Error("Managed Python archive file escapes its expected directory");
  }
  return path;
}

function loadSources() {
  const raw = readFileSync(sourcesPath);
  const config = JSON.parse(raw.toString("utf8"));
  if (config.formatVersion !== 1 || config.provider !== "astral-sh/python-build-standalone"
    || !/^\d{8}$/.test(config.release) || !/^\d+\.\d+\.\d+$/.test(config.pythonVersion)) {
    throw new Error("managed-python-sources.json has an unsupported or incomplete format");
  }
  return {
    config,
    configSha256: createHash("sha256").update(raw).digest("hex"),
  };
}

function verifyExtractedRuntime(runtimeRoot, source, config, targetTriple) {
  const metadataPath = join(runtimeRoot, "PYTHON.json");
  if (!existsSync(metadataPath)) throw new Error(`Managed Python metadata is missing: ${metadataPath}`);
  const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
  if (metadata.target_triple !== targetTriple
    || metadata.python_version !== config.pythonVersion
    || metadata.build_options !== source.buildOptions
    || !Array.isArray(metadata.licenses)
    || !metadata.licenses.length) {
    throw new Error(`Managed Python metadata does not match the pinned source for ${targetTriple}`);
  }
  const python = bundledFile(runtimeRoot, metadata.python_exe, "install");
  bundledFile(runtimeRoot, metadata.license_path, "licenses");
  const licensesRoot = join(runtimeRoot, "licenses");
  const windows = targetTriple.endsWith("-pc-windows-msvc");
  const fileOutput = windows ? "" : run("file", ["-bL", python]);
  const expectedFormat = targetTriple.endsWith("-apple-darwin") ? /^Mach-O\b/ : /^ELF\b/;
  const detectedArch = fileOutput.match(/\b(?:arm64|aarch64|x86[_-]64|amd64)\b/i)?.[0] || "";
  const executableMatches = windows
    ? windowsX64Executable(python)
    : expectedFormat.test(fileOutput) && architecture(detectedArch) === targetTriple.split("-")[0];
  if (!executableMatches) {
    throw new Error(`Managed Python executable format or architecture does not match ${targetTriple}`);
  }
  return { licensesRoot, metadata, metadataPath, python };
}

export function prepareManagedPython(targetTriple) {
  const { config, configSha256 } = loadSources();
  const source = config.sources[targetTriple];
  if (!source) {
    throw new Error(`No pinned managed Python source is available for ${targetTriple}`);
  }
  const archiveName = `cpython-${config.pythonVersion}+${config.release}-${targetTriple}-${source.buildOptions}-full.tar.zst`;
  const archiveUrl = `https://github.com/${config.provider}/releases/download/${config.release}/${encodeURIComponent(archiveName)}`;
  if (!["pgo", "pgo+lto"].includes(source.buildOptions) || source.archive !== archiveName || source.url !== archiveUrl
    || !Number.isSafeInteger(source.size) || source.size <= 0 || !/^[a-f0-9]{64}$/.test(source.sha256)) {
    throw new Error(`Managed Python source for ${targetTriple} has an invalid archive pin`);
  }

  const downloadsRoot = join(tauriRoot, "target", "managed-python", "downloads");
  const archivePath = join(downloadsRoot, source.archive);
  mkdirSync(downloadsRoot, { recursive: true });
  if (existsSync(archivePath)
    && (statSync(archivePath).size !== source.size || sha256(archivePath) !== source.sha256)) {
    rmSync(archivePath, { force: true });
  }
  if (!existsSync(archivePath)) {
    const temporaryArchive = `${archivePath}.${process.pid}.tmp`;
    rmSync(temporaryArchive, { force: true });
    console.log(`Downloading pinned CPython ${config.pythonVersion} for ${targetTriple}...`);
    run("curl", ["-L", "--fail", "--retry", "3", "--output", temporaryArchive, source.url], { stdio: "inherit" });
    if (statSync(temporaryArchive).size !== source.size || sha256(temporaryArchive) !== source.sha256) {
      rmSync(temporaryArchive, { force: true });
      throw new Error(`Managed Python archive failed size or SHA-256 verification for ${targetTriple}`);
    }
    renameSync(temporaryArchive, archivePath);
  }

  const runtimeRoot = join(tauriRoot, "target", "managed-python", targetTriple, config.release);
  const manifestPath = join(runtimeRoot, ".vortocode-managed-python.json");
  const expectedManifestBase = {
    archive: source.archive,
    archiveSha256: source.sha256,
    buildOptions: source.buildOptions,
    configSha256,
    provider: config.provider,
    pythonVersion: config.pythonVersion,
    release: config.release,
    targetTriple,
    verifiedArchive: true,
  };
  if (existsSync(manifestPath)) {
    try {
      const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
      const { runtimeTreeSha256: recordedTreeSha256, ...manifestBase } = manifest;
      if (JSON.stringify(manifestBase) === JSON.stringify(expectedManifestBase)
        && recordedTreeSha256 === runtimeTreeSha256(runtimeRoot)) {
        const verified = verifyExtractedRuntime(runtimeRoot, source, config, targetTriple);
        return { ...verified, archivePath, manifest, runtimeRoot };
      }
    } catch {
      // Recreate generated state when its metadata or files are incomplete.
    }
  }

  const temporaryRoot = `${runtimeRoot}.tmp-${process.pid}`;
  rmSync(temporaryRoot, { force: true, recursive: true });
  mkdirSync(temporaryRoot, { recursive: true });
  console.log(`Extracting verified CPython archive for ${targetTriple}...`);
  run("tar", [
    "-xf",
    archivePath,
    "-C",
    temporaryRoot,
    "python/PYTHON.json",
    "python/install",
    "python/licenses",
  ], { stdio: "inherit" });
  const extractedRoot = join(temporaryRoot, "python");
  verifyExtractedRuntime(extractedRoot, source, config, targetTriple);
  const manifest = {
    ...expectedManifestBase,
    runtimeTreeSha256: runtimeTreeSha256(extractedRoot),
  };
  writeFileSync(join(extractedRoot, ".vortocode-managed-python.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  mkdirSync(dirname(runtimeRoot), { recursive: true });
  rmSync(runtimeRoot, { force: true, recursive: true });
  renameSync(extractedRoot, runtimeRoot);
  rmSync(temporaryRoot, { force: true, recursive: true });
  const verified = verifyExtractedRuntime(runtimeRoot, source, config, targetTriple);
  return { ...verified, archivePath, manifest, runtimeRoot };
}

export function ensureManagedPython(targetTriple) {
  const managed = prepareManagedPython(targetTriple);
  const runtime = JSON.parse(run(managed.python, [
    "-I",
    "-c",
    "import json, platform, sys; print(json.dumps({'machine': platform.machine(), 'platform': sys.platform, 'version': platform.python_version()}))",
  ]));
  if (runtime.version !== managed.manifest.pythonVersion
    || runtime.platform !== process.platform
    || architecture(runtime.machine) !== targetTriple.split("-")[0]) {
    throw new Error(`Managed Python cannot execute natively for ${targetTriple}`);
  }
  return managed;
}
