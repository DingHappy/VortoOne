import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";

export function runNpm(args, cwd) {
  const options = { cwd, stdio: "inherit" };
  if (process.platform !== "win32") {
    execFileSync("npm", args, options);
    return;
  }

  // npm.cmd cannot be spawned with execFile on Windows. npm run exposes its JS entry.
  const cli = process.env.npm_execpath
    || join(dirname(process.execPath), "node_modules", "npm", "bin", "npm-cli.js");
  execFileSync(process.execPath, [cli, ...args], options);
}
