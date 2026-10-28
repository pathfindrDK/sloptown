import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLoad } from "../lib/useLoad";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useLoad", () => {
  it("retries until the container answers (files/history while it wakes up)", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("409")).mockRejectedValueOnce(new Error("502")).mockResolvedValue(["a"]);
    const { result } = renderHook(() => useLoad(load, []));
    await act(async () => {});
    expect(result.current.data).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(load).toHaveBeenCalledTimes(3);
    expect(result.current.data).toEqual(["a"]);
    expect(result.current.failed).toBe(false);
  });

  it("gives up after the retries, and retry() starts over", async () => {
    const load = vi.fn().mockRejectedValue(new Error("down"));
    const { result } = renderHook(() => useLoad(load, []));
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(result.current.failed).toBe(true);
    const calls = load.mock.calls.length;
    load.mockResolvedValue("ok");
    await act(async () => result.current.retry());
    expect(load.mock.calls.length).toBe(calls + 1);
    expect(result.current).toMatchObject({ data: "ok", failed: false });
  });

  it("keeps the last data while reloading on a dependency change", async () => {
    const load = vi.fn().mockResolvedValueOnce(1).mockReturnValueOnce(new Promise(() => {}));
    const { result, rerender } = renderHook(({ v }) => useLoad(load, [v]), { initialProps: { v: 0 } });
    await act(async () => {});
    rerender({ v: 1 });
    await act(async () => {});
    expect(result.current.data).toBe(1);
  });
});
