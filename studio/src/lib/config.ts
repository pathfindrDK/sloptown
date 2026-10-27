// Runtime configuration from the control plane (GET /api/config), loaded once at
// boot so one studio build serves every environment. The VITE_MIIRA_ORIGIN(S)
// build variables are only the fallback (mock mode, tests, dev server).
import { parseOrigins } from "./embedBridge";

export type StudioConfig = {
  /** Origins allowed to embed the studio and speak drive:* (Miira's drive). */
  miiraOrigins: string[];
  /** Where deep links into Miira go (new Vibe, share dialog). */
  miiraOrigin: string;
};

function fromEnv(): StudioConfig {
  const miiraOrigins = parseOrigins(import.meta.env.VITE_MIIRA_ORIGINS as string | undefined);
  const miiraOrigin = ((import.meta.env.VITE_MIIRA_ORIGIN as string | undefined) ?? miiraOrigins[0] ?? "").replace(/\/$/, "");
  return { miiraOrigins, miiraOrigin };
}

let current: StudioConfig = fromEnv();

export function getConfig(): StudioConfig {
  return current;
}

/** Fetch /api/config; keeps the build-time fallback for anything the server leaves out. */
export async function loadConfig(fetcher: typeof fetch = fetch): Promise<StudioConfig> {
  try {
    const r = await fetcher("/api/config", { credentials: "same-origin" });
    if (r.ok) {
      const body = (await r.json()) as Partial<StudioConfig>;
      const origins = (body.miiraOrigins ?? []).map((o) => o.replace(/\/$/, "")).filter(Boolean);
      current = {
        miiraOrigins: origins.length ? origins : current.miiraOrigins,
        miiraOrigin: (body.miiraOrigin || origins[0] || current.miiraOrigin).replace(/\/$/, ""),
      };
    }
  } catch {
    /* offline or old control plane: keep the fallback */
  }
  return current;
}
