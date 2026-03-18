import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "fs";
import { resolve } from "path";

function getAppVersion(): string {
  try {
    const tauriConf = JSON.parse(
      readFileSync(resolve(__dirname, "../src-tauri/tauri.conf.json"), "utf-8"),
    );
    return tauriConf.version ?? "0.1.0";
  } catch {
    return "0.1.0";
  }
}

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(getAppVersion()),
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8420",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8420",
        ws: true,
      },
    },
  },
});
