import { describe, expect, it, vi } from "vitest";
import { EmbedBridge, isAllowedOrigin, parseInbound, parseOrigins } from "../lib/embedBridge";

const MIIRA = "https://miira.dk";

function setup() {
  const win = new EventTarget() as unknown as Window;
  const parent = { postMessage: vi.fn() } as unknown as Window;
  const bridge = new EmbedBridge([MIIRA], win, parent);
  const got: unknown[] = [];
  bridge.on((m) => got.push(m));
  bridge.start();
  return { bridge, parent, got, win };
}

// jsdom's MessageEvent rejects plain objects as `source`, so build one by hand.
function makeEvent(origin: string, data: unknown, source: unknown) {
  const ev = new Event("message") as Event & { origin: string; data: unknown; source: unknown };
  Object.defineProperties(ev, { origin: { value: origin }, data: { value: data }, source: { value: source } });
  return ev;
}

describe("origin allowlist", () => {
  it("parses env and normalises trailing slashes", () => {
    expect(parseOrigins(" https://a.dk/, https://b.dk ,")).toEqual(["https://a.dk", "https://b.dk"]);
    expect(parseOrigins(undefined)).toEqual([]);
  });
  it("rejects null, unknown and look-alike origins", () => {
    expect(isAllowedOrigin(MIIRA, [MIIRA])).toBe(true);
    expect(isAllowedOrigin("null", [MIIRA])).toBe(false);
    expect(isAllowedOrigin("https://miira.dk.evil.com", [MIIRA])).toBe(false);
    expect(isAllowedOrigin("http://miira.dk", [MIIRA])).toBe(false);
  });
});

describe("parseInbound", () => {
  it("validates drive:title from Miira's top bar, trimmed to the project title limit", () => {
    expect(parseInbound({ type: "drive:title", fileId: "f", title: "  Gloser 1. A " })).toEqual({
      type: "drive:title",
      fileId: "f",
      title: "Gloser 1. A",
    });
    expect(parseInbound({ type: "drive:title", fileId: "f", title: "   " })).toBeNull();
    expect(parseInbound({ type: "drive:title", fileId: "f" })).toBeNull();
    expect(parseInbound({ type: "drive:title", title: "x" })).toBeNull();
    const long = parseInbound({ type: "drive:title", fileId: "f", title: "a".repeat(300) });
    expect(long && long.type === "drive:title" && long.title.length).toBe(120);
  });
  it("validates drive:init", () => {
    expect(parseInbound({ type: "drive:init", fileId: "f" })).toBeNull();
    expect(parseInbound({ type: "drive:init", fileId: "f", launchToken: "t", theme: "dark", locale: "xx" })).toEqual({
      type: "drive:init",
      fileId: "f",
      launchToken: "t",
      theme: "dark",
      locale: "da",
    });
    expect(parseInbound({ type: "drive:unknown" })).toBeNull();
    expect(parseInbound("drive:init")).toBeNull();
  });
});

describe("EmbedBridge", () => {
  it("announces ready to allowed origins only", () => {
    const { parent } = setup();
    expect(parent.postMessage).toHaveBeenCalledWith({ type: "drive:ready" }, MIIRA);
  });

  it("ignores messages from other origins or other windows, accepts init from Miira", () => {
    const { got, win, parent } = setup();
    const init = { type: "drive:init", fileId: "f", launchToken: "t" };
    win.dispatchEvent(makeEvent("https://evil.dk", init, parent));
    win.dispatchEvent(makeEvent(MIIRA, init, {}));
    expect(got).toHaveLength(0);
    win.dispatchEvent(makeEvent(MIIRA, init, parent));
    expect(got).toHaveLength(1);
  });

  it("only sends after init, and only to the init origin", () => {
    const { bridge, win, parent } = setup();
    expect(bridge.send({ type: "drive:close" })).toBe(false);
    win.dispatchEvent(makeEvent(MIIRA, { type: "drive:init", fileId: "f", launchToken: "t" }, parent));
    expect(bridge.send({ type: "drive:share", fileId: "f" })).toBe(true);
    expect(parent.postMessage).toHaveBeenLastCalledWith({ type: "drive:share", fileId: "f" }, MIIRA);
  });
});
