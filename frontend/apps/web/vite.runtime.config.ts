import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(
    mode,
    fileURLToPath(new URL("../..", import.meta.url)),
    "VORTO_",
  );
  const target = env.VORTO_RUNTIME_URL || "http://127.0.0.1:8080";
  const proxy = { "/api": { target }, "/ws": { target, ws: true } };
  return {
    plugins: [react()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: { host: "127.0.0.1", port: 5177, strictPort: true, proxy },
    preview: { host: "127.0.0.1", port: 5177, strictPort: true, proxy },
    build: { outDir: "dist", sourcemap: true },
  };
});
