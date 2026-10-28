// Embedded in Miira's drive: wait for drive:init, exchange the launch token
// for a session (bearer kept in memory), then render the project.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setBearer } from "../api/client";
import { getConfig } from "../lib/config";
import { EmbedBridge } from "../lib/embedBridge";
import { LocaleProvider, detectLocale, useT, type Locale } from "../lib/i18n";
import { ShellProvider, applyTheme, embeddedShell } from "../lib/shell";
import { Loading } from "../components/Loading";

const MOCK = import.meta.env.VITE_MOCK === "1";

type Phase = { kind: "waiting" } | { kind: "ready" } | { kind: "denied" };

export function EmbedGate({ children }: { children: ReactNode }) {
  const chromeHidden = new URLSearchParams(window.location.search).get("chrome") === "none";
  const bridge = useMemo(() => new EmbedBridge(getConfig().miiraOrigins), []);
  const shell = useMemo(() => embeddedShell(bridge, chromeHidden), [bridge, chromeHidden]);
  const [phase, setPhase] = useState<Phase>(MOCK ? { kind: "ready" } : { kind: "waiting" });
  const [locale, setLocale] = useState<Locale>(detectLocale());

  useEffect(() => {
    const off = bridge.on(async (msg) => {
      if (msg.type === "drive:theme") applyTheme(msg.theme);
      if (msg.type === "drive:init") {
        applyTheme(msg.theme);
        setLocale(msg.locale);
        try {
          const r = await api.launch(msg.launchToken);
          setBearer(r.token);
          setPhase({ kind: "ready" });
        } catch {
          setPhase({ kind: "denied" });
        }
      }
    });
    bridge.start();
    return () => {
      off();
      bridge.stop();
    };
  }, [bridge]);

  return (
    <LocaleProvider locale={locale}>
      <ShellProvider shell={shell}>{phase.kind === "ready" ? children : <Waiting denied={phase.kind === "denied"} />}</ShellProvider>
    </LocaleProvider>
  );
}

function Waiting({ denied }: { denied: boolean }) {
  const t = useT();
  return (
    <div className="page page--center">
      {denied ? (
        <p>{t("embed.denied")}</p>
      ) : (
        <Loading label={t("embed.waiting")} />
      )}
    </div>
  );
}
