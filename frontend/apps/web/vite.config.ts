import { fileURLToPath } from "node:url";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { defineConfig, loadEnv } from "vite";
import { preloadLocale } from "./vite-locale-preload";

export default defineConfig(({ mode }) => {
  const env = loadEnv(
    mode,
    fileURLToPath(new URL("../..", import.meta.url)),
    "VORTO_",
  );
  const target = env.VORTO_CHAT_SERVER_URL || "http://127.0.0.1:4310";
  const proxy = {
    "/api": { target, changeOrigin: true, ws: true },
    "/ws": { target, changeOrigin: true, ws: true },
  };
  return {
    plugins: [
      tanstackRouter({ target: "react", autoCodeSplitting: true }),
      react(),
      babel({ presets: [reactCompilerPreset()] }),
      preloadLocale(),
    ],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: { host: "127.0.0.1", port: 5174, strictPort: true, proxy },
    preview: { host: "127.0.0.1", port: 5174, strictPort: true, proxy },
    build: {
      outDir: "dist",
      sourcemap: true,
      rollupOptions: {
        input: {
          main: fileURLToPath(new URL("./index.html", import.meta.url)),
          screen: fileURLToPath(new URL("./screen.html", import.meta.url)),
          character: fileURLToPath(new URL("./character-lab.html", import.meta.url)),
        },
      },
    },
  };
});
