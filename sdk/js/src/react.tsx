import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  BudgetExceededError,
  VibeClient,
  hasRole,
  type ChatMessage,
  type Member,
  type Role,
  type User,
} from "./client.js";
import { installErrorBridge } from "./errors.js";

type VibeContextValue = {
  client: VibeClient;
  user: User | null;
  loading: boolean;
  error: Error | null;
};

const VibeContext = createContext<VibeContextValue | null>(null);

function useVibe(): VibeContextValue {
  const ctx = useContext(VibeContext);
  if (!ctx) throw new Error("@miira/vibe: wrap your app in <VibeProvider>");
  return ctx;
}

export function VibeProvider({ children, apiBase = "/api" }: { children: ReactNode; apiBase?: string }) {
  const client = useMemo(() => new VibeClient(apiBase), [apiBase]);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => installErrorBridge(), []);

  useEffect(() => {
    let alive = true;
    client
      .me()
      .then((u) => alive && setUser(u))
      .catch((e: Error) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [client]);

  return <VibeContext.Provider value={{ client, user, loading, error }}>{children}</VibeContext.Provider>;
}

/** The logged-in user. `user` is null while loading or if not logged in. */
export function useCurrentUser(): { user: User | null; loading: boolean; error: Error | null } {
  const { user, loading, error } = useVibe();
  return { user, loading, error };
}

export function useRole(): Role | null {
  return useVibe().user?.role ?? null;
}

/** Renders children only for users with at least `role`. */
export function RequireRole({ role, children, fallback = null }: { role: Role; children: ReactNode; fallback?: ReactNode }) {
  const { user, loading } = useVibe();
  if (loading) return null;
  return <>{hasRole(user, role) ? children : fallback}</>;
}

/** Everyone with access to this Vibe. Refreshes live when sharing changes in Miira. */
export function useMembers(): { members: Member[]; loading: boolean; error: Error | null; refresh: () => void } {
  const { client } = useVibe();
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(() => {
    client
      .members()
      .then((r) => {
        setMembers(r.members);
        setError(null);
      })
      .catch((e: Error) => setError(e))
      .finally(() => setLoading(false));
  }, [client]);

  useEffect(() => {
    refresh();
    return client.onMembersChanged(() => refresh());
  }, [client, refresh]);

  return { members, loading, error, refresh };
}

/** AI calls through the platform. `budget` is set when this week's AI budget is used up. */
export function useAi() {
  const { client } = useVibe();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [budget, setBudget] = useState<{ retryAt: Date | null } | null>(null);

  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | null> => {
    setLoading(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      if (e instanceof BudgetExceededError) setBudget({ retryAt: e.retryAt });
      else setError(e as Error);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const chat = useCallback(
    (messages: ChatMessage[] | string, opts?: { model?: string }) => run(() => client.chat(messages, opts)),
    [client, run],
  );
  const stream = useCallback(
    (messages: ChatMessage[] | string, onDelta: (text: string) => void, opts?: { model?: string; signal?: AbortSignal }) =>
      run(() => client.stream(messages, onDelta, opts)),
    [client, run],
  );

  return { chat, stream, loading, error, budget };
}

/** Theme helper. Import the CSS once with `import "@miira/vibe/theme.css"`. */
export const miiraTheme = {
  css: "@miira/vibe/theme.css",
  apply(theme: "light" | "dark", root: HTMLElement = document.documentElement) {
    root.dataset.theme = theme;
  },
};
