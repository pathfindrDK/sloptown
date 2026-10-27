import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const controlPlane = process.env.SLOPTOWN_CP_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": { target: controlPlane, changeOrigin: false },
      "/auth": { target: controlPlane, changeOrigin: false },
      "/edge": { target: controlPlane, changeOrigin: false },
    },
  },
  build: {
    // CodeMirror is a lazy chunk of ~550 kB; the entry stays small.
    chunkSizeWarningLimit: 700,
  },
  test: {
    environment: "jsdom",
  },
});
