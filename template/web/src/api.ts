// Calls to this app's own backend (api/main.py), served under /api.

export type Hilsen = { id: number; tekst: string; forfatter: string; oprettet: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? body.message ?? `Fejl ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  hilsner: () => request<Hilsen[]>("/hilsner"),
  nyHilsen: (tekst: string) => request<Hilsen>("/hilsner", { method: "POST", body: JSON.stringify({ tekst }) }),
};
