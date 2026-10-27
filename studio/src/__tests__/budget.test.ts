import { describe, expect, it } from "vitest";
import { formatTokens, meterFor } from "../lib/budget";

const bucket = (usedTokens: number, state: "ok" | "warning" | "nerfed" | "blocked", nerf: number | null = 800, hard: number | null = 1000) => ({
  usedTokens,
  nerfLimitTokens: nerf,
  hardLimitTokens: hard,
  state,
  resetsAt: "2026-11-02T00:00:00+01:00",
});

describe("meterFor", () => {
  it("is hidden when budgets are disabled or missing", () => {
    expect(meterFor(bucket(10, "ok"), false).fraction).toBeNull();
    expect(meterFor(null).showModal).toBe(false);
  });
  it("measures against the hard limit and marks the nerf limit", () => {
    const m = meterFor(bucket(500, "ok"));
    expect(m.fraction).toBe(0.5);
    expect(m.nerfMark).toBe(0.8);
    expect(m.showModal).toBe(false);
  });
  it("falls back to the nerf limit and clamps at 1", () => {
    expect(meterFor(bucket(2000, "nerfed", 1000, null)).fraction).toBe(1);
  });
  it("is unlimited when no limits are set", () => {
    expect(meterFor(bucket(5, "ok", null, null)).fraction).toBeNull();
  });
  it("shows the modal when nerfed or blocked", () => {
    expect(meterFor(bucket(900, "nerfed")).showModal).toBe(true);
    expect(meterFor(bucket(1000, "blocked")).showModal).toBe(true);
    expect(meterFor(bucket(850, "warning")).showModal).toBe(false);
  });
});

describe("formatTokens", () => {
  it("abbreviates", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(12_400)).toBe("12k");
    expect(formatTokens(2_000_000)).toBe("2 mio.");
  });
});
