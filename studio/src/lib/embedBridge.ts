// SlopTown side of Miira's drive:* postMessage protocol (docs/miira-integration.md §3).

export type DriveInit = {
  fileId: string;
  launchToken: string;
  theme: "light" | "dark";
  locale: "da" | "en";
};

export type InboundMessage =
  | ({ type: "drive:init" } & DriveInit)
  | { type: "drive:theme"; theme: "light" | "dark" }
  | { type: "drive:access-changed"; fileId: string };

export type OutboundMessage =
  | { type: "drive:ready" }
  | { type: "drive:share"; fileId: string }
  | { type: "drive:title"; fileId: string; title: string }
  | { type: "drive:close" };

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
        theme: m.theme === "dark" ? "dark" : "light",
        locale: m.locale === "en" ? "en" : "da",
      };
    case "drive:theme":
      return { type: "drive:theme", theme: m.theme === "dark" ? "dark" : "light" };
    case "drive:access-changed":
      return typeof m.fileId === "string" ? { type: "drive:access-changed", fileId: m.fileId } : null;
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
