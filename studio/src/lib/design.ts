// The design catalog (GET /api/design): fetched once, shared by the start screen
// and Indstillinger.
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DesignCatalog } from "../api/types";

let cached: Promise<DesignCatalog> | null = null;

export function loadDesign(): Promise<DesignCatalog> {
  cached ??= api.design().catch((e) => {
    cached = null; // try again next time
    throw e;
  });
  return cached;
}

/** The catalog, or null while loading (and if it can't be loaded). */
export function useDesign(): DesignCatalog | null {
  const [catalog, setCatalog] = useState<DesignCatalog | null>(null);
  useEffect(() => {
    let live = true;
    loadDesign().then((c) => live && setCatalog(c), () => {});
    return () => {
      live = false;
    };
  }, []);
  return catalog;
}
