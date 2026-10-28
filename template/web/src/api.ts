// Calls to this app's own backend (api/main.py), served under /api.
// Add one function per route, e.g. `noter: () => request<Note[]>("/noter")`.

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
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

export const api = {};
