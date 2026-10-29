// SlopTown side of Miira's drive:* postMessage protocol (docs/miira-integration.md §3).
import type { PublishState } from "./publish";

/** Miira's theme names; "org" is the school's own palette, carried as tokens. */
export type MiiraTheme = "miira" | "neutral" | "dark" | "kontrast" | "org";

/** A Miira theme: its name, plus the school's --miira-* values when it has its own. */
export type ThemeSpec = { name: MiiraTheme; tokens: Record<string, string> };

export type DriveInit = {
  fileId: string;
  launchToken: string;
  theme: ThemeSpec;
  locale: "da" | "en";
};

export type InboundMessage =
  | ({ type: "drive:init" } & DriveInit)
  | { type: "drive:theme"; theme: ThemeSpec }
  | { type: "drive:access-changed"; fileId: string }
  | { type: "drive:title"; fileId: string; title: string }
  | { type: "drive:action"; fileId: string; action: ToolbarAction }
  | { type: "drive:icon-state"; fileId: string; icon: IconState };

/** The Vibe's icon as Miira shows it, for Indstillinger. A data URL, because Miira's own
 *  files need the Miira session, which the studio (on another site) does not have. */
export type IconState = { src: string; custom: boolean };

const ICON_SRC = /^data:image\/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$/;
const ICON_SRC_MAX = 600_000;

export type ToolbarAction = "publish" | "download" | "unpublish";
const TOOLBAR_ACTIONS: ToolbarAction[] = ["publish", "download", "unpublish"];

/** What Miira's top bar may offer for this project (docs/miira-integration.md §3). */
export type ToolbarState = {
  canEdit: boolean;
  /** null for a "fra bunden" project or a viewer: nothing to publish. `state` picks the
   *  button ("Udgiv" / "Opdater" / "Udgivet"); `published` (= state "published") stays
   *  for Miira builds that predate it. `canUnpublish`: offer "Stop udgivelse". */
  publish: {
    state: PublishState;
    published: boolean;
    busy: boolean;
    disabled: boolean;
    canUnpublish: boolean;
  } | null;
  appUrl: string | null;
  download: boolean;
  budget: {
    state: "ok" | "warning" | "nerfed" | "blocked";
    fraction: number | null;
    nerfMark: number | null;
    usedTokens: number;
    limitTokens: number | null;
  } | null;
};

// The control plane's limit on a project title (projects.py).
const TITLE_MAX = 120;

export type OutboundMessage =
  | { type: "drive:ready" }
  | { type: "drive:share"; fileId: string }
  | { type: "drive:title"; fileId: string; title: string }
  | { type: "drive:close" }
  | { type: "drive:toolbar"; fileId: string; toolbar: ToolbarState | null }
  | { type: "drive:icon"; fileId: string };

export function parseOrigins(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(",")
    .map((s) => s.trim().replace(/\/$/, ""))
    .filter(Boolean);
}

export function isAllowedOrigin(origin: string, allowed: string[]): boolean {
  if (!origin || origin === "null") return false;
  return allowed.includes(origin.replace(/\/$/, ""));
}

const MIIRA_THEMES = new Set(["miira", "neutral", "dark", "kontrast", "org"]);
const TOKEN_NAME = /^--miira-[a-z0-9-]{1,60}$/;
// A school palette is colours and lengths; nothing that can fetch or break out.
const UNSAFE_VALUE = /url\(|[;{}<>]|expression|@import/i;

/** Miira's theme from a drive:init / drive:theme. `theme: "light" | "dark"` alone is the
 *  older contract (before `miiraTheme`), still understood. */
export function parseTheme(m: Record<string, unknown>): ThemeSpec {
  const name = typeof m.miiraTheme === "string" && MIIRA_THEMES.has(m.miiraTheme)
    ? (m.miiraTheme as MiiraTheme)
    : m.theme === "dark" ? "dark" : "miira";
  const tokens: Record<string, string> = {};
  if (m.tokens && typeof m.tokens === "object") {
    for (const [k, v] of Object.entries(m.tokens as Record<string, unknown>).slice(0, 80)) {
      if (TOKEN_NAME.test(k) && typeof v === "string" && v.length <= 120 && !UNSAFE_VALUE.test(v)) tokens[k] = v;
    }
  }
  return { name, tokens };
}

/** Validate shape of an inbound message; returns null for anything we don't understand. */
export function parseInbound(data: unknown): InboundMessage | null {
  if (!data || typeof data !== "object") return null;
  const m = data as Record<string, unknown>;
  switch (m.type) {
    case "drive:init":
      if (typeof m.fileId !== "string" || typeof m.launchToken !== "string") return null;
      return {
        type: "drive:init",
        fileId: m.fileId,
        launchToken: m.launchToken,
        theme: parseTheme(m),
        locale: m.locale === "en" ? "en" : "da",
      };
    case "drive:theme":
      return { type: "drive:theme", theme: parseTheme(m) };
    case "drive:access-changed":
      return typeof m.fileId === "string" ? { type: "drive:access-changed", fileId: m.fileId } : null;
    case "drive:title": {
      if (typeof m.fileId !== "string" || typeof m.title !== "string") return null;
      const title = m.title.trim().slice(0, TITLE_MAX).trim();
      return title ? { type: "drive:title", fileId: m.fileId, title } : null;
    }
    case "drive:icon-state":
      if (typeof m.fileId !== "string" || typeof m.src !== "string") return null;
      if (m.src.length > ICON_SRC_MAX || !ICON_SRC.test(m.src)) return null;
      return { type: "drive:icon-state", fileId: m.fileId, icon: { src: m.src, custom: m.custom === true } };
    case "drive:action":
      if (typeof m.fileId !== "string" || !TOOLBAR_ACTIONS.includes(m.action as ToolbarAction)) return null;
      return { type: "drive:action", fileId: m.fileId, action: m.action as ToolbarAction };
    default:
      return null;
  }
}

export class EmbedBridge {
  private parentOrigin: string | null = null;
  private listeners = new Set<(m: InboundMessage) => void>();
  private onWindowMessage = (ev: MessageEvent) => {
    if (ev.source !== this.target) return;
    if (!isAllowedOrigin(ev.origin, this.allowed)) return;
    const msg = parseInbound(ev.data);
    if (!msg) return;
    // Lock onto the first allowed origin that initialises us.
    if (msg.type === "drive:init") this.parentOrigin = ev.origin;
    else if (ev.origin !== this.parentOrigin) return;
    this.listeners.forEach((l) => l(msg));
  };

  constructor(
    private allowed: string[],
    private win: Window = window,
    private target: Window | null = window.parent !== window ? window.parent : null,
  ) {}

  get embedded() {
    return this.target !== null;
  }

  start() {
    this.win.addEventListener("message", this.onWindowMessage);
    if (!this.target) return;
    // Before init we don't know which allowed origin hosts us: announce to each.
    for (const origin of this.allowed) {
      try {
        this.target.postMessage({ type: "drive:ready" }, origin);
      } catch {
        /* origin mismatch is silently dropped by the browser */
      }
    }
  }

  stop() {
    this.win.removeEventListener("message", this.onWindowMessage);
  }

  on(listener: (m: InboundMessage) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  send(msg: OutboundMessage) {
    if (!this.target || !this.parentOrigin) return false;
    this.target.postMessage(msg, this.parentOrigin);
    return true;
  }
}
