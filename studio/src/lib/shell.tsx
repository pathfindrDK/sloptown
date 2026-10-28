// How the studio is hosted: standalone in a tab, or embedded in Miira's drive.
// Components ask the shell to share/close/rename instead of knowing which.
import { createContext, useContext, type ReactNode } from "react";
import { getConfig } from "./config";
import type { EmbedBridge } from "./embedBridge";

/** Miira's web origin for deep links (from /api/config, see lib/config.ts). */
export function miiraOrigin(): string {
  return getConfig().miiraOrigin;
}

export type Shell = {
  embedded: boolean;
  chromeHidden: boolean;
  share(fileId: string): void;
  close(): void;
  titleChanged(fileId: string, title: string): void;
  /** Miira renamed the file in its own top bar (chrome=none); returns an unsubscribe. */
  onTitle(listener: (fileId: string, title: string) => void): () => void;
};

export function standaloneShell(navigateBack: () => void): Shell {
  return {
    embedded: false,
    chromeHidden: false,
    share(fileId) {
      window.open(`${miiraOrigin()}/drive/file/${encodeURIComponent(fileId)}?share=1`, "_blank", "noopener");
    },
    close: navigateBack,
    titleChanged() {
      /* standalone: the control plane renames; Miira picks it up on next sync */
    },
    onTitle: () => () => {},
  };
}

export function embeddedShell(bridge: EmbedBridge, chromeHidden: boolean): Shell {
  return {
    embedded: true,
    chromeHidden,
    share: (fileId) => bridge.send({ type: "drive:share", fileId }),
    close: () => bridge.send({ type: "drive:close" }),
    titleChanged: (fileId, title) => bridge.send({ type: "drive:title", fileId, title }),
    onTitle: (listener) => {
      const off = bridge.on((m) => {
        if (m.type === "drive:title") listener(m.fileId, m.title);
      });
      return () => {
        off();
      };
    },
  };
}

const ShellContext = createContext<Shell | null>(null);

export function ShellProvider({ shell, children }: { shell: Shell; children: ReactNode }) {
  return <ShellContext.Provider value={shell}>{children}</ShellContext.Provider>;
}

export function useShell(): Shell {
  const s = useContext(ShellContext);
  if (!s) throw new Error("useShell outside ShellProvider");
  return s;
}

export function applyTheme(theme: "light" | "dark") {
  document.documentElement.dataset.theme = theme;
}
