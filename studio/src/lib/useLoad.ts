import { useCallback, useEffect, useState, type DependencyList } from "react";

// Waits between attempts (~30 s in all): a project's editor container may still be
// waking up when a panel first asks for its data (409 not_ready / 502).
const RETRY_MS = [1000, 2000, 3000, 4000, 5000, 5000, 5000, 5000];

/**
 * Loads `load()` whenever `deps` change, retrying failures with backoff. Keeps the
 * last good data while reloading; `failed` is set once every retry is used up.
 */
export function useLoad<T>(load: () => Promise<T>, deps: DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setFailed(false);
    const run = (n: number) =>
      load()
        .then((d) => live && setData(d))
        .catch(() => {
          if (!live) return;
          if (n >= RETRY_MS.length) setFailed(true);
          else timer = setTimeout(() => run(n + 1), RETRY_MS[n]);
        });
    run(0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);
  return { data, failed, retry };
}
