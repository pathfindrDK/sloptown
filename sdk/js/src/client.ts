// Thin fetch layer over the app backend's platform routes (miira_vibe.setup, mounted at /api/_vibe).

export type Role = "owner" | "editor" | "viewer";

export type User = { id: string; name: string; role: Role; classes: string[] };

export type Member = User & { avatarHue: number };

export type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

export const ROLE_RANK: Record<Role, number> = { viewer: 1, editor: 2, owner: 3 };

export function hasRole(user: User | null | undefined, minimum: Role): boolean {
  return !!user && ROLE_RANK[user.role] >= ROLE_RANK[minimum];
}

export class BudgetExceededError extends Error {
  readonly retryAt: Date | null;
  constructor(message: string, retryAt: string | null) {
    super(message);
    this.name = "BudgetExceededError";
    this.retryAt = retryAt ? new Date(retryAt) : null;
  }
}

export class VibeClient {
  constructor(readonly apiBase: string = "/api") {}

  private url(path: string): string {
    return `${this.apiBase.replace(/\/$/, "")}/_vibe${path}`;
  }

  private async json<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(this.url(path), { credentials: "same-origin", ...init });
    if (res.status === 429) {
      const body = await res.json().catch(() => ({}));
      throw new BudgetExceededError(body.message ?? "AI-budgettet er brugt op for denne uge.", body.retryAt ?? null);
    }
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return res.json() as Promise<T>;
  }

  /** The logged-in user, or null for a visitor of a public app (the platform answers 401). */
  async me(): Promise<User | null> {
    const res = await fetch(this.url("/me"), { credentials: "same-origin" });
    if (res.status === 401) return null;
    if (!res.ok) throw new Error(`/me: HTTP ${res.status}`);
    return res.json() as Promise<User>;
  }

  members(): Promise<{ version: string; members: Member[] }> {
    return this.json("/members");
  }

  /** Subscribe to member changes. Returns an unsubscribe function. */
  onMembersChanged(callback: (version: string) => void): () => void {
    if (typeof EventSource === "undefined") return () => {};
    const es = new EventSource(this.url("/members/events"));
    const handler = (e: MessageEvent) => {
      try {
        callback(JSON.parse(e.data).version ?? "");
      } catch {
        callback("");
      }
    };
    es.addEventListener("members.changed", handler as EventListener);
    return () => es.close();
  }

  async chat(messages: ChatMessage[] | string, opts: { model?: string } = {}): Promise<string> {
    const msgs = typeof messages === "string" ? [{ role: "user" as const, content: messages }] : messages;
    const body = await this.json<{ text: string }>("/ai/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ messages: msgs, model: opts.model }),
    });
    return body.text;
  }

  /** Stream a reply; `onDelta` gets each text piece. Resolves with the full text. */
  async stream(
    messages: ChatMessage[] | string,
    onDelta: (text: string) => void,
    opts: { model?: string; signal?: AbortSignal } = {},
  ): Promise<string> {
    const msgs = typeof messages === "string" ? [{ role: "user" as const, content: messages }] : messages;
    const res = await fetch(this.url("/ai/chat"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ messages: msgs, model: opts.model, stream: true }),
      signal: opts.signal,
    });
    if (res.status === 429) {
      const body = await res.json().catch(() => ({}));
      throw new BudgetExceededError(body.message ?? "AI-budgettet er brugt op for denne uge.", body.retryAt ?? null);
    }
    if (!res.ok || !res.body) throw new Error(`/ai/chat: HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let full = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const event = /^event: (.*)$/m.exec(block)?.[1] ?? "message";
        const data = /^data: (.*)$/m.exec(block)?.[1];
        if (!data) continue;
        const payload = JSON.parse(data);
        if (event === "delta") {
          full += payload.text;
          onDelta(payload.text);
        } else if (event === "error") {
          throw new Error(payload.message);
        }
      }
    }
    return full;
  }
}
