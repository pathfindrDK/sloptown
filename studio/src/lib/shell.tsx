// How the studio is hosted: standalone in a tab, or embedded in Miira's drive.
// Components ask the shell to share/close/rename instead of knowing which.
import { createContext, useContext, type ReactNode } from "react";
import { getConfig } from "./config";
import type { EmbedBridge, IconState, ThemeSpec, ToolbarAction, ToolbarState } from "./embedBridge";

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
  /** chrome=none: Miira's top bar shows our actions; tell it what they may do (null = none). */
  toolbar(fileId: string, state: ToolbarState | null): void;
  /** A press on one of those actions in Miira's top bar; returns an unsubscribe. */
  onAction(listener: (fileId: string, action: ToolbarAction) => void): () => void;
  /** Ask Miira to open its icon dialog; the icon lives on Miira's file. */
  requestIcon(fileId: string): void;
  /** The icon Miira shows, now (if it has said) and on every change; returns an unsubscribe. */
  onIcon(listener: (fileId: string, icon: IconState) => void): () => void;
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
    toolbar() {
      /* standalone: our own TopBar shows the actions */
    },
    onAction: () => () => {},
    requestIcon() {
      /* standalone: the icon is set from the Vibe inside Miira */
    },
    onIcon: () => () => {},
  };
}

export function embeddedShell(bridge: EmbedBridge, chromeHidden: boolean): Shell {
  // Miira sends the icon once the studio runs; Indstillinger may open much later.
  let lastIcon: { fileId: string; icon: IconState } | null = null;
  bridge.on((m) => {
    if (m.type === "drive:icon-state") lastIcon = { fileId: m.fileId, icon: m.icon };
  });
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
    requestIcon: (fileId) => bridge.send({ type: "drive:icon", fileId }),
    onIcon: (listener) => {
      if (lastIcon) listener(lastIcon.fileId, lastIcon.icon);
      const off = bridge.on((m) => {
        if (m.type === "drive:icon-state") listener(m.fileId, m.icon);
      });
      return () => {
        off();
      };
    },
    toolbar: (fileId, state) => {
      if (chromeHidden) bridge.send({ type: "drive:toolbar", fileId, toolbar: state });
    },
    onAction: (listener) => {
      const off = bridge.on((m) => {
        if (m.type === "drive:action") listener(m.fileId, m.action);
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

let schoolTokens: string[] = [];

/** Wear Miira's theme: the name selects a block in vendor/tokens.css, and a school's own
 *  palette lies over it as inline properties, cleared again when the theme changes. */
export function applyTheme(theme: ThemeSpec) {
  const root = document.documentElement;
  for (const k of schoolTokens) root.style.removeProperty(k);
  schoolTokens = Object.keys(theme.tokens);
  for (const k of schoolTokens) root.style.setProperty(k, theme.tokens[k]);
  root.dataset.theme = theme.name;
}
