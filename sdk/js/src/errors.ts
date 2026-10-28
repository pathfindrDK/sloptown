// Preview bridge: in the studio's editor preview, forward runtime errors to the
// studio (window.parent) as `{ type: "vibe:error", message, stack }`, so it can offer
// "Ret fejlen", and say `{ type: "vibe:ready" }` once the app has rendered, so the
// studio can drop its loading overlay. Only active when framed on a preview host
// (`preview-<slug>.<domain>`).

export type VibeErrorMessage = { type: "vibe:error"; message: string; stack?: string };
export type VibeReadyMessage = { type: "vibe:ready" };

export function isPreview(win: Window & typeof globalThis = window): boolean {
  return win.parent !== win && win.location.hostname.startsWith("preview-");
}

function stringify(value: unknown): string {
  if (value instanceof Error) return value.message;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// The studio is the framing page; its origin is in the referrer. Fall back to "*" (preview only).
function studioOrigin(win: Window & typeof globalThis): string {
  try {
    if (win.document.referrer) return new URL(win.document.referrer).origin;
  } catch {
    /* keep "*" */
  }
  return "*";
}

/** Tells the studio the app has rendered. No-op outside the preview. */
export function announceReady(win: Window & typeof globalThis = window): void {
  if (!isPreview(win)) return;
  const msg: VibeReadyMessage = { type: "vibe:ready" };
  win.parent.postMessage(msg, studioOrigin(win));
}

/** Installs listeners; returns an uninstall function. No-op outside the preview. */
export function installErrorBridge(win: Window & typeof globalThis = window): () => void {
  if (!isPreview(win)) return () => {};
  const target = studioOrigin(win);
  const post = (message: string, stack?: string) => {
    const msg: VibeErrorMessage = { type: "vibe:error", message: message.slice(0, 4000), stack: stack?.slice(0, 8000) };
    win.parent.postMessage(msg, target);
  };

  const onError = (e: ErrorEvent) => post(e.message || stringify(e.error), e.error instanceof Error ? e.error.stack : undefined);
  const onRejection = (e: PromiseRejectionEvent) =>
    post(`Unhandled rejection: ${stringify(e.reason)}`, e.reason instanceof Error ? e.reason.stack : undefined);
  const originalConsoleError = win.console.error;
  win.console.error = (...args: unknown[]) => {
    originalConsoleError.apply(win.console, args);
    const err = args.find((a): a is Error => a instanceof Error);
    post(args.map(stringify).join(" "), err?.stack);
  };

  win.addEventListener("error", onError);
  win.addEventListener("unhandledrejection", onRejection);
  return () => {
    win.removeEventListener("error", onError);
    win.removeEventListener("unhandledrejection", onRejection);
    win.console.error = originalConsoleError;
  };
}
