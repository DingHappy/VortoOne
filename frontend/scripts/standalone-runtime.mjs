import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { machOFiles } from "../../desktop/scripts/runtime-tree.mjs";

/** Tauri copies resources without signing nested Python libraries; keep their exception off the WebView host. */
export function signRuntime(app, identity, repo) {
  const runtime = join(app, "Contents/Resources/runtime");
  const executable = join(runtime, "vortocode-runtime");
  if (!existsSync(executable)) throw new Error("Standalone runtime missing from application");
  const sign = (path, extra = []) => execFileSync("codesign", ["--force", "--options", "runtime", "--timestamp",
    ...extra, "--sign", identity, path], {stdio: "pipe"});
  for (const binary of machOFiles(runtime).filter((path) => path !== executable)) sign(binary);
  sign(executable, ["--entitlements", join(repo, "desktop/src-tauri/SidecarEntitlements.plist")]);
  sign(app);
  execFileSync("codesign", ["--verify", "--deep", "--strict", app], {stdio: "pipe"});
}
