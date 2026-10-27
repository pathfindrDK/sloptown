import "@miira/vibe/theme.css";
import "./styles.css";

import { VibeProvider } from "@miira/vibe";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <VibeProvider>
      <App />
    </VibeProvider>
  </StrictMode>,
);
