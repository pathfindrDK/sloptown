import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Project } from "../api/types";
import { FIRST_BUILD_MS, Preview, formatCountdown } from "../editor/Preview";
import { LocaleProvider } from "../lib/i18n";

const project = { id: "p1", status: "ready", previewUrl: "https://preview-x.example/" } as Project;

beforeEach(() => {
  // jsdom has none; the toolbar's segmented indicator observes its size.
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-29T10:00:00Z"));
  vi.spyOn(api, "previewSession").mockResolvedValue({ url: "https://preview-x.example/?s=1" } as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const show = (props: { empty: boolean; building: boolean; buildStartedAt?: number | null }) => (
  <LocaleProvider locale="da">
    <Preview project={project} reloadSignal={0} onFixError={() => {}} {...props} />
  </LocaleProvider>
);

describe("the preview during the first build", () => {
  it("formats the countdown as m:ss and stops at 0:00", () => {
    expect(formatCountdown(FIRST_BUILD_MS)).toBe("3:00");
    expect(formatCountdown(61_500)).toBe("1:02");
    expect(formatCountdown(-5)).toBe("0:00");
  });

  it("counts down from the first message, then says it is nearly there", async () => {
    const sent = Date.now() - 20_000;
    render(show({ empty: true, building: true, buildStartedAt: sent }));
    await act(async () => {});
    expect(screen.getByText(/bygger den første version/)).toBeTruthy();
    expect(screen.getByText("2:40")).toBeTruthy();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(screen.getByText("1:40")).toBeTruthy();
    await act(async () => vi.advanceTimersByTimeAsync(120_000));
    expect(screen.getByText(/længere end normalt/)).toBeTruthy();
  });

  it("shows the welcome before the first message and the app once something is built", async () => {
    const view = render(show({ empty: true, building: false }));
    await act(async () => {});
    expect(screen.getByText(/Lad os komme i gang/)).toBeTruthy();
    view.rerender(show({ empty: true, building: true }));
    expect(screen.getByText("3:00")).toBeTruthy();
    // turn.finished arrives before the reloaded history: no flash of the welcome.
    view.rerender(show({ empty: true, building: false }));
    expect(screen.queryByText(/Lad os komme i gang/)).toBeNull();
    view.rerender(show({ empty: false, building: false }));
    expect(screen.queryByText(/bygger den første version/)).toBeNull();
    expect(screen.queryByText(/Lad os komme i gang/)).toBeNull();
  });

  it("goes back to the welcome if the turn ended without building anything", async () => {
    const view = render(show({ empty: true, building: true }));
    await act(async () => {});
    view.rerender(show({ empty: true, building: false }));
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(screen.getByText(/Lad os komme i gang/)).toBeTruthy();
  });
});
