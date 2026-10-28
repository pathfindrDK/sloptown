import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { setTransport } from "./api/client";
import { loadConfig } from "./lib/config";
import { applyTheme } from "./lib/shell";
import "./styles/tokens.css";
import "./styles/app.css";

async function boot() {
  if (import.meta.env.VITE_MOCK === "1") {
    const { mockTransport } = await import("./api/mock");
    setTransport(mockTransport);
  } else {
    await loadConfig();
  }
  // Standalone follows the OS; embedded gets its theme from drive:init.
  applyTheme({ name: window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "miira", tokens: {} });
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void boot();
