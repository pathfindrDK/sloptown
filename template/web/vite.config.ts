// Platform-owned (locked): the agent cannot change this file.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Preview runs at preview-<slug>.<VIBE_APPS_DOMAIN> behind SlopTown's edge.
const appsDomain = process.env.VIBE_APPS_DOMAIN;
const hmrPort = process.env.VIBE_HMR_CLIENT_PORT;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    allowedHosts: appsDomain ? [`.${appsDomain}`, "localhost"] : true,
    hmr: hmrPort ? { clientPort: Number(hmrPort) } : undefined,
    proxy: {
      // The backend (FastAPI on :8000) sees paths without /api, exactly like in vibe-runtime.
      "/api": { target: "http://127.0.0.1:8000", rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
